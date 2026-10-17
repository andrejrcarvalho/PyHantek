"""MCP server that lets AI assistants operate the scope.

Runs Streamable HTTP on localhost inside the app. Every tool executes its
work on the Qt GUI thread, so it sees exactly what the user sees.

Connect Claude Code with:
    claude mcp add --transport http pyhantek http://127.0.0.1:8765/mcp
"""

from __future__ import annotations

import asyncio
import logging
import math
import socket
import threading
from concurrent.futures import Future
from dataclasses import asdict
from typing import Any, Callable, Literal

import numpy as np
from mcp.server.mcpserver import Image, MCPServer
from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, Qt, Signal

from .mathexpr import compile_expr, unit_for
from .processing import measure, spectrum
from .settings import NOISE_REJECT, PERSISTENCE, PROBES, TIMEBASES, VDIVS

log = logging.getLogger(__name__)

DEFAULT_PORT = 8765

INSTRUCTIONS = """\
Controls a live Hantek DSO-6022-family USB oscilloscope (2 channels, 8-bit, up to 30 MS/s)
running in the PyHantek app. All values use SI base units: seconds, volts, hertz.
The screen is 10 horizontal divisions by 8 vertical divisions.
Typical flow: get_status -> configure with set_channel / set_horizontal / set_trigger (or autoset)
-> get_measurements / get_waveform / get_spectrum / screenshot.
Extras: set_math(expression=...) for formulas, set_decoder + get_decoded for UART/I2C/SPI,
set_display for XY mode, persistence, zoom window and spectrogram.
Settings snap to the nearest valid value (1-2-5 steps) and the applied value is returned.
Measurement tools wait for a frame captured with the latest settings.
In Normal or Single trigger mode no frame arrives until the signal crosses the trigger level.
The scope's calibration output (CAL pin) is a square wave whose frequency is set with set_horizontal(calfreq=...).
"""


class GuiBridge(QObject):
    """Runs callables on the Qt GUI thread and hands back a Future."""

    _call = Signal(object)

    def __init__(self):
        super().__init__()
        self._call.connect(self._run, Qt.QueuedConnection)

    def _run(self, job):
        fn, fut = job
        if not fut.set_running_or_notify_cancel():
            return
        try:
            fut.set_result(fn())
        except BaseException as exc:  # forwarded to the waiting tool call
            fut.set_exception(exc)

    def submit(self, fn: Callable[[], Any]) -> Future:
        fut: Future = Future()
        self._call.emit((fn, fut))
        return fut


def _num(v, digits=6):
    """JSON-safe rounded number (None for NaN/inf)."""
    if v is None:
        return None
    v = float(v)
    if not math.isfinite(v):
        return None
    return float(f"{v:.{digits}g}")


def _nearest(values, v):
    return min(values, key=lambda x: abs(math.log(x / v)) if x > 0 and v > 0 else abs(x - v))


CHANNEL = Literal["CH1", "CH2", "MATH"]


