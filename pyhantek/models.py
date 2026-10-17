"""Scope model specifications for the Hantek DSO-6022 family.

Values are taken from the OpenHantek6022 C++ sources (modelDSO6022.cpp).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Firmware version shipped in pyhantek/firmware (from Hantek6022API)
FIRMWARE_VERSION = 0x0210


@dataclass(frozen=True)
class GainStep:
    hw_gain: int  # value sent to the scope (1, 2, 5, 10)
    vdiv: float  # volts/div this step is designed for (1x probe)


@dataclass(frozen=True)
class SampleRate:
    rate: float  # effective sample rate after downsampling (S/s)
    sample_id: int  # id sent with CONTROL_SETSAMPLERATE
    oversampling: int  # number of raw samples averaged into one


GAIN_STEPS = (
    GainStep(10, 20e-3),
    GainStep(10, 50e-3),
    GainStep(10, 100e-3),
    GainStep(5, 200e-3),
    GainStep(2, 500e-3),
    GainStep(1, 1.0),
    GainStep(1, 2.0),
    GainStep(1, 5.0),
)

# ADC digits per volt for each gain step (includes front-end gain error correction).
# The ADC covers 8 divisions with 256 digits -> 32 digits/div nominal.
VOLTAGE_SCALE = (250.0, 250.0, 250.0, 126.25, 49.50, 24.75, 24.75, 24.75)

SAMPLE_RATES = (
    SampleRate(100, 102, 200),
    SampleRate(200, 104, 200),
    SampleRate(500, 110, 200),
    SampleRate(1e3, 120, 200),
    SampleRate(2e3, 140, 200),
    SampleRate(5e3, 1, 200),
    SampleRate(10e3, 1, 100),
    SampleRate(20e3, 2, 100),
    SampleRate(50e3, 5, 100),
    SampleRate(100e3, 10, 100),
    SampleRate(200e3, 10, 50),
    SampleRate(500e3, 10, 20),
    SampleRate(1e6, 10, 10),
    SampleRate(2e6, 10, 5),
    SampleRate(5e6, 10, 2),
    SampleRate(10e6, 10, 1),
    SampleRate(12e6, 12, 1),
    SampleRate(15e6, 15, 1),
    SampleRate(24e6, 24, 1),
    SampleRate(30e6, 30, 1),
)

CAL_FREQUENCIES = (
    32, 40, 50, 60, 80, 100, 120, 160, 200, 250, 300, 400, 440, 500, 600, 660, 800,
    1000, 1200, 1600, 2000, 2500, 3000, 4000, 5000, 6000, 8000,
    10e3, 12e3, 16e3, 20e3, 25e3, 30e3, 40e3, 50e3, 60e3, 80e3, 100e3,
)


@dataclass(frozen=True)
class ScopeModel:
    name: str
    vid: int  # VID/PID with firmware loaded
    pid: int
    vid_nofw: int  # VID/PID of the bare FX2 before firmware upload
    pid_nofw: int
    firmware: str  # firmware file stem in pyhantek/firmware
    max_rate_single: float = 30e6
    max_rate_dual: float = 15e6
    has_eeprom_calibration: bool = True
    gain_steps: tuple[GainStep, ...] = GAIN_STEPS
    voltage_scale: tuple[float, ...] = VOLTAGE_SCALE
    sample_rates: tuple[SampleRate, ...] = SAMPLE_RATES
    cal_frequencies: tuple[float, ...] = field(default=CAL_FREQUENCIES)

    def max_rate(self, channels: int) -> float:
        return self.max_rate_single if channels == 1 else self.max_rate_dual

    def gain_index_for(self, vdiv_at_input: float) -> int:
        """Lowest gain step whose range covers the requested V/div."""
        for i, g in enumerate(self.gain_steps):
            if g.vdiv >= vdiv_at_input * 0.999:
                return i
        return len(self.gain_steps) - 1


MODELS = (
    ScopeModel("DSO-6022BE", 0x04B5, 0x6022, 0x04B4, 0x6022, "dso6022be"),
    ScopeModel("DSO-6022BL", 0x04B5, 0x602A, 0x04B4, 0x602A, "dso6022bl"),
    ScopeModel("DSO-2020", 0x04B5, 0x6022, 0x04B4, 0x2020, "dso6022be"),
    ScopeModel("DSO-6021", 0x04B5, 0x6021, 0x04B4, 0x6021, "dso6021"),
)

DEMO_MODEL = ScopeModel("Demo", 0, 0, 0, 0, "", has_eeprom_calibration=False)
