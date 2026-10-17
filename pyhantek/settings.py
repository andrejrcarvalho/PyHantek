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
MATH_MODES = ["CH1 + CH2", "CH1 − CH2", "CH2 − CH1", "CH1 × CH2"]
WINDOWS = ["Hann", "Hamming", "Blackman", "Flat top", "Rectangular"]
INTERPOLATIONS = ["Linear", "Sin(x)/x", "Dots"]

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
class Settings:
    channels: list[ChannelSettings] = field(
        default_factory=lambda: [ChannelSettings(offset=1.5), ChannelSettings(offset=-2.5)]
    )
    timebase: float = 500e-6
    samplerate: float = 0.0  # 0 = automatic
    trigger: TriggerSettings = field(default_factory=TriggerSettings)
    math_enabled: bool = False
    math_mode: int = 0
    math_vdiv: float = 1.0
    math_offset: float = 0.0
    spectrum_enabled: bool = False
    spectrum_window: str = "Hann"
    spectrum_channels: list[bool] = field(default_factory=lambda: [True, True, False])
    calfreq: float = 1000.0
    interpolation: str = "Linear"
    averaging: int = 1
    cursors: bool = False

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
            elif hasattr(s, key):
                setattr(s, key, value)
        return s

    @property
    def duration(self) -> float:
        return self.timebase * DIVS_X

    def channel_count(self) -> int:
        """Hardware channel count: CH2 data is only delivered in two-channel mode."""
        return 2 if (self.channels[1].enabled or self.math_enabled) else 1


def _merge(obj, values: dict):
    if isinstance(values, dict):
        for k, v in values.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
    return obj