class McpServer:
    def __init__(self, window, host: str = "127.0.0.1", port: int = DEFAULT_PORT):
        self.win = window
        self.host = host
        self.port = port
        self.bridge = GuiBridge()
        self._thread: threading.Thread | None = None
        self._server = None
        self.error = ""

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ------------------------------------------------------------ lifecycle

    def start(self) -> bool:
        if self.running:
            return True
        self.error = ""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind((self.host, self.port))
        except OSError as exc:
            self.error = f"port {self.port} unavailable ({exc.strerror or exc})"
            log.warning("MCP server: %s", self.error)
            return False
        import uvicorn

        try:
            app = self._build().streamable_http_app(stateless_http=True, json_response=True, host=self.host)
        except Exception as exc:
            log.exception("could not create the MCP server")
            self.error = f"setup failed: {exc}"
            return False
        config = uvicorn.Config(app, host=self.host, port=self.port, log_level="warning", lifespan="on")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._serve, name="mcp-server", daemon=True)
        self._thread.start()
        return True

    def _serve(self):
        try:
            asyncio.run(self._server.serve())
        except BaseException as exc:  # includes SystemExit from uvicorn on startup errors
            self.error = str(exc) or type(exc).__name__
            log.warning("MCP server stopped: %s", self.error)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._thread = None
        self._server = None

    # ------------------------------------------------------------ helpers

    async def gui(self, fn: Callable[[], Any], timeout: float = 10.0):
        return await asyncio.wait_for(asyncio.wrap_future(self.bridge.submit(fn)), timeout)

    async def fresh_frame(self, timeout: float):
        """Wait for a frame captured with the current settings. Returns None on timeout."""
        fut = await self.gui(self.win.request_frame)
        try:
            await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
            return True
        except asyncio.TimeoutError:
            fut.cancel()
            return None

    def _apply(self) -> None:
        self.win.panel.refresh()
        self.win._settings_changed()

    def _timeout_info(self, timeout) -> dict:
        s = self.win.settings
        return {
            "error": f"no new frame within {timeout} s",
            "trigger_mode": s.trigger.mode,
            "running": bool(self.win.acq and self.win.acq.running),
            "hint": "In Normal/Single mode the signal may not cross the trigger level; "
            "try set_trigger(mode='Auto') or adjust the level, or call run() if stopped.",
        }

    # ------------------------------------------------------------ tools

    def _build(self):
        mcp = MCPServer("pyhantek", instructions=INSTRUCTIONS)
        win = self.win

        def settings_dict():
            s = asdict(win.settings)
            s["math_unit"] = unit_for(win.settings.math_expr) or "unknown"
            return s

        # ---------------- status

        @mcp.tool()
        async def get_status() -> dict:
            """Device, acquisition state, effective sample rate and all current settings."""

            def work():
                f = win.last_frame
                acq = win.acq
                dev = win.device
                fps = None
                ft = win.frame_times
                if len(ft) >= 2:
                    fps = _num((len(ft) - 1) / max(ft[-1] - ft[0], 1e-6), 3)
                return {
                    "device": dev.name,
                    "demo": dev.is_demo,
                    "serial": dev.serial,
                    "firmware": None if dev.is_demo else f"{dev.firmware_version:04x}",
                    "running": bool(acq and acq.running),
                    "acquisition_status": win.st_state.text(),
                    "last_frame": None
                    if f is None
                    else {
                        "triggered": f.triggered,
                        "roll": f.roll,
                        "samplerate": f.samplerate,
                        "record_samples": max((len(d) for d in f.data if d is not None), default=0),
                        "clipped": {"CH1": f.clipped[0], "CH2": f.clipped[1]},
                    },
                    "frames_per_second": fps,
                    "settings": settings_dict(),
                    "limits": {
                        "timebases_s_per_div": TIMEBASES,
                        "vdiv_at_1x_probe": VDIVS,
                        "probes": PROBES,
                        "samplerates": [r.rate for r in win.device.model.sample_rates],
                        "calfreqs": list(win.device.model.cal_frequencies),
                    },
                }

            return await self.gui(work)

        # ---------------- configuration

        @mcp.tool()
        async def set_channel(
            channel: Literal["CH1", "CH2"],
            enabled: bool | None = None,
            volts_per_div: float | None = None,
            coupling: Literal["DC", "AC"] | None = None,
            probe: Literal[1, 10, 100] | None = None,
            position_div: float | None = None,
            invert: bool | None = None,
        ) -> dict:
            """Configure an input channel. volts_per_div is at the probe tip (it includes the probe factor).
            position_div moves the 0 V line (-4..4 divisions). AC coupling only works on scopes with the AC/DC mod."""

            def work():
                i = 0 if channel == "CH1" else 1
                ch = win.settings.channels[i]
                if enabled is not None:
                    other = win.settings.channels[1 - i]
                    if not enabled and not other.enabled:
                        raise ValueError("at least one channel must stay enabled")
                    ch.enabled = enabled
                if probe is not None:
                    ch.vdiv = ch.vdiv / ch.probe * probe
                    ch.probe = probe
                if volts_per_div is not None:
                    ch.vdiv = _nearest([v * ch.probe for v in VDIVS], volts_per_div)
                if coupling is not None:
                    ch.coupling = coupling
                if position_div is not None:
                    ch.offset = max(-4.0, min(4.0, float(position_div)))
                if invert is not None:
                    ch.invert = invert
                self._apply()
                return asdict(ch)

            return await self.gui(work)

        @mcp.tool()
        async def set_horizontal(
            seconds_per_div: float | None = None,
            samplerate: float | None = None,
            calfreq: float | None = None,
        ) -> dict:
            """Set the timebase (100 ns/div .. 10 s/div), an optional fixed sample rate (0 = automatic),
            and the frequency of the calibration square-wave output."""

            def work():
                s = win.settings
                if seconds_per_div is not None:
                    s.timebase = _nearest(TIMEBASES, seconds_per_div)
                if samplerate is not None:
                    rates = [r.rate for r in win.device.model.sample_rates]
                    s.samplerate = 0.0 if samplerate <= 0 else _nearest(rates, samplerate)
                if calfreq is not None:
                    s.calfreq = float(_nearest(list(win.device.model.cal_frequencies), calfreq))
                self._apply()
                return {"seconds_per_div": s.timebase, "samplerate": s.samplerate or "auto", "calfreq": s.calfreq}

            return await self.gui(work)

        @mcp.tool()
        async def set_trigger(
            mode: Literal["Auto", "Normal", "Single", "Roll"] | None = None,
            source: Literal["CH1", "CH2"] | None = None,
            slope: Literal["Rising", "Falling"] | None = None,
            level_volts: float | None = None,
            position: float | None = None,
            noise_reject: Literal["Off", "Low", "High"] | None = None,
            level_to_50_percent: bool = False,
        ) -> dict:
            """Configure the software trigger. position is where the trigger point sits on screen (0 = left, 1 = right).
            level_to_50_percent sets the level to the middle of the current signal on the source channel."""

            def work():
                t = win.settings.trigger
                if mode is not None:
                    t.mode = mode
                if source is not None:
                    t.source = 0 if source == "CH1" else 1
                if slope is not None:
                    t.slope = slope
                if level_volts is not None:
                    t.level = float(level_volts)
                if position is not None:
                    t.position = max(0.0, min(1.0, float(position)))
                if noise_reject is not None:
                    t.noise_reject = NOISE_REJECT.index(noise_reject)
                if level_to_50_percent:
                    f = win.last_frame
                    d = f.data[t.source] if f is not None else None
                    if d is not None and np.isfinite(d).any():
                        t.level = round(float((np.nanmax(d) + np.nanmin(d)) / 2), 4)
                self._apply()
                out = asdict(t)
                out["source"] = ["CH1", "CH2"][t.source]
                out["noise_reject"] = NOISE_REJECT[t.noise_reject]
                return out

            return await self.gui(work)

        @mcp.tool()
        async def set_math(
            enabled: bool | None = None,
            expression: str | None = None,
            volts_per_div: float | None = None,
            position_div: float | None = None,
        ) -> dict:
            """Configure the math trace. expression is a formula over ch1, ch2 (volts), t (s from trigger), fs.
            Examples: "ch1 - ch2", "ch1 * ch2" (power), "diff(ch1)", "integ(ch1)", "lowpass(ch1, 10e3)",
            "highpass(ch1, 100)", "avg(ch1, 10)", "sqrt(ch1**2 + ch2**2)", "ch1 > 1.5" (logic 0/1).
            Functions: abs sqrt exp log log10 sin cos tan sign floor ceil min max clip diff integ avg
            lowpass highpass mean rms dc. volts_per_div is the math trace scale in its own unit per division."""

            def work():
                s = win.settings
                if expression is not None:
                    compile_expr(expression)  # raises a readable MathError for bad input
                    s.math_expr = expression.strip()
                if enabled is not None:
                    s.math_enabled = enabled
                if volts_per_div is not None:
                    s.math_vdiv = _nearest(list(VDIVS) + [10.0, 20.0, 50.0, 100.0, 1e3, 1e4, 1e5], volts_per_div)
                if position_div is not None:
                    s.math_offset = max(-4.0, min(4.0, float(position_div)))
                self._apply()
                return {"enabled": s.math_enabled, "expression": s.math_expr, "unit": unit_for(s.math_expr) or "unknown",
                        "per_div": s.math_vdiv, "position_div": s.math_offset}

            return await self.gui(work)

        @mcp.tool()
        async def set_display(
            spectrum: bool | None = None,
            spectrum_window: Literal["Hann", "Hamming", "Blackman", "Flat top", "Rectangular"] | None = None,
            averaging: Literal[1, 2, 4, 8, 16, 32] | None = None,
            interpolation: Literal["Linear", "Sin(x)/x", "Dots"] | None = None,
            cursors: bool | None = None,
            xy_mode: bool | None = None,
            persistence_s: float | None = None,
            zoom: bool | None = None,
            zoom_start_div: float | None = None,
            zoom_end_div: float | None = None,
            spectrum_view: Literal["Spectrum", "Spectrogram", "Both"] | None = None,
            spectrogram_channel: CHANNEL | None = None,
        ) -> dict:
            """Display options. xy_mode plots CH2 (vertical) against CH1 (horizontal).
            persistence_s keeps fading older traces (0 = off, -1 = infinite; snaps to 0.2/0.5/1/2/5 s).
            zoom shows a magnified window of divisions zoom_start_div..zoom_end_div (0..10) of the main screen.
            spectrum_view picks the FFT plot, a spectrogram (waterfall) of spectrogram_channel, or both."""

            def work():
                s = win.settings
                if spectrum is not None:
                    s.spectrum_enabled = spectrum
                if spectrum_window is not None:
                    s.spectrum_window = spectrum_window
                if averaging is not None:
                    s.averaging = int(averaging)
                if interpolation is not None:
                    s.interpolation = interpolation
                if cursors is not None:
                    s.cursors = cursors
                if xy_mode is not None:
                    s.xy_mode = xy_mode
                if persistence_s is not None:
                    p = float(persistence_s)
                    s.persistence = 0.0 if p == 0 else -1.0 if p < 0 else min((x for x in PERSISTENCE if x > 0), key=lambda x: abs(x - p))
                if zoom is not None:
                    s.zoom_enabled = zoom
                if zoom_start_div is not None or zoom_end_div is not None:
                    a = s.zoom_start if zoom_start_div is None else zoom_start_div
                    b = s.zoom_end if zoom_end_div is None else zoom_end_div
                    a, b = sorted((max(0.0, min(10.0, float(a))), max(0.0, min(10.0, float(b)))))
                    if b - a < 0.05:
                        raise ValueError("zoom window must be at least 0.05 divisions wide")
                    s.zoom_start, s.zoom_end = a, b
                if spectrum_view is not None:
                    s.spectrum_view = spectrum_view
                    if spectrum is None:
                        s.spectrum_enabled = True
                if spectrogram_channel is not None:
                    s.spectrogram_channel = {"CH1": 0, "CH2": 1, "MATH": 2}[spectrogram_channel]
                self._apply()
                return {"spectrum": s.spectrum_enabled, "spectrum_window": s.spectrum_window, "averaging": s.averaging,
                        "interpolation": s.interpolation, "cursors": s.cursors, "xy_mode": s.xy_mode,
                        "persistence_s": s.persistence, "zoom": s.zoom_enabled,
                        "zoom_div": [s.zoom_start, s.zoom_end],
                        "zoom_seconds_per_div": s.timebase * (s.zoom_end - s.zoom_start) / 10,
                        "spectrum_view": s.spectrum_view,
                        "spectrogram_channel": ["CH1", "CH2", "MATH"][s.spectrogram_channel]}

            return await self.gui(work)

        # ---------------- acquisition control

        @mcp.tool()
        async def run() -> dict:
            """Start continuous acquisition."""
            await self.gui(lambda: win.acq and win.acq.set_running(True))
            return {"running": True}

        @mcp.tool()
        async def stop() -> dict:
            """Stop acquisition; the last trace stays on screen and can still be measured."""
            await self.gui(lambda: win.acq and win.acq.set_running(False))
            return {"running": False}

        @mcp.tool()
        async def single(timeout_s: float = 10.0) -> dict:
            """Arm a single-shot capture and wait for the trigger. Returns the measurements of the captured trace."""
            await self.gui(win.single_shot)
            if await self.fresh_frame(timeout_s) is None:
                return self._timeout_info(timeout_s)
            return await self.gui(self._measurements)

        @mcp.tool()
        async def autoset(timeout_s: float = 15.0) -> dict:
            """Automatically choose V/div, position, timebase and trigger level for the connected signals."""
            await self.gui(win.autoset)
            loop = asyncio.get_running_loop()
            end = loop.time() + timeout_s
            while loop.time() < end:
                await asyncio.sleep(0.2)
                if not await self.gui(lambda: win.autoset_stage):
                    break
            else:
                return {"error": "autoset did not finish", "settings": await self.gui(settings_dict)}
            await self.fresh_frame(5.0)
            return {"settings": await self.gui(settings_dict), "measurements": await self.gui(self._measurements)}

        # ---------------- data

        @mcp.tool()
        async def get_measurements(fresh: bool = True, timeout_s: float = 5.0) -> dict:
            """Automatic measurements of the on-screen part of each enabled trace: frequency, period, Vpp, max, min,
            mean, RMS, AC RMS, duty cycle (0..1), rise and fall time (10-90 %). Null means not measurable.
            With fresh=True it first waits for a frame captured with the current settings."""
            if fresh and await self.gui(lambda: bool(win.acq and win.acq.running)):
                if await self.fresh_frame(timeout_s) is None:
                    return self._timeout_info(timeout_s)
            return await self.gui(self._measurements)

        @mcp.tool()
        async def get_waveform(
            channel: CHANNEL = "CH1",
            max_points: int = 500,
            span: Literal["screen", "record"] = "screen",
            fresh: bool = True,
        ) -> dict:
            """Sample data of one trace as time/voltage pairs. span='screen' returns what is visible
            (time 0 = trigger point); span='record' returns the whole capture. Data is evenly decimated
            to max_points (<= 5000), so read samplerate_effective before interpreting fast edges."""
            if fresh and await self.gui(lambda: bool(win.acq and win.acq.running)):
                await self.fresh_frame(5.0)

            def work():
                f = win.last_frame
                if f is None or win.last_view is None:
                    return {"error": "no data yet"}
                s = win.settings
                idx = {"CH1": 0, "CH2": 1, "MATH": 2}[channel]
                if span == "screen":
                    x, volts, sr = win.last_view
                    v = volts[idx]
                    if v is None:
                        return {"error": f"{channel} is not enabled"}
                    t = (x[: len(v)] - s.trigger.position * 10) * s.timebase
                    keep = (x[: len(v)] >= 0) & (x[: len(v)] <= 10)
                    t, v = t[keep], v[keep]
                else:
                    data = list(f.data)
                    if idx == 2:
                        v = win.compute_math(data[0], data[1], f.samplerate)
                        if v is None:
                            return {"error": f"math: {win.math_error}"}
                    else:
                        v = data[idx]
                    if v is None:
                        return {"error": f"{channel} is not enabled"}
                    t = (np.arange(len(v)) - f.trigger_index) / f.samplerate
                    sr = f.samplerate
                n = len(v)
                m = max(2, min(int(max_points), 5000, n))
                sel = np.linspace(0, n - 1, m).round().astype(int)
                return {
                    "channel": channel,
                    "unit": (unit_for(s.math_expr) or "unknown") if idx == 2 else "V",
                    "samplerate": sr,
                    "samples_total": n,
                    "samplerate_effective": _num(sr * (m - 1) / max(n - 1, 1)),
                    "triggered": f.triggered,
                    "time_s": [_num(x, 6) for x in t[sel]],
                    "volts": [_num(x, 5) for x in v[sel]],
                }

            return await self.gui(work)

        @mcp.tool()
        async def get_spectrum(
            channel: CHANNEL = "CH1",
            peaks: int = 10,
            window: Literal["Hann", "Hamming", "Blackman", "Flat top", "Rectangular"] = "Flat top",
            fresh: bool = True,
        ) -> dict:
            """FFT of the full capture. Returns the strongest peaks (frequency in Hz, level in dBV rms),
            the noise floor, and THD of the strongest tone (harmonics 2..10). 'Flat top' gives accurate amplitudes;
            'Hann' gives better frequency resolution."""
            if fresh and await self.gui(lambda: bool(win.acq and win.acq.running)):
                await self.fresh_frame(5.0)

            def work():
                f = win.last_frame
                if f is None:
                    return {"error": "no data yet"}
                s = win.settings
                idx = {"CH1": 0, "CH2": 1, "MATH": 2}[channel]
                if idx == 2:
                    v = win.compute_math(f.data[0], f.data[1], f.samplerate)
                    if v is None:
                        return {"error": f"math: {win.math_error}"}
                else:
                    v = f.data[idx]
                if v is None:
                    return {"error": f"{channel} is not enabled"}
                return _analyse_spectrum(v, f.samplerate, window, peaks)

            return await self.gui(work)

        @mcp.tool()
        async def set_decoder(
            enabled: bool | None = None,
            protocol: Literal["UART", "I2C", "SPI"] | None = None,
            uart_channel: Literal["CH1", "CH2"] | None = None,
            baud: float | None = None,
            data_bits: int | None = None,
            parity: Literal["None", "Even", "Odd"] | None = None,
            stop_bits: Literal[1, 1.5, 2] | None = None,
            inverted: bool | None = None,
            clock_channel: Literal["CH1", "CH2"] | None = None,
            spi_edge: Literal["Rising", "Falling"] | None = None,
            spi_bits: int | None = None,
            spi_msb_first: bool | None = None,
            threshold_auto: bool | None = None,
            threshold_volts: float | None = None,
            format: Literal["Hex", "Dec", "ASCII", "Bin"] | None = None,
        ) -> dict:
            """Configure the serial protocol decoder.
            UART uses one channel (baud 0 = auto detect). I2C uses clock_channel as SCL and the other channel as SDA.
            SPI uses clock_channel as CLK and the other as data; words are framed by gaps in the clock (no chip select).
            threshold_auto=True puts the logic threshold at the middle of the signal; threshold_volts sets a fixed one.
            The sample rate must be at least ~4x the bit rate: pick a fast enough timebase."""

            def work():
                d = win.settings.decoder
                for name, val in (("enabled", enabled), ("protocol", protocol), ("uart_bits", data_bits),
                                  ("uart_parity", parity), ("uart_inverted", inverted), ("spi_edge", spi_edge),
                                  ("spi_bits", spi_bits), ("spi_msb_first", spi_msb_first), ("format", format)):
                    if val is not None:
                        setattr(d, name, val)
                if uart_channel is not None:
                    d.uart_channel = 0 if uart_channel == "CH1" else 1
                if clock_channel is not None:
                    d.clock_channel = 0 if clock_channel == "CH1" else 1
                if baud is not None:
                    d.uart_baud = max(0.0, float(baud))
                if stop_bits is not None:
                    d.uart_stop = float(stop_bits)
                if threshold_volts is not None:
                    d.threshold_auto = False
                    d.threshold = float(threshold_volts)
                if threshold_auto is not None:
                    d.threshold_auto = threshold_auto
                self._apply()
                return asdict(d)

            return await self.gui(work)

        @mcp.tool()
        async def set_demo_signal(signal: Literal["sine", "uart", "i2c", "spi", "lissajous"]) -> dict:
            """Only for the simulated demo device: choose the test signals.
            sine = CH1 1 kHz distorted sine + CH2 calibration square; uart = CH1 115200 baud text;
            i2c = CH1 SCL / CH2 SDA at 100 kHz; spi = CH1 CLK / CH2 MOSI at 1 MHz; lissajous = 1 kHz / 1.5 kHz sines."""

            def work():
                if not win.device.is_demo:
                    raise ValueError("a real scope is connected; demo signals are not available")
                win.set_demo_signal(signal)
                return {"demo_signal": signal}

            return await self.gui(work)

        @mcp.tool()
        async def get_decoded(max_items: int = 200, fresh: bool = True) -> dict:
            """Decoded protocol items from the latest capture (time relative to the trigger point).
            Also returns the decoded bytes as hex and as text for UART/SPI."""
            if fresh and await self.gui(lambda: bool(win.acq and win.acq.running)):
                await self.fresh_frame(5.0)

            def work():
                s = win.settings
                if not s.decoder.enabled:
                    return {"error": "decoder is off; call set_decoder(enabled=True, protocol=...)"}
                f = win.last_frame
                res = win.last_decode
                if f is None or res is None:
                    return {"error": "no data yet"}
                sr = f.samplerate
                items = []
                for a in res.annotations[: max(1, min(int(max_items), 2000))]:
                    t0 = (a.start - f.trigger_index) / sr if not f.roll else a.start / sr
                    items.append({"time_s": _num(t0), "duration_s": _num((a.end - a.start) / sr), "kind": a.kind,
                                  "text": a.text, "value": a.value, **({"info": a.info} if a.info else {})})
                values = [a.value for a in res.annotations if a.kind in ("data", "error") and a.value is not None]
                out = {"protocol": s.decoder.protocol, "samplerate": sr, "count": len(res.annotations), "items": items}
                if res.message:
                    out["message"] = res.message
                out.update({k: v for k, v in res.params.items()})
                if values and max(values) < 256:
                    out["bytes_hex"] = " ".join(f"{v:02X}" for v in values)
                    out["text"] = "".join(chr(v) if 32 <= v < 127 or v in (9, 10, 13) else "." for v in values)
                return out

            return await self.gui(work)

        @mcp.tool()
        async def screenshot(include_panel: bool = False) -> Image:
            """PNG image of the scope screen (traces, spectrum and measurement table).
            include_panel=True captures the whole window including the control panel."""

            def work():
                widget = win if include_panel else win.splitter
                pix = widget.grab()
                ba = QByteArray()
                buf = QBuffer(ba)
                buf.open(QIODevice.WriteOnly)
                pix.save(buf, "PNG")
                return bytes(ba.data())

            return Image(data=await self.gui(work), format="png")

        return mcp

    # runs on the GUI thread
    def _measurements(self) -> dict:
        win = self.win
        f = win.last_frame
        if f is None or win.last_view is None:
            return {"error": "no data yet"}
        x, volts, sr = win.last_view
        on = (x >= 0) & (x <= 10)
        out: dict[str, Any] = {
            "triggered": f.triggered,
            "samplerate": sr,
            "seconds_per_div": win.settings.timebase,
            "channels": {},
        }
        for i, name in enumerate(["CH1", "CH2", "MATH"]):
            v = volts[i]
            if v is None:
                continue
            m = measure(v[on[: len(v)]], sr, f.lsb[i] if i < 2 else 0.0)
            unit = (unit_for(win.settings.math_expr) or "unknown") if i == 2 else "V"
            d = {k: _num(val) for k, val in asdict(m).items()}
            d["unit"] = unit
            if i < 2:
                d["clipped"] = f.clipped[i]
                d["volts_per_div"] = win.settings.channels[i].vdiv
            out["channels"][name] = d
        return out


