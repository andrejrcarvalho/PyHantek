"""Main application window."""

from __future__ import annotations

import csv
import math
import time
from collections import deque
from concurrent.futures import Future

import numpy as np
from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QGuiApplication, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..acquisition import Acquisition, Frame
from ..device import DemoDevice
from ..decoders import DecodeResult, run_decoder
from ..mathexpr import MathError, compile_expr, evaluate, label_for, unit_for
from ..processing import measure, minmax_decimate, si_format, sinc_interpolate, spectrum
from ..settings import CH_NAMES, DIVS_X, DIVS_Y, TIMEBASES, VDIVS, Settings
from . import theme
from .controls import ControlPanel
from .extra_views import SpectrogramView, ZoomView
from .scope_view import ScopeView, SpectrumView

MEAS_COLUMNS = [
    ("Frequency", "freq", "Hz"),
    ("Period", "period", "s"),
    ("Vpp", "vpp", "V"),
    ("Max", "vmax", "V"),
    ("Min", "vmin", "V"),
    ("Mean", "mean", "V"),
    ("RMS", "rms", "V"),
    ("AC RMS", "ac_rms", "V"),
    ("Duty", "duty", "%"),
    ("Rise", "rise", "s"),
    ("Fall", "fall", "s"),
]


class Banner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent, objectName="Banner")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 6, 8, 6)
        self.text = QLabel()
        self.text.setWordWrap(True)
        lay.addWidget(self.text, 1)
        self.button = QPushButton()
        self.button.clicked.connect(lambda: self._callback and self._callback())
        self._callback = None
        lay.addWidget(self.button)
        self.hide()

    def show_message(self, text, button=None, callback=None):
        self.text.setText(text)
        self._callback = callback
        if button:
            self.button.setText(button)
            self.button.show()
        else:
            self.button.hide()
        self.show()


