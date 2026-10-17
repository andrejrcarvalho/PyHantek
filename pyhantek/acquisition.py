"""Background acquisition: configures the scope, captures, converts and triggers."""

from __future__ import annotations

import copy
import logging
import threading
import time
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QThread, Signal

from . import device as dev
from .calibration import Calibration
from .processing import downsample, find_trigger, split_channels
from .settings import Settings

log = logging.getLogger(__name__)

MAX_RECORD = 20000  # net samples per channel and capture
LEAD_SKIP = 2048  # unstable raw samples at the start of every capture
SLOW_RATE = 10e3  # below this the firmware is always used in 2-channel mode


@dataclass
class Frame:
    """One processed capture, ready for display."""

    samplerate: float
    data: list[np.ndarray | None]  # volts per channel (None = channel off)
    trigger_index: int  # sample shown at the trigger position
    triggered: bool
    roll: bool = False
    clipped: list[bool] = field(default_factory=lambda: [False, False])
    lsb: list[float] = field(default_factory=lambda: [0.0, 0.0])  # volts per ADC digit
    timestamp: float = field(default_factory=time.monotonic)
    generation: int = 0  # settings generation this frame was captured with


@dataclass
class HwConfig:
    gain_index: tuple[int, int]
    rate_index: int
    channels: int
    coupling: int
    calfreq: int


def select_rate(model, settings: Settings, channels: int) -> int:
    """Index into model.sample_rates for the current timebase."""
    limit = model.max_rate(channels)
    rates = model.sample_rates
    if settings.samplerate:
        best = 0
        for i, r in enumerate(rates):
            if r.rate <= min(settings.samplerate, limit):
                best = i
        return best
    # highest rate (avoiding the unstable maximum) that keeps the screen within half the record
    best = 0
    for i, r in enumerate(rates):
        if r.rate < limit and r.rate * settings.duration <= MAX_RECORD / 2:
            best = i
    return best


