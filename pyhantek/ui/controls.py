"""Side panel with all scope controls, grouped into cards."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..mathexpr import FUNCTIONS_HELP, PRESETS, MathError, compile_expr, label_for

from ..models import ScopeModel
from ..processing import si_format
from ..settings import (
    DECODE_FORMATS,
    INTERPOLATIONS,
    NOISE_REJECT,
    PERSISTENCE,
    PROTOCOLS,
    SPECTRUM_VIEWS,
    PROBES,
    TIMEBASES,
    TRIGGER_MODES,
    VDIVS,
    WINDOWS,
    Settings,
)
from . import theme


class Stepper(QWidget):
    """◀ [value] ▶ selector for 1-2-5 sequences."""

    changed = Signal(float)

    def __init__(self, values, fmt, parent=None):
        super().__init__(parent)
        self.fmt = fmt
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.minus = QPushButton("−", objectName="Step")
        self.plus = QPushButton("+", objectName="Step")
        self.combo = QComboBox()
        self.combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        lay.addWidget(self.minus)
        lay.addWidget(self.combo, 1)
        lay.addWidget(self.plus)
        self.set_values(values)
        self.minus.clicked.connect(lambda: self.step(-1))
        self.plus.clicked.connect(lambda: self.step(1))
        self.combo.currentIndexChanged.connect(lambda i: i >= 0 and self.changed.emit(self.values[i]))

    def set_values(self, values):
        cur = self.value() if getattr(self, "values", None) else None
        self.values = list(values)
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems([self.fmt(v) for v in self.values])
        self.combo.blockSignals(False)
        if cur is not None:
            self.set_value(cur)

    def value(self) -> float:
        i = self.combo.currentIndex()
        return self.values[i] if i >= 0 else self.values[0]

    def set_value(self, v: float) -> None:
        i = min(range(len(self.values)), key=lambda k: abs(self.values[k] - v) / max(abs(v), 1e-12))
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(i)
        self.combo.blockSignals(False)

    def step(self, d: int) -> None:
        i = max(0, min(len(self.values) - 1, self.combo.currentIndex() + d))
        self.combo.setCurrentIndex(i)


class Segmented(QWidget):
    changed = Signal(int)

    def __init__(self, labels, parent=None, tooltips=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, text in enumerate(labels):
            name = "SegmentFirst" if i == 0 else "SegmentLast" if i == len(labels) - 1 else "Segment"
            b = QPushButton(text, objectName=name)
            b.setCheckable(True)
            b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            if tooltips:
                b.setToolTip(tooltips[i])
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.idClicked.connect(self.changed.emit)

    def set_index(self, i: int) -> None:
        b = self.group.button(i)
        if b:
            b.setChecked(True)

    def index(self) -> int:
        return self.group.checkedId()


class Card(QFrame):
    def __init__(self, title: str, color: str | None = None, toggle: bool = False, parent=None):
        super().__init__(parent, objectName="Card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)
        head = QHBoxLayout()
        if color:
            dot = QLabel()
            dot.setFixedSize(10, 10)
            dot.setStyleSheet(f"background:{color}; border-radius:5px;")
            head.addWidget(dot)
        self.title = QLabel(title.upper(), objectName="CardTitle")
        if color:
            self.title.setStyleSheet(f"color:{color};")
        head.addWidget(self.title)
        head.addStretch(1)
        self.toggle = None
        if toggle:
            self.toggle = QCheckBox()
            self.toggle.setCursor(Qt.PointingHandCursor)
            head.addWidget(self.toggle)
        outer.addLayout(head)
        self.body = QWidget()
        self.grid = QGridLayout(self.body)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(6)
        self.grid.setColumnStretch(1, 1)
        outer.addWidget(self.body)
        if self.toggle:
            self.toggle.toggled.connect(self._sync_body)

    def _sync_body(self, on: bool):
        self.body.setEnabled(on)

    def row(self, label: str, widget: QWidget) -> None:
        r = self.grid.rowCount()
        if label:
            lbl = QLabel(label, objectName="Muted")
            self.grid.addWidget(lbl, r, 0)
            self.grid.addWidget(widget, r, 1)
        else:
            self.grid.addWidget(widget, r, 0, 1, 2)


def fmt_time(v):
    return si_format(v, "s") + "/div"


def fmt_rate(v):
    return si_format(v, "S/s")


class ChannelCard(Card):
    def __init__(self, idx: int, panel: "ControlPanel"):
        super().__init__(f"Channel {idx + 1}", theme.CH_COLORS[idx], toggle=True)
        self.idx = idx
        self.panel = panel
        self.vdiv = Stepper(VDIVS, lambda v: si_format(v, "V") + "/div")
        self.row("Scale", self.vdiv)
        self.coupling = Segmented(["DC", "AC"], tooltips=["DC coupling", "AC coupling (requires the AC/DC hardware mod on a 6022)"])
        self.row("Coupling", self.coupling)
        self.probe = Segmented([f"{p}×" for p in PROBES])
        self.row("Probe", self.probe)
        off = QWidget()
        ol = QHBoxLayout(off)
        ol.setContentsMargins(0, 0, 0, 0)
        ol.setSpacing(4)
        self.offset = QDoubleSpinBox(decimals=2, minimum=-4, maximum=4, singleStep=0.1, suffix=" div")
        zero = QPushButton("0", objectName="Step")
        zero.setToolTip("Center the trace")
        ol.addWidget(self.offset, 1)
        ol.addWidget(zero)
        self.row("Position", off)
        self.invert = QCheckBox("Invert")
        self.row("", self.invert)

        self.toggle.toggled.connect(self._on_enabled)
        self.vdiv.changed.connect(self._on_vdiv)
        self.coupling.changed.connect(lambda i: self._set("coupling", ["DC", "AC"][i]))
        self.probe.changed.connect(self._on_probe)
        self.offset.valueChanged.connect(lambda v: self._set("offset", v))
        zero.clicked.connect(lambda: self.offset.setValue(0.0))
        self.invert.toggled.connect(lambda v: self._set("invert", v))

    @property
    def ch(self):
        return self.panel.settings.channels[self.idx]

    def _set(self, name, value):
        setattr(self.ch, name, value)
        self.panel.emit_changed()

    def _on_enabled(self, on):
        if not on and not self.panel.settings.channels[1 - self.idx].enabled:
            # keep at least one channel on
            self.toggle.blockSignals(True)
            self.toggle.setChecked(True)
            self.toggle.blockSignals(False)
            self.body.setEnabled(True)
            return
        self._set("enabled", on)

    def _on_vdiv(self, v):
        self.ch.vdiv = v
        self.panel.emit_changed()

    def _on_probe(self, i):
        old = self.ch.probe
        new = PROBES[i]
        self.ch.probe = new
        self.vdiv.set_values([v * new for v in VDIVS])
        self.ch.vdiv = self.ch.vdiv * new / old
        self.vdiv.set_value(self.ch.vdiv)
        self.panel.emit_changed()

    def refresh(self):
        ch = self.ch
        for w in (self.toggle, self.vdiv, self.coupling, self.probe, self.offset, self.invert):
            w.blockSignals(True)
        self.toggle.setChecked(ch.enabled)
        self.body.setEnabled(ch.enabled)
        self.vdiv.set_values([v * ch.probe for v in VDIVS])
        self.vdiv.set_value(ch.vdiv)
        self.coupling.set_index(0 if ch.coupling == "DC" else 1)
        self.probe.set_index(PROBES.index(ch.probe) if ch.probe in PROBES else 0)
        self.offset.setValue(ch.offset)
        self.invert.setChecked(ch.invert)
        for w in (self.toggle, self.vdiv, self.coupling, self.probe, self.offset, self.invert):
            w.blockSignals(False)


class ControlPanel(QScrollArea):
    changed = Signal()
    level_to_50 = Signal()
    clear_persistence = Signal()

    def __init__(self, settings: Settings, model: ScopeModel, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.model = model
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFixedWidth(318)
        root = QWidget()
        self.lay = QVBoxLayout(root)
        self.lay.setContentsMargins(10, 10, 10, 10)
        self.lay.setSpacing(10)
        self.setWidget(root)

        # --- horizontal
        h = Card("Horizontal")
        self.timebase = Stepper(TIMEBASES, fmt_time)
        h.row("Time", self.timebase)
        self.rate = QComboBox()
        h.row("Sample rate", self.rate)
        self.rate_info = QLabel("", objectName="Muted")
        h.row("", self.rate_info)
        self.lay.addWidget(h)

        # --- trigger
        t = Card("Trigger")
        self.trig_mode = Segmented(TRIGGER_MODES, tooltips=[
            "Show a free-running trace when no trigger is found",
            "Only update on a trigger event",
            "Capture one triggered trace, then stop",
            "Continuous scrolling display (best for slow signals)",
        ])
        t.row("", self.trig_mode)
        self.trig_src = Segmented(["CH1", "CH2"])
        t.row("Source", self.trig_src)
        self.trig_slope = Segmented(["↗ Rising", "↘ Falling"])
        t.row("Slope", self.trig_slope)
        lvl = QWidget()
        ll = QHBoxLayout(lvl)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(4)
        self.trig_level = QDoubleSpinBox(decimals=3, minimum=-500, maximum=500, singleStep=0.01, suffix=" V")
        b50 = QPushButton("50%")
        b50.setToolTip("Set the level to the middle of the signal")
        ll.addWidget(self.trig_level, 1)
        ll.addWidget(b50)
        t.row("Level", lvl)
        self.noise = QComboBox()
        self.noise.addItems(NOISE_REJECT)
        self.noise.setToolTip("Averages samples around the trigger point to ignore noise")
        t.row("Noise reject", self.noise)
        self.lay.addWidget(t)

        # --- channels
        self.ch_cards = [ChannelCard(i, self) for i in range(2)]
        for c in self.ch_cards:
            self.lay.addWidget(c)

        # --- math
        m = Card("Math", theme.CH_COLORS[2], toggle=True)
        self.math_card = m
        self.math_expr = QComboBox()
        self.math_expr.setEditable(True)
        self.math_expr.setInsertPolicy(QComboBox.NoInsert)
        for label, expr, _ in PRESETS:
            self.math_expr.addItem(label, expr)
        self.math_expr.setToolTip("Pick a preset or type a formula and press Enter.\n\n" + FUNCTIONS_HELP)
        m.row("Formula", self.math_expr)
        self.math_error = QLabel("", objectName="Muted")
        self.math_error.setWordWrap(True)
        self.math_error.setStyleSheet(f"color:{theme.BAD};")
        self.math_error.hide()
        m.row("", self.math_error)
        self.math_vdiv = Stepper([v for v in VDIVS] + [10.0, 20.0, 50.0, 100.0, 1e3, 1e4, 1e5],
                                 lambda v: si_format(v, "") + "/div")
        m.row("Scale", self.math_vdiv)
        self.math_offset = QDoubleSpinBox(decimals=2, minimum=-4, maximum=4, singleStep=0.1, suffix=" div")
        m.row("Position", self.math_offset)
        self.lay.addWidget(m)

        # --- spectrum
        sp = Card("Spectrum", toggle=True)
        self.spec_card = sp
        self.window = QComboBox()
        self.window.addItems(WINDOWS)
        sp.row("Window", self.window)
        chk = QWidget()
        cl = QHBoxLayout(chk)
        cl.setContentsMargins(0, 0, 0, 0)
        self.spec_ch = []
        for i, name in enumerate(["CH1", "CH2", "Math"]):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setStyleSheet(f"QPushButton:checked {{ background:{theme.CH_COLORS[i]}; border-color:{theme.CH_COLORS[i]}; color:#05070a; }}")
            cl.addWidget(b)
            self.spec_ch.append(b)
        sp.row("Show", chk)
        self.spec_view = Segmented(SPECTRUM_VIEWS)
        sp.row("View", self.spec_view)
        self.sgram_ch = Segmented(["CH1", "CH2", "Math"])
        sp.row("Waterfall", self.sgram_ch)
        self.lay.addWidget(sp)

        # --- decoder
        dc = Card("Decoder", toggle=True)
        self.dec_card = dc
        self.protocol = Segmented(["UART", "I²C", "SPI"])
        dc.row("", self.protocol)
        self.dec_stack = QStackedWidget()
        # UART page
        up = QWidget()
        ug = QGridLayout(up)
        ug.setContentsMargins(0, 0, 0, 0)
        ug.setColumnStretch(1, 1)
        self.uart_ch = Segmented(["CH1", "CH2"])
        self.uart_baud = QComboBox()
        self.uart_baud.setEditable(True)
        self.uart_baud.addItem("Auto", 0.0)
        for b in (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600):
            self.uart_baud.addItem(str(b), float(b))
        self.uart_bits = QSpinBox(minimum=5, maximum=9, value=8)
        self.uart_parity = Segmented(["None", "Even", "Odd"])
        self.uart_stop = Segmented(["1", "1.5", "2"])
        self.uart_inv = QCheckBox("Inverted (RS-232)")
        for r, (lbl, w) in enumerate([("Line", self.uart_ch), ("Baud", self.uart_baud), ("Data bits", self.uart_bits),
                                       ("Parity", self.uart_parity), ("Stop bits", self.uart_stop), ("", self.uart_inv)]):
            if lbl:
                ug.addWidget(QLabel(lbl, objectName="Muted"), r, 0)
                ug.addWidget(w, r, 1)
            else:
                ug.addWidget(w, r, 0, 1, 2)
        self.dec_stack.addWidget(up)
        # I2C page
        ip = QWidget()
        ig = QGridLayout(ip)
        ig.setContentsMargins(0, 0, 0, 0)
        ig.setColumnStretch(1, 1)
        self.i2c_scl = Segmented(["CH1", "CH2"])
        ig.addWidget(QLabel("SCL", objectName="Muted"), 0, 0)
        ig.addWidget(self.i2c_scl, 0, 1)
        note = QLabel("SDA is the other channel", objectName="Muted")
        note.setWordWrap(True)
        ig.addWidget(note, 1, 0, 1, 2)
        self.dec_stack.addWidget(ip)
        # SPI page
        pp = QWidget()
        pg_ = QGridLayout(pp)
        pg_.setContentsMargins(0, 0, 0, 0)
        pg_.setColumnStretch(1, 1)
        self.spi_clk = Segmented(["CH1", "CH2"])
        self.spi_edge = Segmented(["Rising", "Falling"])
        self.spi_bits = QSpinBox(minimum=4, maximum=32, value=8)
        self.spi_order = Segmented(["MSB", "LSB"])
        for r, (lbl, w) in enumerate([("CLK", self.spi_clk), ("Sample on", self.spi_edge), ("Word bits", self.spi_bits),
                                       ("Bit order", self.spi_order)]):
            pg_.addWidget(QLabel(lbl, objectName="Muted"), r, 0)
            pg_.addWidget(w, r, 1)
        note = QLabel("Data is the other channel. Words are framed by gaps in the clock.", objectName="Muted")
        note.setWordWrap(True)
        pg_.addWidget(note, 4, 0, 1, 2)
        self.dec_stack.addWidget(pp)
        dc.row("", self.dec_stack)
        thr = QWidget()
        tl = QHBoxLayout(thr)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(4)
        self.thr_auto = QCheckBox("Auto")
        self.thr = QDoubleSpinBox(decimals=2, minimum=-50, maximum=50, singleStep=0.1, suffix=" V")
        tl.addWidget(self.thr_auto)
        tl.addWidget(self.thr, 1)
        dc.row("Threshold", thr)
        self.dec_fmt = Segmented(DECODE_FORMATS)
        dc.row("Format", self.dec_fmt)
        self.lay.addWidget(dc)

        # --- display
        d = Card("Display")
        self.xy = Segmented(["YT", "XY"], tooltips=["Voltage over time", "CH2 against CH1 (Lissajous)"])
        d.row("Mode", self.xy)
        pers = QWidget()
        pl = QHBoxLayout(pers)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.setSpacing(4)
        self.persist = QComboBox()
        self.persist.addItems(["Off" if p == 0 else "Infinite" if p < 0 else f"{p:g} s" for p in PERSISTENCE])
        self.persist.setToolTip("Keep fading copies of older traces on screen (like an analog phosphor)")
        clear = QPushButton("Clear")
        pl.addWidget(self.persist, 1)
        pl.addWidget(clear)
        d.row("Persistence", pers)
        self.zoom = QCheckBox("Zoom window")
        self.zoom.setToolTip("Drag the shaded region on the screen to choose what to magnify")
        d.row("", self.zoom)
        self.interp = QComboBox()
        self.interp.addItems(INTERPOLATIONS)
        d.row("Drawing", self.interp)
        self.avg = QComboBox()
        self.avg.addItems(["Off", "2", "4", "8", "16", "32"])
        self.avg.setToolTip("Average consecutive triggered traces to reduce noise")
        d.row("Averaging", self.avg)
        self.cursors = QCheckBox("Measurement cursors")
        d.row("", self.cursors)
        self.calfreq = QComboBox()
        self.calfreq.addItems([si_format(f, "Hz") for f in model.cal_frequencies])
        self.calfreq.setToolTip("Frequency of the square wave on the scope's calibration output")
        d.row("Cal. output", self.calfreq)
        self.lay.addWidget(d)
        self.lay.addStretch(1)

        # wiring
        self.timebase.changed.connect(lambda v: self._set(settings, "timebase", v))
        self.rate.currentIndexChanged.connect(self._on_rate)
        self.trig_mode.changed.connect(lambda i: self._set(settings.trigger, "mode", TRIGGER_MODES[i]))
        self.trig_src.changed.connect(lambda i: self._set(settings.trigger, "source", i))
        self.trig_slope.changed.connect(lambda i: self._set(settings.trigger, "slope", ["Rising", "Falling"][i]))
        self.trig_level.valueChanged.connect(lambda v: self._set(settings.trigger, "level", v))
        b50.clicked.connect(self.level_to_50.emit)
        self.noise.currentIndexChanged.connect(lambda i: self._set(settings.trigger, "noise_reject", i))
        m.toggle.toggled.connect(lambda v: self._set(settings, "math_enabled", v))
        self.math_expr.activated.connect(self._on_math_preset)
        self.math_expr.lineEdit().editingFinished.connect(self._on_math_typed)
        self.math_vdiv.changed.connect(lambda v: self._set(settings, "math_vdiv", v))
        self.math_offset.valueChanged.connect(lambda v: self._set(settings, "math_offset", v))
        sp.toggle.toggled.connect(lambda v: self._set(settings, "spectrum_enabled", v))
        self.window.currentTextChanged.connect(lambda v: self._set(settings, "spectrum_window", v))
        for i, b in enumerate(self.spec_ch):
            b.toggled.connect(lambda v, i=i: self._set_spec(i, v))
        self.interp.currentTextChanged.connect(lambda v: self._set(settings, "interpolation", v))
        self.xy.changed.connect(lambda i: self._set(settings, "xy_mode", i == 1))
        self.persist.currentIndexChanged.connect(lambda i: self._set(settings, "persistence", PERSISTENCE[i]))
        clear.clicked.connect(self.clear_persistence.emit)
        self.zoom.toggled.connect(lambda v: self._set(settings, "zoom_enabled", v))
        self.spec_view.changed.connect(lambda i: self._set(settings, "spectrum_view", SPECTRUM_VIEWS[i]))
        self.sgram_ch.changed.connect(lambda i: self._set(settings, "spectrogram_channel", i))
        dcs = settings.decoder
        dc.toggle.toggled.connect(lambda v: self._set(self.settings.decoder, "enabled", v))
        self.protocol.changed.connect(self._on_protocol)
        self.uart_ch.changed.connect(lambda i: self._set(self.settings.decoder, "uart_channel", i))
        self.uart_baud.activated.connect(lambda i: self._set(self.settings.decoder, "uart_baud", float(self.uart_baud.itemData(i) or 0)))
        self.uart_baud.lineEdit().editingFinished.connect(self._on_baud_typed)
        self.uart_bits.valueChanged.connect(lambda v: self._set(self.settings.decoder, "uart_bits", v))
        self.uart_parity.changed.connect(lambda i: self._set(self.settings.decoder, "uart_parity", ["None", "Even", "Odd"][i]))
        self.uart_stop.changed.connect(lambda i: self._set(self.settings.decoder, "uart_stop", [1, 1.5, 2][i]))
        self.uart_inv.toggled.connect(lambda v: self._set(self.settings.decoder, "uart_inverted", v))
        self.i2c_scl.changed.connect(lambda i: self._set(self.settings.decoder, "clock_channel", i))
        self.spi_clk.changed.connect(lambda i: self._set(self.settings.decoder, "clock_channel", i))
        self.spi_edge.changed.connect(lambda i: self._set(self.settings.decoder, "spi_edge", ["Rising", "Falling"][i]))
        self.spi_bits.valueChanged.connect(lambda v: self._set(self.settings.decoder, "spi_bits", v))
        self.spi_order.changed.connect(lambda i: self._set(self.settings.decoder, "spi_msb_first", i == 0))
        self.thr_auto.toggled.connect(self._on_thr_auto)
        self.thr.valueChanged.connect(lambda v: self._set(self.settings.decoder, "threshold", v))
        self.dec_fmt.changed.connect(lambda i: self._set(self.settings.decoder, "format", DECODE_FORMATS[i]))
        del dcs
        self.avg.currentIndexChanged.connect(lambda i: self._set(settings, "averaging", [1, 2, 4, 8, 16, 32][i]))
        self.cursors.toggled.connect(lambda v: self._set(settings, "cursors", v))
        self.calfreq.currentIndexChanged.connect(lambda i: self._set(settings, "calfreq", float(model.cal_frequencies[i])))
        self.refresh()

    # ------------------------------------------------------------

    def emit_changed(self):
        self.changed.emit()

    def _set(self, obj, name, value):
        if getattr(obj, name) != value:
            setattr(obj, name, value)
            self.changed.emit()

    def _on_math_preset(self, i):
        expr = self.math_expr.itemData(i)
        if expr:
            self.math_error.hide()
            self._set(self.settings, "math_expr", expr)

    def _on_math_typed(self):
        text = self.math_expr.currentText().strip()
        i = self.math_expr.findText(text)
        expr = self.math_expr.itemData(i) if i >= 0 else text
        try:
            compile_expr(expr)
        except MathError as exc:
            self.math_error.setText(str(exc))
            self.math_error.show()
            return
        self.math_error.hide()
        self._set(self.settings, "math_expr", expr)

    def _on_protocol(self, i):
        self.dec_stack.setCurrentIndex(i)
        self._set(self.settings.decoder, "protocol", PROTOCOLS[i])
        self._sync_clock_widgets()

    def _sync_clock_widgets(self):
        for w in (self.i2c_scl, self.spi_clk):
            w.blockSignals(True)
            w.set_index(self.settings.decoder.clock_channel)
            w.blockSignals(False)

    def _on_baud_typed(self):
        text = self.uart_baud.currentText().strip().lower()
        try:
            baud = 0.0 if text in ("", "auto") else float(text.replace("k", "e3"))
        except ValueError:
            return
        self._set(self.settings.decoder, "uart_baud", baud)

    def _on_thr_auto(self, on):
        self.thr.setEnabled(not on)
        self._set(self.settings.decoder, "threshold_auto", on)

    def _set_spec(self, i, v):
        self.settings.spectrum_channels[i] = v
        self.changed.emit()

    def _on_rate(self, i):
        if i < 0:
            return
        value = self.rate.itemData(i)
        self._set(self.settings, "samplerate", float(value or 0.0))

    def _fill_rates(self):
        s = self.settings
        limit = self.model.max_rate(s.channel_count())
        self.rate.blockSignals(True)
        self.rate.clear()
        self.rate.addItem("Auto", 0.0)
        for r in self.model.sample_rates:
            if r.rate <= limit:
                self.rate.addItem(fmt_rate(r.rate), r.rate)
        idx = self.rate.findData(s.samplerate) if s.samplerate else 0
        self.rate.setCurrentIndex(max(idx, 0))
        self.rate.blockSignals(False)

    def set_actual_rate(self, rate: float, record: int | None = None):
        txt = f"Running at {fmt_rate(rate)}"
        if record:
            txt += f" · {record:,} pts"
        self.rate_info.setText(txt)

    def refresh(self):
        """Update all widgets from the settings object."""
        s = self.settings
        widgets = [self.timebase, self.trig_mode, self.trig_src, self.trig_slope, self.trig_level, self.noise,
                   self.math_card.toggle, self.math_expr, self.math_vdiv, self.math_offset, self.spec_card.toggle,
                   self.window, self.interp, self.avg, self.cursors, self.calfreq, *self.spec_ch,
                   self.xy, self.persist, self.zoom, self.spec_view, self.sgram_ch, self.dec_card.toggle,
                   self.protocol, self.uart_ch, self.uart_baud, self.uart_bits, self.uart_parity, self.uart_stop,
                   self.uart_inv, self.i2c_scl, self.spi_clk, self.spi_edge, self.spi_bits, self.spi_order,
                   self.thr_auto, self.thr, self.dec_fmt]
        for w in widgets:
            w.blockSignals(True)
        self.timebase.set_value(s.timebase)
        self._fill_rates()
        self.trig_mode.set_index(TRIGGER_MODES.index(s.trigger.mode) if s.trigger.mode in TRIGGER_MODES else 0)
        self.trig_src.set_index(s.trigger.source)
        self.trig_slope.set_index(0 if s.trigger.slope == "Rising" else 1)
        self.trig_level.setValue(s.trigger.level)
        self.noise.setCurrentIndex(s.trigger.noise_reject)
        self.math_card.toggle.setChecked(s.math_enabled)
        self.math_card.body.setEnabled(s.math_enabled)
        i = self.math_expr.findData(s.math_expr)
        if i >= 0:
            self.math_expr.setCurrentIndex(i)
        else:
            self.math_expr.setEditText(label_for(s.math_expr))
        self.math_vdiv.set_value(s.math_vdiv)
        self.math_offset.setValue(s.math_offset)
        self.spec_card.toggle.setChecked(s.spectrum_enabled)
        self.spec_card.body.setEnabled(s.spectrum_enabled)
        self.window.setCurrentText(s.spectrum_window)
        for b, on in zip(self.spec_ch, s.spectrum_channels):
            b.setChecked(on)
        self.interp.setCurrentText(s.interpolation)
        self.avg.setCurrentIndex([1, 2, 4, 8, 16, 32].index(s.averaging) if s.averaging in (1, 2, 4, 8, 16, 32) else 0)
        self.cursors.setChecked(s.cursors)
        self.xy.set_index(1 if s.xy_mode else 0)
        self.persist.setCurrentIndex(PERSISTENCE.index(s.persistence) if s.persistence in PERSISTENCE else 0)
        self.zoom.setChecked(s.zoom_enabled)
        self.spec_view.set_index(SPECTRUM_VIEWS.index(s.spectrum_view) if s.spectrum_view in SPECTRUM_VIEWS else 0)
        self.sgram_ch.set_index(s.spectrogram_channel)
        d = s.decoder
        self.dec_card.toggle.setChecked(d.enabled)
        self.dec_card.body.setEnabled(d.enabled)
        pi = PROTOCOLS.index(d.protocol) if d.protocol in PROTOCOLS else 0
        self.protocol.set_index(pi)
        self.dec_stack.setCurrentIndex(pi)
        self.uart_ch.set_index(d.uart_channel)
        bi = self.uart_baud.findData(float(d.uart_baud))
        if bi >= 0:
            self.uart_baud.setCurrentIndex(bi)
        else:
            self.uart_baud.setEditText(f"{d.uart_baud:g}")
        self.uart_bits.setValue(d.uart_bits)
        self.uart_parity.set_index(["None", "Even", "Odd"].index(d.uart_parity) if d.uart_parity in ("None", "Even", "Odd") else 0)
        self.uart_stop.set_index({1: 0, 1.5: 1, 2: 2}.get(d.uart_stop, 0))
        self.uart_inv.setChecked(d.uart_inverted)
        self.i2c_scl.set_index(d.clock_channel)
        self.spi_clk.set_index(d.clock_channel)
        self.spi_edge.set_index(0 if d.spi_edge == "Rising" else 1)
        self.spi_bits.setValue(d.spi_bits)
        self.spi_order.set_index(0 if d.spi_msb_first else 1)
        self.thr_auto.setChecked(d.threshold_auto)
        self.thr.setEnabled(not d.threshold_auto)
        self.thr.setValue(d.threshold)
        self.dec_fmt.set_index(DECODE_FORMATS.index(d.format) if d.format in DECODE_FORMATS else 0)
        freqs = list(self.model.cal_frequencies)
        self.calfreq.setCurrentIndex(min(range(len(freqs)), key=lambda k: abs(freqs[k] - s.calfreq)))
        for w in widgets:
            w.blockSignals(False)
        for c in self.ch_cards:
            c.refresh()