class MainWindow(QMainWindow):
    def __init__(self, device, reconnect=None, mcp_host="127.0.0.1", mcp_port=None, mcp_enabled=None):
        super().__init__()
        self.reconnect_cb = reconnect
        self.settings_gen = 0
        self._frame_waiters: list[tuple[int, Future]] = []
        self.mcp = None
        self.store = QSettings()  # organisation/application set in app.main (tests use their own)
        self.settings = Settings.from_json(self.store.value("settings", ""))
        self.acq: Acquisition | None = None
        self.last_frame: Frame | None = None
        self.last_view = None
        self.avg_buf: deque = deque()
        self.avg_key = None
        self.frame_times: deque = deque(maxlen=30)
        self.autoset_stage = 0
        self.device = device
        self.last_decode: DecodeResult | None = None
        self.math_error = ""
        self._math_cache = (None, None)

        self.setWindowTitle("PyHantek")
        self.resize(1360, 860)

        self._build_ui(device.model)
        self.attach_device(device)

        geo = self.store.value("geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        state = self.store.value("splitter")
        if state is not None:
            self.splitter.restoreState(state)
        self._settings_changed()
        self._init_mcp(mcp_host, mcp_port, mcp_enabled)

    # ------------------------------------------------------------ MCP

    def _init_mcp(self, host, port, enabled):
        from ..mcp_server import DEFAULT_PORT, McpServer

        port = int(port or self.store.value("mcp_port", DEFAULT_PORT))
        if enabled is None:
            enabled = str(self.store.value("mcp_enabled", "true")).lower() == "true"
        self.mcp = McpServer(self, host, port)
        self.mcp_action.setChecked(enabled)
        self._toggle_mcp(enabled)

    def _toggle_mcp(self, on):
        if self.mcp is None:
            return
        if on:
            ok = self.mcp.start()
            if not ok:
                self.mcp_action.blockSignals(True)
                self.mcp_action.setChecked(False)
                self.mcp_action.blockSignals(False)
                self.st_mcp.setText(f"MCP off: {self.mcp.error}")
                self.st_mcp.setStyleSheet(f"color:{theme.BAD};")
                return
            self.st_mcp.setText(f"MCP ● {self.mcp.url}")
            self.st_mcp.setStyleSheet(f"color:{theme.GOOD};")
            self.st_mcp.setToolTip(f"AI assistants can control the scope.\nClaude Code: claude mcp add --transport http pyhantek {self.mcp.url}")
        else:
            self.mcp.stop()
            self.st_mcp.setText("MCP off")
            self.st_mcp.setStyleSheet(f"color:{theme.MUTED};")
        self.store.setValue("mcp_enabled", "true" if on else "false")

    def request_frame(self) -> Future:
        """Future resolved when a frame captured with the current settings has been drawn (GUI thread)."""
        fut: Future = Future()
        self._frame_waiters.append((self.settings_gen, fut))
        return fut

    def _resolve_waiters(self, frame):
        keep = []
        for gen, fut in self._frame_waiters:
            if fut.cancelled():
                continue
            if frame.generation >= gen:
                fut.set_result(True)
            else:
                keep.append((gen, fut))
        self._frame_waiters = keep

    # ------------------------------------------------------------ UI

    def _build_ui(self, model):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)

        title = QLabel("  PyHantek")
        title.setStyleSheet("font-size:15px; font-weight:800; letter-spacing:0.5px;")
        tb.addWidget(title)
        self.device_chip = QLabel()
        self.device_chip.setStyleSheet(f"color:{theme.MUTED}; padding: 0 12px;")
        tb.addWidget(self.device_chip)
        from PySide6.QtWidgets import QComboBox

        self.demo_combo = QComboBox()
        self.demo_combo.addItems(["Demo: sine + square", "Demo: UART", "Demo: I²C", "Demo: SPI", "Demo: Lissajous"])
        self.demo_combo.setToolTip("Simulated test signals (only with the demo device)")
        self.demo_combo.currentIndexChanged.connect(lambda i: self.set_demo_signal(DemoDevice.SIGNALS[i]))
        self.demo_combo_action = tb.addWidget(self.demo_combo)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        self.run_btn = QPushButton("■  STOP", objectName="Run")
        self.run_btn.setToolTip("Run / stop acquisition (Space)")
        self.run_btn.clicked.connect(self.toggle_run)
        tb.addWidget(self.run_btn)
        single = QPushButton("Single")
        single.setToolTip("Capture one triggered trace (S)")
        single.clicked.connect(self.single_shot)
        tb.addWidget(single)
        autoset = QPushButton("Autoset")
        autoset.setToolTip("Find a good scale, timebase and trigger level (A)")
        autoset.clicked.connect(self.autoset)
        tb.addWidget(autoset)
        tb.addSeparator()

        def action(text, slot, shortcut=None, tip=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.setToolTip(tip or text)
            a.triggered.connect(slot)
            tb.addAction(a)
            return a

        action("Screenshot", self.screenshot, "Ctrl+P", "Save a PNG of the screen (Ctrl+P)")
        action("Export CSV", self.export_csv, "Ctrl+E", "Export the visible traces (Ctrl+E)")
        action("Calibrate", self.calibrate, None, "Measure the 0 V offsets with grounded inputs")
        action("Reset", self.reset_settings, None, "Restore default settings")
        self.mcp_action = action("MCP", lambda: None, None, "Let AI assistants control the scope over MCP (localhost only)")
        self.mcp_action.setCheckable(True)
        self.mcp_action.toggled.connect(self._toggle_mcp)
        action("About", self.about)

        central = QWidget(objectName="Root")
        vl = QVBoxLayout(central)
        vl.setContentsMargins(10, 10, 0, 10)
        vl.setSpacing(8)
        self.banner = Banner()
        vl.addWidget(self.banner)

        self.info_bar = QLabel()
        self.info_bar.setTextFormat(Qt.RichText)
        self.info_bar.setStyleSheet("padding: 0 4px; font-family: monospace;")
        vl.addWidget(self.info_bar)
        self.scope = ScopeView()
        self.zoom = ZoomView()
        self.spectrum = SpectrumView()
        self.spectrogram = SpectrogramView()
        self.table = self._build_table()
        self.decoded = self._build_decoded_table()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.table, "Measurements")
        self.tabs.addTab(self.decoded, "Decoded")
        self.tabs.setFixedHeight(24 * 3 + 30 + 34)
        self.splitter = QSplitter(Qt.Vertical)
        for i, w in enumerate((self.scope, self.zoom, self.spectrum, self.spectrogram, self.tabs)):
            self.splitter.addWidget(w)
            self.splitter.setStretchFactor(i, (5, 3, 2, 2, 0)[i])
        self.splitter.setChildrenCollapsible(False)
        vl.addWidget(self.splitter, 1)

        body = QWidget()
        hl = QHBoxLayout(body)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setSpacing(0)
        hl.addWidget(central, 1)
        self.panel = ControlPanel(self.settings, model)
        hl.addWidget(self.panel)
        self.setCentralWidget(body)

        sb = self.statusBar()
        self.st_state = QLabel()
        self.st_rate = QLabel()
        self.st_fps = QLabel()
        self.st_mcp = QLabel()
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(160)
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.hide()
        sb.addWidget(self.st_state)
        sb.addWidget(self.st_rate)
        sb.addPermanentWidget(self.progress)
        sb.addPermanentWidget(self.st_mcp)
        sb.addPermanentWidget(self.st_fps)

        self.panel.changed.connect(self._settings_changed)
        self.panel.level_to_50.connect(self.level_to_50)
        self.panel.clear_persistence.connect(lambda: self.scope.phosphor.clear())
        self.scope.zoom_moved.connect(self._on_zoom_moved)
        self.scope.trigger_level_moved.connect(self._on_level_dragged)
        self.scope.trigger_position_moved.connect(self._on_position_dragged)
        self.scope.offset_moved.connect(self._on_offset_dragged)
        self.scope.wheel_steps.connect(self._on_wheel)

        for key, slot in (
            ("Space", self.toggle_run),
            ("S", self.single_shot),
            ("A", self.autoset),
            ("Right", lambda: self.panel.timebase.step(1)),
            ("Left", lambda: self.panel.timebase.step(-1)),
            ("Up", lambda: self._step_vdiv(-1)),
            ("Down", lambda: self._step_vdiv(1)),
            ("C", lambda: self.panel.cursors.toggle()),
        ):
            a = QAction(self)
            a.setShortcut(QKeySequence(key))
            a.setShortcutContext(Qt.WindowShortcut)
            a.triggered.connect(slot)
            self.addAction(a)

        self._fps_timer = QTimer(self, interval=500, timeout=self._update_fps)
        self._fps_timer.start()

    def _build_table(self):
        t = QTableWidget(3, len(MEAS_COLUMNS))
        t.setHorizontalHeaderLabels([c[0] for c in MEAS_COLUMNS])
        t.setVerticalHeaderLabels(["CH1", "CH2", "Math"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        t.verticalHeader().setSectionResizeMode(QHeaderView.Fixed)
        t.verticalHeader().setDefaultSectionSize(24)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionMode(QAbstractItemView.NoSelection)
        t.setFocusPolicy(Qt.NoFocus)
        t.setShowGrid(False)
        for r in range(3):
            for c in range(len(MEAS_COLUMNS)):
                item = QTableWidgetItem("—")
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                item.setForeground(Qt.white)
                t.setItem(r, c, item)
            hdr = QTableWidgetItem(["CH1", "CH2", "Math"][r])
            hdr.setForeground(pg_color(theme.CH_COLORS[r]))
            t.setVerticalHeaderItem(r, hdr)
        return t

    def _build_decoded_table(self):
        t = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["Time", "Type", "Value", "Info"])
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        t.horizontalHeader().setStretchLastSection(True)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(22)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setShowGrid(False)
        return t

    # ------------------------------------------------------------ device

    def attach_device(self, device):
        if self.acq is not None:
            self.acq.shutdown()
        self.device = device
        self.panel.model = device.model
        fw = f" · FW {device.firmware_version:04x}" if not device.is_demo else ""
        serial = f" · S/N {device.serial}" if not device.is_demo and device.serial != "unknown" else ""
        self.device_chip.setText(f"●  {device.name}{serial}{fw}")
        self.device_chip.setStyleSheet(f"color:{theme.WARN if device.is_demo else theme.GOOD}; padding: 0 12px;")
        self.acq = Acquisition(device, self.settings)
        self.acq.update_settings(self.settings, self.settings_gen)
        self.acq.frame_ready.connect(self._on_frame)
        self.acq.status.connect(self._on_status)
        self.acq.progress.connect(self._on_progress)
        self.acq.running_changed.connect(self._on_running)
        self.acq.rate_changed.connect(lambda r: self.panel.set_actual_rate(r))
        self.acq.device_lost.connect(self._on_device_lost)
        self.acq.calibration_done.connect(self._on_calibration_done)
        self.acq.start()
        self._on_running(True)
        self.demo_combo_action.setVisible(device.is_demo)
        if device.is_demo:
            self.banner.show_message(
                "No Hantek scope found — showing simulated signals. Plug in a DSO-6022BE/BL, DSO-2020 or DSO-6021 and press Connect.",
                "Connect",
                self._reconnect,
            )
        else:
            self.banner.hide()
            if not self.acq.cal.from_eeprom:
                self.statusBar().showMessage("No calibration in EEPROM — consider running Calibrate", 8000)

    def set_demo_signal(self, name: str):
        if not self.device.is_demo:
            return
        self.device.signal = name
        i = DemoDevice.SIGNALS.index(name)
        if self.demo_combo.currentIndex() != i:
            self.demo_combo.blockSignals(True)
            self.demo_combo.setCurrentIndex(i)
            self.demo_combo.blockSignals(False)
        self.scope.phosphor.clear()
        self.spectrogram.clear()
        self.settings_gen += 1  # frames from the old signal are stale
        if self.acq:
            self.acq.update_settings(self.settings, self.settings_gen)

    def _reconnect(self):
        if self.reconnect_cb is None:
            return
        dev = self.reconnect_cb(self)
        if dev is not None:
            self.attach_device(dev)

    def _on_device_lost(self, msg):
        self.banner.show_message(f"Scope disconnected ({msg}).", "Reconnect", self._reconnect)
        self.scope.set_badge("DISCONNECTED", theme.BAD)

    # ------------------------------------------------------------ settings

    def _settings_changed(self):
        s = self.settings
        prev_count = getattr(self, "_channel_count", None)
        if prev_count != s.channel_count():
            self._channel_count = s.channel_count()
            self.panel._fill_rates()
        self.scope.apply_settings(s)
        self.spectrum.setVisible(s.spectrum_enabled and s.spectrum_view in ("Spectrum", "Both"))
        self.spectrogram.setVisible(s.spectrum_enabled and s.spectrum_view in ("Spectrogram", "Both"))
        self.zoom.setVisible(s.zoom_enabled and not s.xy_mode)
        if s.zoom_enabled:
            self.zoom.set_region(s.zoom_start, s.zoom_end, s.timebase)
        self.scope.phosphor.set_visible(s.persistence != 0)
        self.scope.phosphor.clear()
        if not s.decoder.enabled:
            self.zoom.bus.clear()
            self.last_decode = None
        self.settings_gen += 1
        if self.acq:
            self.acq.update_settings(s, self.settings_gen)
        self._redraw()

    def _on_level_dragged(self, v):
        self.settings.trigger.level = round(v, 4)
        self.panel.trig_level.blockSignals(True)
        self.panel.trig_level.setValue(v)
        self.panel.trig_level.blockSignals(False)
        self._settings_changed()

    def _on_zoom_moved(self, a, b):
        self.settings.zoom_start, self.settings.zoom_end = round(a, 3), round(b, 3)
        self.zoom.set_region(a, b, self.settings.timebase)
        self._redraw()

    def _on_position_dragged(self, p):
        self.settings.trigger.position = p
        self._settings_changed()

    def _on_offset_dragged(self, i, v):
        if i < 2:
            self.settings.channels[i].offset = round(v, 2)
        else:
            self.settings.math_offset = round(v, 2)
        self.panel.refresh()
        self._settings_changed()

    def _on_wheel(self, steps, mods):
        if mods & Qt.ShiftModifier:
            self._step_vdiv(-steps)
        else:
            self.panel.timebase.step(-steps)

    def _step_vdiv(self, d):
        card = self.panel.ch_cards[self.settings.trigger.source]
        card.vdiv.step(d)

    def level_to_50(self):
        f = self.last_frame
        src = self.settings.trigger.source
        if f is None or f.data[src] is None:
            return
        d = f.data[src]
        self.panel.trig_level.setValue(round(float((np.nanmax(d) + np.nanmin(d)) / 2), 3))

    def reset_settings(self):
        fresh = Settings()
        self.settings.__dict__.update(fresh.__dict__)
        self.panel.refresh()
        self._settings_changed()

    # ------------------------------------------------------------ run control

    def toggle_run(self):
        if self.acq:
            if not self.acq.running and self.settings.trigger.mode == "Single":
                self.avg_buf.clear()
            self.acq.set_running(not self.acq.running)

    def single_shot(self):
        if self.settings.trigger.mode != "Single":
            self.settings.trigger.mode = "Single"
            self.panel.refresh()
            self._settings_changed()
        if self.acq:
            self.acq.set_running(False)
            self.acq.set_running(True)
        self.scope.set_badge("ARMED", theme.WARN)

    def _on_running(self, running):
        self.run_btn.setText("■  STOP" if running else "▶  RUN")
        self.run_btn.setProperty("running", "true" if running else "false")
        self.run_btn.style().unpolish(self.run_btn)
        self.run_btn.style().polish(self.run_btn)
        if not running:
            self.scope.set_badge("STOP", theme.BAD)
            self.st_state.setText("Stopped")

    def _on_status(self, text):
        self.st_state.setText(text)
        if text.startswith("Waiting"):
            self.scope.set_badge("WAIT", theme.WARN)

    def _on_progress(self, p):
        if p < 0:
            self.progress.hide()
        else:
            self.progress.show()
            self.progress.setValue(int(p * 1000))

    def _update_fps(self):
        ft = self.frame_times
        if len(ft) >= 2 and time.monotonic() - ft[-1] < 2:
            fps = (len(ft) - 1) / (ft[-1] - ft[0])
            self.st_fps.setText(f"{fps:.1f} fps")
        else:
            self.st_fps.setText("")

    # ------------------------------------------------------------ frames

    def _on_frame(self, frame: Frame):
        # Coalesce: if drawing is slower than acquisition, only draw the newest frame
        self._pending_frame = frame
        if not getattr(self, "_frame_scheduled", False):
            self._frame_scheduled = True
            QTimer.singleShot(0, self._draw_pending)

    def _draw_pending(self):
        self._frame_scheduled = False
        frame = getattr(self, "_pending_frame", None)
        self._pending_frame = None
        if frame is None:
            return
        self.frame_times.append(time.monotonic())
        self.last_frame = frame
        if frame.roll:
            self.scope.set_badge("ROLL", theme.ACCENT)
        elif frame.triggered:
            self.scope.set_badge("TRIG'D", theme.GOOD)
        else:
            self.scope.set_badge("AUTO", theme.WARN)
        self._redraw(new_frame=True)
        self._resolve_waiters(frame)
        if self.autoset_stage:
            self._autoset_step(frame)

    def _window(self, frame: Frame, n: int):
        """Sample index range and x positions (in divisions) for the visible screen."""
        s = self.settings
        sr = frame.samplerate
        if frame.roll:
            return 0, n, np.linspace(0, DIVS_X, n, endpoint=False)
        pre = s.trigger.position * DIVS_X * s.timebase * sr
        span = DIVS_X * s.timebase * sr
        i0 = max(0, int(math.floor(frame.trigger_index - pre)) - 1)
        i1 = min(n, int(math.ceil(frame.trigger_index - pre + span)) + 2)
        x = ((np.arange(i0, i1) - frame.trigger_index) / sr / s.timebase) + s.trigger.position * DIVS_X
        return i0, i1, x

    def _redraw(self, new_frame=False):
        f = self.last_frame
        s = self.settings
        if f is None:
            return
        lens = [len(d) for d in f.data if d is not None]
        if not lens:
            self.scope.set_traces([None, None, None])
            return
        n = min(lens)
        i0, i1, x = self._window(f, n)
        visible = [d[i0:i1] if d is not None else None for d in f.data]

        # averaging of consecutive triggered traces
        if s.averaging > 1 and f.triggered and not f.roll:
            key = (i1 - i0, f.samplerate, s.timebase, s.trigger.position, tuple(d is None for d in visible))
            if key != self.avg_key:
                self.avg_buf.clear()
                self.avg_key = key
            if new_frame:
                self.avg_buf.append(visible)
                while len(self.avg_buf) > s.averaging:
                    self.avg_buf.popleft()
            visible = [
                np.mean([b[ch] for b in self.avg_buf], axis=0) if visible[ch] is not None and self.avg_buf else visible[ch]
                for ch in range(2)
            ]
        else:
            self.avg_buf.clear()

        math_v = None
        if s.math_enabled:
            t_rel = (x - s.trigger.position * DIVS_X) * s.timebase
            math_v = self.compute_math(visible[0], visible[1], f.samplerate, t_rel)

        volts = [visible[0] if s.channels[0].enabled else None, visible[1] if s.channels[1].enabled else None, math_v]
        traces = []
        full_traces = []
        dots = s.interpolation == "Dots"
        for i, v in enumerate(volts):
            if s.xy_mode:
                traces.append(None)
                full_traces.append(None)
                continue
            if v is None or len(v) < 2:
                traces.append(None)
                full_traces.append(None)
                continue
            vdiv = s.channels[i].vdiv if i < 2 else s.math_vdiv
            off = s.channels[i].offset if i < 2 else s.math_offset
            xx, vv = x[: len(v)], v
            if s.interpolation == "Sin(x)/x" and len(v) < 400 and np.all(np.isfinite(v)):
                factor = int(math.ceil(800 / len(v)))
                vv = sinc_interpolate(v, factor)
                xx = x[0] + np.arange(len(vv)) * (x[1] - x[0]) / factor
            y = np.clip(vv / vdiv + off, -DIVS_Y / 2 - 0.02, DIVS_Y / 2 + 0.02)
            full_traces.append((xx, y))
            if not dots:
                xx, y = minmax_decimate(xx, y, 1500)
            traces.append((xx, y))
        self.scope.set_traces(traces, dots=dots)
        self.last_view = (x, volts, f.samplerate)

        # XY mode: CH1 horizontal, CH2 vertical, using the on-screen time window
        xy = None
        if s.xy_mode and visible[0] is not None and visible[1] is not None:
            on = (x >= 0) & (x <= DIVS_X)
            a, b = visible[0][on[: len(visible[0])]], visible[1][on[: len(visible[1])]]
            k = min(len(a), len(b))
            xy = (np.clip(DIVS_X / 2 + a[:k] / s.channels[0].vdiv + s.channels[0].offset, -0.02, DIVS_X + 0.02),
                  np.clip(b[:k] / s.channels[1].vdiv + s.channels[1].offset, -DIVS_Y / 2 - 0.02, DIVS_Y / 2 + 0.02))
        self.scope.set_xy(xy)

        if new_frame and s.persistence != 0:
            self.scope.phosphor.add([xy] if s.xy_mode else full_traces, s.persistence)
        if s.zoom_enabled and not s.xy_mode:
            self.zoom.set_traces(full_traces)
        if s.decoder.enabled and not s.xy_mode:
            self._decode(f, new_frame)

        # info line
        def chip(color, text, warn=False):
            border = theme.BAD if warn else theme.BORDER
            return (f'<span style="color:{color}; background:{theme.SURFACE}; border:1px solid {border};">'
                    f"&nbsp;{text}&nbsp;</span>")

        parts = []
        for i in range(2):
            ch = s.channels[i]
            if ch.enabled:
                clip = " ⚠ CLIPPING" if f.clipped[i] else ""
                inv = " INV" if ch.invert else ""
                parts.append(chip(theme.CH_COLORS[i], f"{CH_NAMES[i]} {si_format(ch.vdiv, 'V')}/div {ch.coupling}{inv}{clip}", bool(clip)))
        if s.math_enabled:
            if self.math_error:
                parts.append(chip(theme.BAD, f"M error: {self.math_error}", True))
            else:
                unit = unit_for(s.math_expr) or "u"
                parts.append(chip(theme.CH_COLORS[2], f"M {label_for(s.math_expr)} {si_format(s.math_vdiv, unit)}/div"))
        if s.xy_mode:
            parts.append(chip("#4ade80", "XY"))
        if s.persistence:
            parts.append(chip(theme.MUTED, "Persist " + ("∞" if s.persistence < 0 else f"{s.persistence:g} s")))
        parts.append(chip(theme.TEXT, f"H {si_format(s.timebase, 's')}/div · {si_format(f.samplerate, 'S/s')}"))
        if s.trigger.mode != "Roll":
            arrow = "↗" if s.trigger.slope == "Rising" else "↘"
            tcol = theme.CH_COLORS[s.trigger.source]
            parts.append(chip(tcol, f"T {s.trigger.mode} {CH_NAMES[s.trigger.source]} {arrow} {si_format(s.trigger.level, 'V')}"))
        else:
            parts.append(chip(theme.ACCENT, "Roll"))
        self.info_bar.setText("&nbsp;&nbsp;".join(parts))
        self.st_rate.setText(f"{si_format(f.samplerate, 'S/s')} · {n:,} samples")
        if new_frame:
            self.panel.set_actual_rate(f.samplerate, n)

        # measurements on what's on screen
        on_screen = (x >= 0) & (x <= DIVS_X)
        for r, v in enumerate(volts):
            m = measure(v[on_screen[: len(v)]], f.samplerate, f.lsb[r] if r < 2 else 0.0) if v is not None else None
            for c, (_, attr, unit) in enumerate(MEAS_COLUMNS):
                item = self.table.item(r, c)
                if m is None:
                    item.setText("—")
                    continue
                val = getattr(m, attr)
                if unit == "%":
                    item.setText(f"{val * 100:.1f} %" if math.isfinite(val) else "—")
                else:
                    u = ((unit_for(s.math_expr) or "u") if unit == "V" else unit) if r == 2 else unit
                    item.setText(si_format(val, u))
        if f.clipped[0] or f.clipped[1]:
            for r in range(2):
                for c in range(2, 6):
                    self.table.item(r, c).setForeground(pg_color(theme.BAD if f.clipped[r] else "#ffffff"))
        else:
            for r in range(2):
                for c in range(2, 6):
                    self.table.item(r, c).setForeground(pg_color("#ffffff"))

        # spectrum on the full record for best resolution
        if s.spectrum_enabled and new_frame:
            full = list(f.data)
            full.append(self.compute_math(full[0], full[1], f.samplerate) if s.math_enabled else None)
            if s.spectrum_view in ("Spectrum", "Both"):
                spectra = []
                for i in range(3):
                    on = s.spectrum_channels[i] and (s.channels[i].enabled if i < 2 else s.math_enabled)
                    spectra.append(spectrum(full[i], f.samplerate, s.spectrum_window) if on and full[i] is not None else None)
                self.spectrum.set_spectra(spectra, f.samplerate / 2)
            if s.spectrum_view in ("Spectrogram", "Both"):
                i = s.spectrogram_channel
                if full[i] is not None:
                    fr, db = spectrum(full[i], f.samplerate, s.spectrum_window)
                    if len(fr) > 2:
                        name = ["CH1", "CH2", "Math"][i]
                        self.spectrogram.add(fr[1:], db[1:], f.samplerate / 2, f"Waterfall · {name}", theme.CH_COLORS[i])

    # ------------------------------------------------------------ math & decoding

    def compute_math(self, a, b, sr, t=None):
        """Evaluate the math formula; returns None (and records the error) when it cannot be computed."""
        expr = self.settings.math_expr
        cached_expr, compiled = self._math_cache
        try:
            if cached_expr != expr:
                compiled = compile_expr(expr)
                self._math_cache = (expr, compiled)
            out = evaluate(compiled, a, b, sr, t)
            self.math_error = ""
            return out
        except MathError as exc:
            self.math_error = str(exc)
            return None

    def _decode(self, f: Frame, new_frame: bool):
        s = self.settings
        if new_frame or self.last_decode is None:
            self.last_decode = run_decoder(s.decoder, f.data, f.samplerate)
            self._fill_decoded(f)
        res = self.last_decode
        sr = f.samplerate
        n = max((len(d) for d in f.data if d is not None), default=1)

        def to_x(i):
            if f.roll:
                return i / n * DIVS_X
            return (i - f.trigger_index) / sr / s.timebase + s.trigger.position * DIVS_X

        items = [(to_x(a.start), to_x(a.end), a.kind, a.text) for a in res.annotations]
        proto = {"I2C": "I²C"}.get(s.decoder.protocol, s.decoder.protocol)
        extra = res.message or (f"{res.params['baud']:g} baud" if "baud" in res.params else "")
        title = f"{proto}" + (f" · {extra}" if extra else "") + f" · {len(res.annotations)} items"
        self.scope.bus.set_annotations(items, title, self.scope.px_per_div())
        if s.zoom_enabled:
            a, b = s.zoom_start, s.zoom_end
            self.zoom.bus.set_annotations(items, proto, self.zoom.vb.width() / max(b - a, 1e-9), (a, b))

    def _fill_decoded(self, f: Frame):
        res = self.last_decode
        t = self.decoded
        rows = res.annotations[:1000]
        t.setRowCount(len(rows) if rows else 1)
        if not rows:
            t.setItem(0, 0, QTableWidgetItem(""))
            t.setItem(0, 1, QTableWidgetItem(""))
            t.setItem(0, 2, QTableWidgetItem(res.message or "nothing decoded"))
            t.setItem(0, 3, QTableWidgetItem(""))
            return
        sr = f.samplerate
        from .extra_views import KIND_COLORS

        for r, a in enumerate(rows):
            tt = (a.start - f.trigger_index) / sr if not f.roll else a.start / sr
            cells = [si_format(tt, "s"), a.kind, a.text, a.info]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if c == 2:
                    item.setForeground(pg_color(KIND_COLORS.get(a.kind, "#ffffff")))
                t.setItem(r, c, item)

    # ------------------------------------------------------------ autoset

    def autoset(self):
        """Two passes: coarse capture at 5 V/div, then refine scale, timebase and trigger."""
        s = self.settings
        for ch in s.channels:
            ch.vdiv = 5.0 * ch.probe
            ch.coupling = ch.coupling
        s.timebase = 1e-3
        s.samplerate = 0.0
        if s.trigger.mode in ("Roll", "Single"):
            s.trigger.mode = "Auto"
        self.panel.refresh()
        self._settings_changed()
        if self.acq and not self.acq.running:
            self.acq.set_running(True)
        self.autoset_stage = 1
        self._autoset_gen = self.settings_gen  # ignore frames captured with older settings
        self.statusBar().showMessage("Autoset…", 3000)

    def _autoset_step(self, frame: Frame):
        if frame.generation < self._autoset_gen:
            return
        s = self.settings
        best_freq = None
        both = s.channels[0].enabled and s.channels[1].enabled
        for i, ch in enumerate(s.channels):
            d = frame.data[i]
            if not ch.enabled or d is None:
                continue
            m = measure(d, frame.samplerate, frame.lsb[i])
            # each channel gets the full screen, or half of it when both are shown
            room = 3.2 if both else 6.0
            if self.autoset_stage == 1:
                target = 2 * max(abs(m.vmax), abs(m.vmin)) / room
            else:
                target = m.vpp / room
            choices = [v * ch.probe for v in VDIVS]
            ch.vdiv = next((v for v in choices if v >= target), choices[-1])
            center = (2.0 if i == 0 else -2.0) if both else 0.0
            ch.offset = center - (m.mean / ch.vdiv if self.autoset_stage == 2 else 0.0)
            ch.offset = round(max(-3.8, min(3.8, ch.offset)), 2)
            if math.isfinite(m.freq) and (i == s.trigger.source or best_freq is None):
                best_freq = m.freq
            if i == s.trigger.source:
                s.trigger.level = round((m.vmax + m.vmin) / 2, 3)
        if self.autoset_stage == 1 and best_freq:
            want = 3.0 / best_freq / DIVS_X  # about three periods on screen
            s.timebase = next((t for t in TIMEBASES if t >= want), TIMEBASES[-1])
        s.trigger.position = 0.5
        if self.autoset_stage == 1:
            self.autoset_stage = 2
        else:
            self.autoset_stage = 0
            self.statusBar().showMessage("Autoset done", 2000)
        self.panel.refresh()
        self._settings_changed()
        self._autoset_gen = self.settings_gen

    # ------------------------------------------------------------ export

    def screenshot(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save screenshot", f"scope_{time.strftime('%Y%m%d_%H%M%S')}.png", "PNG image (*.png)")
        if not path:
            return
        img = self.splitter.grab()
        img.save(path)
        QGuiApplication.clipboard().setPixmap(img)
        self.statusBar().showMessage(f"Saved {path} (also copied to clipboard)", 5000)

    def export_csv(self):
        if self.last_view is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Export CSV", f"scope_{time.strftime('%Y%m%d_%H%M%S')}.csv", "CSV (*.csv)")
        if not path:
            return
        x, volts, sr = self.last_view
        s = self.settings
        t = (x - s.trigger.position * DIVS_X) * s.timebase
        names = ["CH1 (V)", "CH2 (V)", "Math"]
        cols = [(names[i], v) for i, v in enumerate(volts) if v is not None]
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["time (s)"] + [c[0] for c in cols])
            for k in range(len(t)):
                w.writerow([f"{t[k]:.9g}"] + [f"{c[1][k]:.6g}" if k < len(c[1]) else "" for c in cols])
        self.statusBar().showMessage(f"Exported {len(t)} samples to {path}", 5000)

    # ------------------------------------------------------------ calibration

    def calibrate(self):
        if self.acq is None:
            return
        r = QMessageBox.question(
            self,
            "Calibrate offsets",
            "Connect both probes to ground (or short the inputs), then press OK.\n\n"
            "The 0 V level is measured for every gain range and stored in the OpenHantek "
            "calibration file for this scope. The EEPROM is not modified.",
            QMessageBox.Ok | QMessageBox.Cancel,
        )
        if r == QMessageBox.Ok:
            self.acq.calibrate_offsets()

    def _on_calibration_done(self, ok, msg):
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Calibration", msg)

    def about(self):
        QMessageBox.about(
            self,
            "About PyHantek",
            f"<h3>PyHantek {__version__}</h3>"
            "<p>A Python/Qt oscilloscope for Hantek DSO-6022BE/BL, Voltcraft DSO-2020 and DSO-6021.</p>"
            "<p>Protocol, calibration and firmware from OpenHantek6022 and Hantek6022API.</p>"
            "<p><b>Shortcuts:</b> Space run/stop · S single · A autoset · ←/→ timebase · ↑/↓ V/div · "
            "C cursors · mouse wheel = timebase, Shift+wheel = V/div</p>",
        )

    # ------------------------------------------------------------ shutdown

    def closeEvent(self, ev):
        self.store.setValue("settings", self.settings.to_json())
        self.store.setValue("geometry", self.saveGeometry())
        self.store.setValue("splitter", self.splitter.saveState())
        if self.mcp:
            self.mcp.stop()
        if self.acq:
            self.acq.shutdown()
        super().closeEvent(ev)


def pg_color(c):
    from PySide6.QtGui import QColor

    return QColor(c)
