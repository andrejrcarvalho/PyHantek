"""User-facing scope settings (plain dataclasses, persisted as JSON)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field


def _125(lo_exp: int, hi_exp: int) -> list[float]:
    return [round(m * 10.0**e, 12) for e in range(lo_exp, hi_exp + 1) for m in (1, 2, 5)]


TIMEBASES = [t for t in _125(-7, 1) if 100e-9 <= t <= 10.0]  # s/div
VDIVS = [v for v in _125(-2, 0) if 10e-3 <= v <= 5.0]  # V/div at 1x probe
PROBES = [1, 10, 100]
DIVS_X = 10
DIVS_Y = 8

TRIGGER_MODES = ["Auto", "Normal", "Single", "Roll"]
SLOPES = ["Rising", "Falling"]
NOISE_REJECT = ["Off", "Low", "High"]  # trigger averaging 1, 20, 400 samples
# legacy math modes (settings files from before expressions existed)
LEGACY_MATH = ["ch1 + ch2", "ch1 - ch2", "ch2 - ch1", "ch1 * ch2"]
WINDOWS = ["Hann", "Hamming", "Blackman", "Flat top", "Rectangular"]
INTERPOLATIONS = ["Linear", "Sin(x)/x", "Dots"]
PERSISTENCE = [0.0, 0.2, 0.5, 1.0, 2.0, 5.0, -1.0]  # seconds, 0 = off, -1 = infinite
SPECTRUM_VIEWS = ["Spectrum", "Spectrogram", "Both"]
PROTOCOLS = ["UART", "I2C", "SPI"]
DECODE_FORMATS = ["Hex", "Dec", "ASCII", "Bin"]

CH_NAMES = ["CH1", "CH2"]


@dataclass
class ChannelSettings:
    enabled: bool = True
    vdiv: float = 1.0  # V/div at the probe tip
    probe: int = 1
    coupling: str = "DC"
    offset: float = 0.0  # divisions
    invert: bool = False


@dataclass
class TriggerSettings:
    mode: str = "Auto"
    source: int = 0
    slope: str = "Rising"
    level: float = 0.0  # volts
    position: float = 0.5  # 0..1 of the screen width
    noise_reject: int = 0


@dataclass
class DecoderSettings:
    enabled: bool = False
    protocol: str = "UART"
    format: str = "Hex"
    threshold_auto: bool = True
    threshold: float = 1.5  # volts
    uart_channel: int = 0
    uart_baud: float = 0.0  # 0 = auto detect
    uart_bits: int = 8
    uart_parity: str = "None"
    uart_stop: float = 1
    uart_inverted: bool = False
    clock_channel: int = 0  # I2C SCL / SPI CLK; the other channel carries the data
    spi_edge: str = "Rising"
    spi_bits: int = 8
    spi_msb_first: bool = True


@dataclass
class Settings:
    channels: list[ChannelSettings] = field(
        default_factory=lambda: [ChannelSettings(offset=1.5), ChannelSettings(offset=-2.5)]
    )
    timebase: float = 500e-6
    samplerate: float = 0.0  # 0 = automatic
    trigger: TriggerSettings = field(default_factory=TriggerSettings)
    math_enabled: bool = False
    math_expr: str = "ch1 + ch2"
    math_vdiv: float = 1.0
    math_offset: float = 0.0
    spectrum_enabled: bool = False
    spectrum_window: str = "Hann"
    spectrum_channels: list[bool] = field(default_factory=lambda: [True, True, False])
    calfreq: float = 1000.0
    interpolation: str = "Linear"
    averaging: int = 1
    cursors: bool = False
    xy_mode: bool = False
    persistence: float = 0.0
    zoom_enabled: bool = False
    zoom_start: float = 4.0  # divisions
    zoom_end: float = 6.0
    spectrum_view: str = "Spectrum"
    spectrogram_channel: int = 0
    decoder: DecoderSettings = field(default_factory=DecoderSettings)

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_json(cls, text: str) -> "Settings":
        s = cls()
        try:
            data = json.loads(text)
        except (TypeError, ValueError):
            return s
        for key, value in data.items():
            if key == "channels" and isinstance(value, list):
                s.channels = [_merge(ChannelSettings(), v) for v in value[:2]]
                while len(s.channels) < 2:
                    s.channels.append(ChannelSettings())
            elif key == "trigger" and isinstance(value, dict):
                s.trigger = _merge(TriggerSettings(), value)
            elif key == "decoder" and isinstance(value, dict):
                s.decoder = _merge(DecoderSettings(), value)
            elif key == "math_mode" and isinstance(value, int) and "math_expr" not in data:
                s.math_expr = LEGACY_MATH[value] if 0 <= value < len(LEGACY_MATH) else LEGACY_MATH[0]
            elif hasattr(s, key):
                setattr(s, key, value)
        return s

    @property
    def duration(self) -> float:
        return self.timebase * DIVS_X

    def channel_needed(self, ch: int) -> bool:
        """True if channel data is needed (shown, used by math/XY/decoder, or trigger source)."""
        if self.channels[ch].enabled or self.math_enabled or self.xy_mode:
            return True
        d = self.decoder
        if d.enabled and (d.protocol != "UART" or d.uart_channel == ch):
            return True
        return False

    def channel_count(self) -> int:
        """Hardware channel count: CH2 data is only delivered in two-channel mode."""
        two = self.channels[1].enabled or self.math_enabled or self.xy_mode
        if self.decoder.enabled and (self.decoder.protocol != "UART" or self.decoder.uart_channel == 1):
            two = True
        return 2 if two else 1


def _merge(obj, values: dict):
    if isinstance(values, dict):
        for k, v in values.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
    return obj