def _analyse_spectrum(v: np.ndarray, sr: float, win: str, npeaks: int) -> dict:
    freqs, db = spectrum(v, sr, win)
    if len(db) < 8:
        return {"error": "not enough samples"}
    df = freqs[1] - freqs[0]
    floor = float(np.median(db[1:]))
    # local maxima clearly above the noise floor, skipping DC
    k = np.arange(2, len(db) - 1)
    is_peak = (db[k] >= db[k - 1]) & (db[k] > db[k + 1]) & (db[k] > floor + 15)
    cand = k[is_peak]
    cand = cand[np.argsort(db[cand])[::-1]]
    chosen: list[int] = []
    for c in cand:  # keep peaks at least 3 bins apart (window main lobe)
        if all(abs(c - o) > 3 for o in chosen):
            chosen.append(int(c))
        if len(chosen) >= max(1, min(int(npeaks), 50)):
            break
    result: dict[str, Any] = {
        "samplerate": sr,
        "resolution_hz": _num(df),
        "window": win,
        "dc_dbv": _num(db[0], 4),
        "noise_floor_dbv": _num(floor, 4),
        "peaks": [{"freq_hz": _num(freqs[c]), "dbv": _num(db[c], 4), "vrms": _num(10 ** (db[c] / 20), 4)} for c in chosen],
    }
    if chosen and chosen[0] < 20:
        result["thd_percent"] = None
        result["thd_note"] = "too few signal periods in the record for THD; use a slower timebase"
    elif chosen:
        f0 = chosen[0]
        fund = 10 ** (db[f0] / 20)
        harm = []
        for h in range(2, 11):
            b = f0 * h
            if b + 2 >= len(db):
                break
            harm.append(10 ** (np.max(db[b - 2 : b + 3]) / 20))
        if harm:
            result["thd_percent"] = _num(100 * math.sqrt(sum(a * a for a in harm)) / fund, 4)
            result["fundamental_hz"] = _num(freqs[f0])
    return result