class Acquisition(QThread):
    frame_ready = Signal(object)
    status = Signal(str)
    progress = Signal(float)  # 0..1 while a slow capture is running, -1 when done
    running_changed = Signal(bool)
    rate_changed = Signal(float)
    device_lost = Signal(str)
    calibration_done = Signal(bool, str)

    def __init__(self, device, settings: Settings, parent=None):
        super().__init__(parent)
        self.device = device
        self.model = device.model
        self.cal = Calibration.from_eeprom_bytes(device.read_calibration())
        self.cal_file = ""
        if not device.is_demo:
            self.cal_file = self.cal.load_ini(self.model, device.serial)
        self._settings = copy.deepcopy(settings)
        self._generation = 0
        self._cur_gen = 0
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop_capture = threading.Event()
        self._quit = False
        self._running = True
        self._last_hw: dict[int, bytes] = {}
        self._calibrate = False
        self._roll: list[np.ndarray] | None = None
        self._roll_key = None
        self.current_rate = 0.0

    # ------------------------------------------------------------ API (GUI thread)

    def update_settings(self, settings: Settings, generation: int = 0) -> None:
        with self._lock:
            old = self._settings
            self._settings = copy.deepcopy(settings)
            self._generation = generation
        # abort slow captures when the user changes something that matters
        if (old.timebase, old.samplerate, old.trigger.mode, old.channel_count()) != (
            settings.timebase,
            settings.samplerate,
            settings.trigger.mode,
            settings.channel_count(),
        ) or [c.vdiv / c.probe for c in old.channels] != [c.vdiv / c.probe for c in settings.channels]:
            self._stop_capture.set()
        self._wake.set()

    def set_running(self, running: bool) -> None:
        if running == self._running:
            return
        self._running = running
        if not running:
            self._stop_capture.set()
        self._wake.set()
        self.running_changed.emit(running)

    @property
    def running(self) -> bool:
        return self._running

    def calibrate_offsets(self) -> None:
        self._calibrate = True
        self._stop_capture.set()
        self._wake.set()

    def shutdown(self) -> None:
        self._quit = True
        self._stop_capture.set()
        self._wake.set()
        self.wait(3000)
        try:
            self.device.close()
        except Exception:
            pass

    # ------------------------------------------------------------ worker

    def run(self) -> None:
        while not self._quit:
            try:
                if self._calibrate:
                    self._calibrate = False
                    self._run_calibration()
                    continue
                if not self._running:
                    self._wake.wait(0.1)
                    self._wake.clear()
                    continue
                self._capture_once()
            except dev.DeviceGone as exc:
                self._running = False
                self.running_changed.emit(False)
                self.device_lost.emit(str(exc))
                return
            except dev.DeviceError as exc:
                log.warning("capture failed: %s", exc)
                self.status.emit(f"USB error: {exc}")
                time.sleep(0.2)
            except Exception:  # keep the thread alive, but tell the user
                log.exception("unexpected error in acquisition")
                self.status.emit("Internal error during acquisition (see log)")
                time.sleep(0.5)

    def _snapshot(self) -> Settings:
        with self._lock:
            self._cur_gen = self._generation
            return copy.deepcopy(self._settings)

    def _hw_config(self, s: Settings) -> HwConfig:
        channels = s.channel_count()
        rate_index = select_rate(self.model, s, channels)
        if self.model.sample_rates[rate_index].rate < SLOW_RATE:
            channels = 2
        gains = tuple(self.model.gain_index_for(c.vdiv / c.probe) for c in s.channels)
        coupling = (0x01 if s.channels[0].coupling == "DC" else 0) | (0x10 if s.channels[1].coupling == "DC" else 0)
        return HwConfig(gains, rate_index, channels, coupling, dev.calfreq_code(s.calfreq))

    def _apply(self, hw: HwConfig) -> bool:
        """Send changed settings to the scope. Returns True if anything changed."""
        rate = self.model.sample_rates[hw.rate_index]
        wanted = {
            dev.SET_GAIN_CH1: bytes([self.model.gain_steps[hw.gain_index[0]].hw_gain, hw.gain_index[0]]),
            dev.SET_GAIN_CH2: bytes([self.model.gain_steps[hw.gain_index[1]].hw_gain, hw.gain_index[1]]),
            dev.SET_SAMPLERATE: bytes([rate.sample_id, hw.rate_index]),
            dev.SET_NUM_CHANNELS: bytes([hw.channels]),
            dev.SET_COUPLING: bytes([hw.coupling]),
            dev.SET_CALFREQ: bytes([hw.calfreq]),
        }
        changed = False
        for code, data in wanted.items():
            if self._last_hw.get(code) != data:
                self.device.control(code, data)
                self._last_hw[code] = data
                changed = changed or code != dev.SET_CALFREQ
        if rate.rate != self.current_rate:
            self.current_rate = rate.rate
            self.rate_changed.emit(rate.rate)
        return changed

    def _convert(self, raw: np.ndarray, hw: HwConfig, s: Settings, skip: int, frame_samples: int | None = None):
        """Raw bytes -> list of volt arrays (plus clipping flags and LSB size)."""
        rate = self.model.sample_rates[hw.rate_index]
        high_speed = rate.rate >= 30e6
        out: list[np.ndarray | None] = [None, None]
        clipped = [False, False]
        lsb = [0.0, 0.0]
        chans = split_channels(raw, hw.channels)
        for ch, samples in enumerate(chans):
            samples = samples[skip:]
            if frame_samples is not None:
                samples = samples[: frame_samples * rate.oversampling]
            cs = s.channels[ch]
            step = hw.gain_index[ch]
            clipped[ch] = bool(np.any((samples == 0) | (samples == 255)))
            zero = self.cal.zero_level(step, ch, high_speed) + self.cal.offset_corr[step, ch]
            scale = self.cal.gain_factor(step, ch) * cs.probe / self.model.voltage_scale[step]
            if cs.invert:
                scale = -scale
            out[ch] = (downsample(samples, rate.oversampling) - zero) * scale
            lsb[ch] = abs(scale)
        if hw.channels == 2 and not s.channels[0].enabled and not s.math_enabled:
            out[0] = None
        if not s.channels[1].enabled and not s.math_enabled:
            out[1] = None
        return out, clipped, lsb

    def _capture_once(self) -> None:
        s = self._snapshot()
        hw = self._hw_config(s)
        changed = self._apply(hw)
        rate = self.model.sample_rates[hw.rate_index]
        display = max(2, int(round(s.duration * rate.rate)))
        roll = s.trigger.mode == "Roll"
        if roll:
            self._capture_roll(s, hw, display)
            return
        self._roll = None

        # net record: enough for the screen plus pre/post-trigger margins
        net = int(min(MAX_RECORD, max(2 * display + 200, 2000)))
        raw_per_ch = net * rate.oversampling + LEAD_SKIP
        raw_per_ch = (raw_per_ch + 511) // 512 * 512
        nbytes = raw_per_ch * hw.channels
        byte_rate = rate.rate * rate.oversampling * hw.channels
        skip = raw_per_ch - net * rate.oversampling
        slow = nbytes / byte_rate > 0.3

        self._stop_capture.clear()

        def on_chunk(_buf, received):
            self.progress.emit(received / nbytes)

        if changed and not slow:
            # the first fast capture after a change may still hold data from the old setting
            self.device.read(nbytes, byte_rate, self._stop_capture)
        raw = self.device.read(nbytes, byte_rate, self._stop_capture, on_chunk if slow else None)
        if slow:
            self.progress.emit(-1.0)
        if raw is None:
            return
        data, clipped, lsb = self._convert(raw, hw, s, skip)
        trig = s.trigger
        src = data[trig.source] if trig.source < 2 else None
        n = len(next(d for d in data if d is not None)) if any(d is not None for d in data) else 0
        pre = int(round(trig.position * display))
        trigger_index = None
        if src is not None and n >= display:
            avg = (1, 20, 400)[min(max(trig.noise_reject, 0), 2)]
            trigger_index = find_trigger(src, trig.level, trig.slope == "Rising", pre, n - (display - pre), avg)

        if trigger_index is None:
            if trig.mode == "Auto":
                frame = Frame(rate.rate, data, min(pre, max(n - 1, 0)), False, clipped=clipped, lsb=lsb,
                              generation=self._cur_gen)
                self.frame_ready.emit(frame)
                self.status.emit("Auto")
            else:
                self.status.emit("Waiting for trigger…")
            self._pace(s)
            return

        self.frame_ready.emit(Frame(rate.rate, data, trigger_index, True, clipped=clipped, lsb=lsb,
                                    generation=self._cur_gen))
        self.status.emit("Triggered")
        if trig.mode == "Single":
            self._running = False
            self.running_changed.emit(False)
            self.status.emit("Stopped (single shot)")
        self._pace(s)

    def _pace(self, s: Settings) -> None:
        # keep the GUI at <= ~30 frames/s and leave the CPU some air
        time.sleep(0.03)

    def _capture_roll(self, s: Settings, hw: HwConfig, display: int) -> None:
        """Roll mode: new samples enter on the right and push the trace left."""
        rate = self.model.sample_rates[hw.rate_index]
        key = (hw.rate_index, hw.channels, hw.gain_index, display)
        if self._roll is None or self._roll_key != key:
            self._roll = [np.full(display, np.nan), np.full(display, np.nan)]
            self._roll_key = key
        os_ = rate.oversampling
        byte_rate = rate.rate * os_ * hw.channels
        # read ~1/10 of a screen (at least 0.05 s), aligned to oversampling blocks and USB packets
        want = max(display // 10, int(rate.rate * 0.05), 1)
        raw_per_ch = want * os_ + LEAD_SKIP
        raw_per_ch = (raw_per_ch + 511) // 512 * 512
        nbytes = raw_per_ch * hw.channels
        state = {"done": 0}
        last_emit = [0.0]

        def push(buf, received):
            per_ch = received // hw.channels
            usable = (per_ch - LEAD_SKIP) // os_
            if usable <= state["done"]:
                return
            part = buf[: (LEAD_SKIP + usable * os_) * hw.channels]
            data, clipped, lsb = self._convert(part, hw, s, LEAD_SKIP)
            for ch in range(2):
                d = data[ch]
                if d is None:
                    continue
                new = d[state["done"] : usable]
                if len(new) >= display:
                    self._roll[ch][:] = new[-display:]
                elif len(new):
                    self._roll[ch] = np.roll(self._roll[ch], -len(new))
                    self._roll[ch][-len(new) :] = new
            state["done"] = usable
            state["last"] = (data, clipped, lsb)
            now = time.monotonic()
            if now - last_emit[0] > 0.04:
                last_emit[0] = now
                self._emit_roll(rate.rate, data, clipped, lsb)

        self._stop_capture.clear()
        raw = self.device.read(nbytes, byte_rate, self._stop_capture, push)
        if raw is not None:
            push(raw, len(raw))
        if "last" in state:  # always deliver the final state of this chunk
            self._emit_roll(rate.rate, *state["last"])
        self.status.emit("Roll")

    def _emit_roll(self, rate, data, clipped, lsb):
        out = [self._roll[ch].copy() if data[ch] is not None else None for ch in range(2)]
        self.frame_ready.emit(Frame(rate, out, 0, False, roll=True, clipped=clipped, lsb=lsb, generation=self._cur_gen))

    # ------------------------------------------------------------ offset calibration

    def _run_calibration(self) -> None:
        """Measure the 0 V level for every gain step (inputs must be grounded)."""
        was_running = self._running
        self.status.emit("Calibrating offsets…")
        s = self._snapshot()
        rate_index = next(i for i, r in enumerate(self.model.sample_rates) if r.rate == 100e3)
        rate = self.model.sample_rates[rate_index]
        new = self.cal.offset_corr.copy()
        steps = len(self.model.gain_steps)
        try:
            for step in range(steps):
                hw = HwConfig((step, step), rate_index, 2, 0x11, dev.calfreq_code(s.calfreq))
                self._apply(hw)
                nbytes = (20000 * rate.oversampling // 512 + 1) * 512 * 2
                byte_rate = rate.rate * rate.oversampling * 2
                self._stop_capture.clear()
                raw = None
                for _ in range(2):  # first capture may still use the old gain
                    raw = self.device.read(nbytes, byte_rate, threading.Event())
                if raw is None:
                    raise dev.DeviceError("no data")
                for ch, samples in enumerate(split_channels(raw, 2)):
                    samples = samples[LEAD_SKIP:].astype(np.float64)
                    if samples.std() > 3 or np.ptp(samples) > 20:
                        self.calibration_done.emit(False, "A signal is present. Connect both probes to ground and try again.")
                        return
                    new[step, ch] = samples.mean() - self.cal.zero_level(step, ch, False)
                    if abs(new[step, ch]) > 20:
                        self.calibration_done.emit(False, f"Offset on CH{ch + 1} is too large ({new[step, ch]:.1f} digits).")
                        return
                self.progress.emit((step + 1) / steps)
            self.cal.offset_corr[:] = new
            path = self.cal.save_offsets(self.model, self.device.serial) if not self.device.is_demo else "(demo: not saved)"
            self.calibration_done.emit(True, f"Offsets saved to {path}")
        except dev.DeviceError as exc:
            self.calibration_done.emit(False, f"Calibration failed: {exc}")
        finally:
            self.progress.emit(-1.0)
            self._last_hw.clear()
            self._running = was_running
