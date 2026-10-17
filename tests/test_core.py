import math
import threading

import numpy as np
import pytest

from pyhantek import device as dev
from pyhantek.acquisition import Acquisition, select_rate
from pyhantek.calibration import Calibration
from pyhantek.models import MODELS
from pyhantek.processing import find_trigger, measure, si_format, spectrum
from pyhantek.settings import Settings


def test_calfreq_encoding_matches_openhantek():
    assert dev.calfreq_code(1000) == 1
    assert dev.calfreq_code(50) == 105
    assert dev.calfreq_code(100) == 0  # special case
    assert dev.calfreq_code(1200) == 212
    assert dev.calfreq_code(100e3) == 100


def test_gain_selection():
    m = MODELS[0]
    assert m.gain_index_for(0.02) == 0
    assert m.gain_index_for(0.01) == 0
    assert m.gain_index_for(0.2) == 3
    assert m.gain_index_for(10.0) == 7


def test_rate_selection_follows_timebase():
    m = MODELS[0]
    s = Settings(timebase=1e-3)
    assert m.sample_rates[select_rate(m, s, 2)].rate == 1e6
    s.timebase = 1e-6
    assert m.sample_rates[select_rate(m, s, 2)].rate == 12e6  # avoids the unstable 15 MS/s max
    assert m.sample_rates[select_rate(m, s, 1)].rate == 24e6
    s.samplerate = 100e3
    assert m.sample_rates[select_rate(m, s, 2)].rate == 100e3


def test_trigger_rising_and_falling():
    t = np.arange(2000)
    x = np.sin(2 * np.pi * t / 200)
    i = find_trigger(x, 0.0, True, 100, 1900)
    assert i is not None and x[i] >= 0 > x[i - 1]
    j = find_trigger(x, 0.0, False, 100, 1900)
    assert j is not None and x[j] <= 0 < x[j - 1]
    assert find_trigger(x, 2.0, True, 100, 1900) is None


def test_trigger_noise_reject():
    rng = np.random.default_rng(0)
    x = np.sin(2 * np.pi * np.arange(4000) / 400) + rng.normal(0, 0.3, 4000)
    i = find_trigger(x, 0.0, True, 50, 3900, average=20)
    # should land close to a true rising zero crossing (multiples of 400)
    assert i is not None and min(i % 400, 400 - i % 400) < 25


def test_measurements_square_wave():
    sr = 1e6
    t = np.arange(20000) / sr
    x = np.where((t * 1000) % 1 < 0.3, 2.0, 0.0)
    m = measure(x, sr)
    assert m.freq == pytest.approx(1000, rel=1e-3)
    assert m.duty == pytest.approx(0.3, abs=0.01)
    assert m.vpp == pytest.approx(2.0)
    assert m.mean == pytest.approx(0.6, abs=0.01)


def test_spectrum_peak():
    sr = 100e3
    t = np.arange(8192) / sr
    f, db = spectrum(np.sqrt(2) * np.sin(2 * np.pi * 5000 * t), sr, "Flat top")
    k = np.argmax(db)
    assert f[k] == pytest.approx(5000, abs=sr / 8192)
    assert db[k] == pytest.approx(0.0, abs=0.1)  # 1 V rms = 0 dBV


def test_calibration_tables():
    data = bytearray([255] * 80)
    data[0] = 130  # off_ls step 0 ch0
    data[48] = 0x80 + 50  # fine_ls step 0 ch0 -> +0.2
    data[32] = 0x80 + 5  # gain step 0 ch0 -> 1.01
    cal = Calibration.from_eeprom_bytes(bytes(data))
    assert cal.zero_level(0, 0, False) == pytest.approx(130.2)
    assert cal.zero_level(0, 1, False) == 128.0
    assert cal.gain_factor(0, 0) == pytest.approx(1.01)


def test_si_format():
    assert si_format(0.0012, "V") == "1.20 mV"
    assert si_format(1e6, "S/s") == "1.00 MS/s"
    assert si_format(math.nan) == "—"


def test_demo_capture_end_to_end(qtbot=None):
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    s = Settings(timebase=500e-6)
    s.trigger.level = 0.0
    acq = Acquisition(dev.DemoDevice(), s)
    frames = []
    acq.frame_ready.connect(frames.append)
    acq._capture_once()  # run synchronously, no thread
    app.processEvents()
    assert frames, "no frame produced"
    f = frames[-1]
    assert f.triggered
    ch1 = f.data[0]
    # demo CH1: 1.5 V sine + 0.25 V 3rd harmonic
    assert 2.6 < np.ptp(ch1) < 3.6
    m = measure(ch1, f.samplerate)
    assert m.freq == pytest.approx(1000, rel=0.01)
    ch2 = measure(f.data[1], f.samplerate)
    assert ch2.freq == pytest.approx(1000, rel=0.01)
    assert ch2.duty == pytest.approx(0.5, abs=0.02)


def test_demo_roll_mode():
    from PySide6.QtCore import QCoreApplication

    app = QCoreApplication.instance() or QCoreApplication([])
    s = Settings(timebase=10e-3)
    s.trigger.mode = "Roll"
    acq = Acquisition(dev.DemoDevice(), s)
    frames = []
    acq.frame_ready.connect(frames.append)
    acq._capture_once()
    app.processEvents()
    assert frames and frames[-1].roll
    assert np.isfinite(frames[-1].data[0]).any()


def test_hex_parser_rejects_bad_checksum():
    with pytest.raises(dev.DeviceError):
        dev.parse_intel_hex(":0100000000FE\n")
    blocks = dev.parse_intel_hex(":020000000102FB\n:020002000304F5\n:00000001FF\n")
    assert blocks == [(0, bytes([1, 2, 3, 4]))]


def test_device_read_without_stop():
    d = dev.DemoDevice()
    raw = d.read(4096, 1e6, threading.Event())
    assert raw is not None and len(raw) == 4096


def test_minmax_decimate_keeps_ends_and_peaks():
    from pyhantek.processing import minmax_decimate

    x = np.arange(10003, dtype=float)
    y = np.zeros(10003)
    y[5001] = 7.0  # glitch
    y[-1] = -3.0
    xd, yd = minmax_decimate(x, y, 1500)
    assert len(xd) <= 3002
    assert xd[-1] == x[-1] and yd.min() == -3.0 and yd.max() == 7.0


def test_frequency_with_two_periods_on_screen():
    sr = 1e6
    for phase in np.linspace(0, 1, 7, endpoint=False):
        t = np.arange(2000) / sr  # exactly two periods of 1 kHz
        x = np.sin(2 * np.pi * (1000 * t + phase))
        m = measure(x, sr)
        assert m.freq == pytest.approx(1000, rel=0.01), phase


def test_trigger_hysteresis_ignores_noise_on_opposite_edge():
    rng = np.random.default_rng(3)
    sr = 2e6
    t = np.arange(20000) / sr
    for _ in range(30):
        phase = rng.uniform(0, 1)
        x = 1.5 * np.sin(2 * np.pi * (1000 * t + phase)) + rng.normal(0, 0.012, len(t))
        i = find_trigger(x, 0.0, True, 5000, 15000)
        assert i is not None
        # must be on a rising edge: the clean signal's slope is positive there
        assert np.cos(2 * np.pi * (1000 * t[i] + phase)) > 0
