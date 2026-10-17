import numpy as np
import pytest

from pyhantek import decoders as dec
from pyhantek.mathexpr import PRESETS, MathError, compile_expr, evaluate
from pyhantek.settings import DecoderSettings, Settings


# ------------------------------------------------------------------ math


def test_math_presets_and_values():
    fs = 1e6
    t = np.arange(20000) / fs
    a = np.sin(2 * np.pi * 1000 * t)
    b = np.ones_like(a)
    for _, expr, _ in PRESETS:
        out = evaluate(expr, a, b, fs)
        assert out.shape == a.shape
    d = evaluate("diff(ch1)", a, b, fs)
    assert np.max(d[10:-10]) == pytest.approx(2 * np.pi * 1000, rel=1e-3)
    integ = evaluate("integ(ch1)", a, b, fs)
    assert integ.max() == pytest.approx(1 / (np.pi * 1000), rel=1e-3)
    lp = evaluate("lowpass(ch1, 10e3)", a, b, fs)
    assert np.max(lp[2000:-2000]) == pytest.approx(1 / (1 + 0.01), rel=2e-3)
    assert evaluate("ch1 > 0.5", a, b, fs).max() == 1.0
    assert evaluate("2 * pi", a, None, fs)[0] == pytest.approx(2 * np.pi)
    assert np.isnan(evaluate("ch1 / 0", a, b, fs)).all()


@pytest.mark.parametrize("bad", ['__import__("os")', "ch1.real", "open(1)", "lambda: 1", "ch3 + 1", "abs(1, 2)",
                                 '"x"', "[1, 2]", "ch1[0]", "x := 1", "", "a if"])
def test_math_rejects_unsafe_or_invalid(bad):
    with pytest.raises(MathError):
        compile_expr(bad)


def test_math_missing_channel():
    with pytest.raises(MathError):
        evaluate("ch1 + ch2", np.zeros(10), None, 1e3)


def test_legacy_math_mode_migrates():
    s = Settings.from_json('{"math_mode": 3, "math_enabled": true}')
    assert s.math_expr == "ch1 * ch2" and s.math_enabled


# ------------------------------------------------------------------ decoders


def test_uart_auto_baud_and_text():
    rng = np.random.default_rng(0)
    sr = 1e6
    for baud in (9600, 115200):
        v = dec.synth_uart(b"PyHantek\r\n", baud, sr)
        v = v + rng.normal(0, 0.08, len(v))
        r = dec.decode_uart(v, sr, fmt="ASCII")
        assert r.params["baud"] == baud
        assert "".join(a.text for a in r.annotations) == "PyHantek\\x0D\\x0A"
        assert all(a.kind == "data" for a in r.annotations)


def test_uart_inverted_and_too_slow():
    sr = 1e6
    v = 3.3 - dec.synth_uart(b"\x55\xaa", 9600, sr)
    r = dec.decode_uart(v, sr, inverted=True)
    assert [a.value for a in r.annotations] == [0x55, 0xAA]
    r = dec.decode_uart(dec.synth_uart(b"A", 9600, sr), 20e3, baud=9600)
    assert "too low" in r.message


def test_i2c_write_and_read():
    sr = 4e6
    scl, sda = dec.synth_i2c(0x3C, b"\x00\xff", False, sr)
    r = dec.decode_i2c(scl, sda, sr)
    assert [a.text for a in r.annotations] == ["S", "W 0x3C", "A", "0x00", "A", "0xFF", "A", "P"]
    scl, sda = dec.synth_i2c(0x50, b"\x12", True, sr)
    r = dec.decode_i2c(scl, sda, sr)
    assert [a.kind for a in r.annotations][:3] == ["start", "addr", "ack"] and r.annotations[1].info == "read"


def test_spi_words_and_bit_order():
    sr = 10e6
    clk, data = dec.synth_spi(b"\xa5\x3c\x01", sr)
    assert [a.value for a in dec.decode_spi(clk, data, sr).annotations] == [0xA5, 0x3C, 0x01]
    assert [a.value for a in dec.decode_spi(clk, data, sr, msb_first=False).annotations] == [0xA5, 0x3C, 0x80]


def test_run_decoder_dispatch_and_missing_channel():
    sr = 1e6
    cfg = DecoderSettings(enabled=True, protocol="UART", uart_channel=1, format="ASCII")
    v = dec.synth_uart(b"ok", 9600, sr)
    r = dec.run_decoder(cfg, [None, v], sr)
    assert "".join(a.text for a in r.annotations) == "ok"
    cfg.protocol = "I2C"
    assert "both channels" in dec.run_decoder(cfg, [None, v], sr).message


def test_flat_signal_reports_no_signal():
    assert dec.decode_uart(np.full(1000, 1.2), 1e6).message == "no signal"


# ------------------------------------------------------------------ settings


def test_channel_needed_for_decoder_and_xy():
    s = Settings()
    s.channels[1].enabled = False
    assert s.channel_count() == 1
    s.decoder.enabled = True
    s.decoder.protocol = "I2C"
    assert s.channel_count() == 2 and s.channel_needed(1)
    s.decoder.enabled = False
    s.xy_mode = True
    assert s.channel_needed(1)


def test_uart_resyncs_when_capture_starts_mid_byte():
    sr = 1e6
    v = dec.synth_uart(b"\xa5\x5aOK", 9600, sr, idle_bits=12)
    spb = sr / 9600
    # cut inside the first byte, then add a long idle gap and a clean message
    tail = dec.synth_uart(b"Hi", 9600, sr, idle_bits=12)
    v = np.concatenate((v[int(14 * spb):], tail))
    r = dec.decode_uart(v, sr, baud=9600, fmt="ASCII")
    assert "".join(a.text for a in r.annotations).endswith("Hi")
    assert all(a.kind == "data" for a in r.annotations)
