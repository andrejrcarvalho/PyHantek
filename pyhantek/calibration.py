"""Offset/gain calibration from the scope EEPROM and per-device ini files.

The ini file is compatible with OpenHantek6022:
  Linux/macOS: ~/.config/OpenHantek/<model>_<serial>_calibration.ini
  Windows:     %APPDATA%\\OpenHantek\\<model>_<serial>_calibration.ini
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .models import ScopeModel

STEPS = 8
CHANNELS = 2


def _valid(b: int) -> bool:
    return b not in (0, 255)


@dataclass
class Calibration:
    # raw EEPROM tables, indexed [gain_step][channel]
    off_ls: np.ndarray = field(default_factory=lambda: np.full((STEPS, CHANNELS), 255, np.uint8))
    off_hs: np.ndarray = field(default_factory=lambda: np.full((STEPS, CHANNELS), 255, np.uint8))
    gain: np.ndarray = field(default_factory=lambda: np.full((STEPS, CHANNELS), 255, np.uint8))
    fine_ls: np.ndarray = field(default_factory=lambda: np.full((STEPS, CHANNELS), 255, np.uint8))
    fine_hs: np.ndarray = field(default_factory=lambda: np.full((STEPS, CHANNELS), 255, np.uint8))
    # user corrections from the ini file (ADC digits / gain factor)
    offset_corr: np.ndarray = field(default_factory=lambda: np.zeros((STEPS, CHANNELS)))
    gain_corr: np.ndarray = field(default_factory=lambda: np.ones((STEPS, CHANNELS)))
    from_eeprom: bool = False

    @classmethod
    def from_eeprom_bytes(cls, data: bytes | None) -> "Calibration":
        cal = cls()
        if data and len(data) >= 80:
            tables = np.frombuffer(data[:80], np.uint8).reshape(5, STEPS, CHANNELS).copy()
            cal.off_ls, cal.off_hs, cal.gain, cal.fine_ls, cal.fine_hs = tables
            cal.from_eeprom = True
        return cal

    def zero_level(self, step: int, ch: int, high_speed: bool) -> float:
        """ADC value representing 0 V."""
        off = (self.off_hs if high_speed else self.off_ls)[step, ch]
        fine = (self.fine_hs if high_speed else self.fine_ls)[step, ch]
        if _valid(off) and _valid(fine):
            return off + (fine - 0x80) / 250.0
        if _valid(off):
            return float(off)
        return 128.0

    def gain_factor(self, step: int, ch: int) -> float:
        g = self.gain[step, ch]
        eeprom = 1.0 + (g - 0x80) / 500.0 if _valid(g) else 1.0
        return eeprom * self.gain_corr[step, ch]

    # ------------------------------------------------------------ ini file

    @staticmethod
    def _settings(model: ScopeModel, serial: str):
        from PySide6.QtCore import QSettings

        return QSettings(QSettings.IniFormat, QSettings.UserScope, "OpenHantek", f"{model.name}_{serial}_calibration")

    def load_ini(self, model: ScopeModel, serial: str) -> str:
        s = self._settings(model, serial)
        for group, table, default in (("offset", self.offset_corr, 0.0), ("gain", self.gain_corr, 1.0)):
            for ch in range(CHANNELS):
                for i, g in enumerate(model.gain_steps):
                    key = f"{group}/ch{ch}/{int(round(g.vdiv * 1000))}mV"
                    try:
                        table[i, ch] = float(s.value(key, default))
                    except (TypeError, ValueError):
                        table[i, ch] = default
        if str(s.value("eeprom/replace_eeprom", "false")).lower() == "true":
            # values created by calibrate_6022.py replace the EEPROM content
            for t in (self.off_ls, self.off_hs, self.gain, self.fine_ls, self.fine_hs):
                t.fill(255)
        return s.fileName()

    def save_offsets(self, model: ScopeModel, serial: str) -> str:
        s = self._settings(model, serial)
        for ch in range(CHANNELS):
            for i, g in enumerate(model.gain_steps):
                s.setValue(f"offset/ch{ch}/{int(round(g.vdiv * 1000))}mV", round(float(self.offset_corr[i, ch]), 2))
                s.setValue(f"gain/ch{ch}/{int(round(g.vdiv * 1000))}mV", round(float(self.gain_corr[i, ch]), 4))
        s.sync()
        return s.fileName()
