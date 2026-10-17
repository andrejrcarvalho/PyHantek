"""Serial protocol decoders (UART, I²C, SPI) working on analog captures.

Each channel is first turned into a 0/1 signal with a hysteresis threshold,
then decoded into annotations with sample-index start/end positions.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

STANDARD_BAUDS = [300, 600, 1200, 2400, 4800, 9600, 14400, 19200, 28800, 38400, 57600, 76800,
                  115200, 230400, 250000, 460800, 500000, 921600, 1000000, 2000000]


@dataclass
class Annotation:
    start: float  # sample index
    end: float
    kind: str  # data, addr, start, stop, ack, nak, error
    text: str
    value: int | None = None
    info: str = ""


@dataclass
class DecodeResult:
    annotations: list[Annotation] = field(default_factory=list)
    message: str = ""  # problems or detected parameters (e.g. auto baud)
    params: dict = field(default_factory=dict)


# ------------------------------------------------------------------ digitize


def digitize(v: np.ndarray, threshold: float | None = None, hysteresis: float = 0.1) -> np.ndarray | None:
    """Analog -> uint8 0/1 with hysteresis. Returns None when there is no usable signal swing."""
    v = np.asarray(v, dtype=np.float64)
    finite = v[np.isfinite(v)]
    if finite.size < 8:
        return None
    lo, hi = np.percentile(finite, [2, 98])
    swing = hi - lo
    if threshold is None:
        if swing < 0.05:
            return None
        threshold = (lo + hi) / 2
    h = max(swing * hysteresis / 2, 1e-6)
    state = np.full(len(v), -1, dtype=np.int8)
    state[v >= threshold + h] = 1
    state[v <= threshold - h] = 0
    known = state >= 0
    if not known.any():
        return np.zeros(len(v), np.uint8)
    idx = np.where(known, np.arange(len(v)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = state[idx]
    first = int(np.argmax(known))
    out[:first] = state[first]
    return out.astype(np.uint8)


def _edges(d: np.ndarray):
    """(rising, falling) edge positions: index of the first sample after the change."""
    ch = np.diff(d.astype(np.int8))
    return np.flatnonzero(ch > 0) + 1, np.flatnonzero(ch < 0) + 1


def fmt_value(v: int, fmt: str, bits: int = 8) -> str:
    if fmt == "Dec":
        return str(v)
    if fmt == "Bin":
        return format(v, f"0{bits}b")
    if fmt == "ASCII":
        return repr(chr(v))[1:-1] if 32 <= v < 127 else f"\\x{v:02X}"
    return f"0x{v:0{(bits + 3) // 4}X}"


# ------------------------------------------------------------------ UART


def detect_baud(d: np.ndarray, sr: float) -> float | None:
    r, f = _edges(d)
    e = np.sort(np.concatenate((r, f)))
    if e.size < 3:
        return None
    widths = np.diff(e)
    widths = widths[widths > 0]
    if widths.size == 0:
        return None
    # the shortest pulses are single bits; use a low percentile to ignore glitches
    w = float(np.percentile(widths, 5)) if widths.size > 20 else float(widths.min())
    if w < 1:
        return None
    raw = sr / w
    best = min(STANDARD_BAUDS, key=lambda b: abs(b - raw) / b)
    return float(best) if abs(best - raw) / best < 0.12 else raw


def decode_uart(
    v: np.ndarray,
    sr: float,
    baud: float = 0,
    data_bits: int = 8,
    parity: str = "None",
    stop_bits: float = 1,
    inverted: bool = False,
    threshold: float | None = None,
    fmt: str = "Hex",
) -> DecodeResult:
    res = DecodeResult()
    d = digitize(v, threshold)
    if d is None:
        res.message = "no signal"
        return res
    if inverted:
        d = 1 - d
    if not baud:
        baud = detect_baud(d, sr)
        if not baud:
            res.message = "could not detect baud rate"
            return res
        res.params["baud_detected"] = baud
    spb = sr / baud
    if spb < 3:
        res.message = f"sample rate too low for {baud:.0f} baud (need ≥ {3 * baud:.0f} S/s); use a faster timebase"
        return res
    n = len(d)
    _, falling = _edges(d)
    n_par = 0 if parity == "None" else 1
    frame_bits = 1 + data_bits + n_par + stop_bits
    pos = 0.0
    i = 0
    while i < falling.size:
        s = falling[i]
        if s < pos:
            i += 1
            continue
        end = s + frame_bits * spb
        if end > n:
            break
        mid = lambda k: int(s + (k + 0.5) * spb)  # noqa: E731
        if d[mid(0)] != 0:  # glitch, not a start bit
            i += 1
            continue
        bits = [int(d[mid(1 + k)]) for k in range(data_bits)]
        value = sum(b << k for k, b in enumerate(bits))  # LSB first
        errors = []
        if n_par:
            p = int(d[mid(1 + data_bits)])
            ones = sum(bits) + p
            if (parity == "Even" and ones % 2) or (parity == "Odd" and not ones % 2):
                errors.append("parity")
        if d[min(mid(1 + data_bits + n_par), n - 1)] != 1:
            errors.append("framing")
        text = fmt_value(value, fmt, data_bits)
        kind = "error" if errors else "data"
        res.annotations.append(Annotation(float(s), float(s + (1 + data_bits + n_par) * spb), kind, text, value,
                                          " ".join(errors)))
        pos = s + (1 + data_bits + n_par + 0.5) * spb  # next start bit may begin after half a stop bit
        i += 1
    res.params["baud"] = baud
    return res


# ------------------------------------------------------------------ I2C


def decode_i2c(scl_v, sda_v, sr: float, threshold: float | None = None, fmt: str = "Hex") -> DecodeResult:
    res = DecodeResult()
    scl = digitize(scl_v, threshold)
    sda = digitize(sda_v, threshold)
    if scl is None or sda is None:
        res.message = "no signal on SCL or SDA"
        return res
    n = min(len(scl), len(sda))
    scl, sda = scl[:n], sda[:n]
    scl_rise, _ = _edges(scl)
    sda_rise, sda_fall = _edges(sda)
    starts = sda_fall[(scl[sda_fall] == 1) & (scl[np.maximum(sda_fall - 1, 0)] == 1)]
    stops = sda_rise[(scl[sda_rise] == 1) & (scl[np.maximum(sda_rise - 1, 0)] == 1)]
    events = sorted([(int(p), 0) for p in starts] + [(int(p), 1) for p in stops] + [(int(p), 2) for p in scl_rise])
    period = float(np.median(np.diff(scl_rise))) if scl_rise.size > 2 else 10.0
    half = period / 2
    active = False
    bits: list[tuple[int, int]] = []
    byte_index = 0
    for pos, kind in events:
        if kind == 0:  # START / repeated START
            res.annotations.append(Annotation(pos - half / 2, pos + half / 2, "start", "Sr" if active else "S"))
            active, bits, byte_index = True, [], 0
        elif kind == 1:  # STOP
            if active:
                res.annotations.append(Annotation(pos - half / 2, pos + half / 2, "stop", "P"))
            active, bits = False, []
        elif active:
            bits.append((pos, int(sda[min(pos + 1, n - 1)])))
            if len(bits) == 9:
                value = 0
                for _, b in bits[:8]:
                    value = (value << 1) | b
                t0, t8 = bits[0][0] - half, bits[7][0] + half
                if byte_index == 0:
                    rw = "R" if value & 1 else "W"
                    afmt = "Hex" if fmt == "ASCII" else fmt  # addresses are never text
                    res.annotations.append(Annotation(t0, t8, "addr", f"{rw} {fmt_value(value >> 1, afmt, 7)}", value >> 1,
                                                      "read" if value & 1 else "write"))
                else:
                    res.annotations.append(Annotation(t0, t8, "data", fmt_value(value, fmt), value))
                ack = bits[8][1] == 0
                res.annotations.append(Annotation(t8, bits[8][0] + half, "ack" if ack else "nak", "A" if ack else "N"))
                bits = []
                byte_index += 1
    if not res.annotations:
        res.message = "no I²C START found (check which channel is SCL)"
    return res


# ------------------------------------------------------------------ SPI


def decode_spi(
    clk_v,
    data_v,
    sr: float,
    edge: str = "Rising",
    bits: int = 8,
    msb_first: bool = True,
    threshold: float | None = None,
    fmt: str = "Hex",
) -> DecodeResult:
    """SPI without chip select: words are framed by gaps in the clock."""
    res = DecodeResult()
    clk = digitize(clk_v, threshold)
    data = digitize(data_v, threshold)
    if clk is None or data is None:
        res.message = "no signal on CLK or DATA"
        return res
    n = min(len(clk), len(data))
    rise, fall = _edges(clk[:n])
    edges = rise if edge == "Rising" else fall
    if edges.size < bits:
        res.message = "not enough clock edges"
        return res
    period = float(np.median(np.diff(edges)))
    half = period / 2
    gaps = np.flatnonzero(np.diff(edges) > 2.5 * period) + 1
    for group in np.split(edges, gaps):
        for k in range(0, len(group) - bits + 1, bits):
            word = group[k : k + bits]
            vals = [int(data[min(p, n - 1)]) for p in word]
            if not msb_first:
                vals = vals[::-1]
            value = 0
            for b in vals:
                value = (value << 1) | b
            res.annotations.append(Annotation(word[0] - half, word[-1] + half, "data", fmt_value(value, fmt, bits), value))
        rest = len(group) % bits
        if rest:
            res.annotations.append(Annotation(group[-rest] - half, group[-1] + half, "error", f"{rest} bits", None,
                                              "incomplete word"))
    return res


# ------------------------------------------------------------------ dispatch


def run_decoder(cfg, data: list, sr: float) -> DecodeResult:
    """Decode according to DecoderSettings `cfg` using channel arrays `data` (volts)."""
    thr = None if cfg.threshold_auto else cfg.threshold
    if cfg.protocol == "UART":
        v = data[cfg.uart_channel]
        if v is None:
            return DecodeResult(message=f"CH{cfg.uart_channel + 1} is off")
        return decode_uart(v, sr, cfg.uart_baud, cfg.uart_bits, cfg.uart_parity, cfg.uart_stop, cfg.uart_inverted, thr,
                           cfg.format)
    a, b = cfg.clock_channel, 1 - cfg.clock_channel
    if data[a] is None or data[b] is None:
        return DecodeResult(message=f"{cfg.protocol} needs both channels on")
    if cfg.protocol == "I2C":
        return decode_i2c(data[a], data[b], sr, thr, cfg.format)
    return decode_spi(data[a], data[b], sr, cfg.spi_edge, cfg.spi_bits, cfg.spi_msb_first, thr, cfg.format)


# ------------------------------------------------------------------ test signal helpers


def synth_uart(payload: bytes, baud: float, sr: float, idle_bits: float = 3, high=3.3, low=0.0) -> np.ndarray:
    spb = sr / baud
    bits = [1] * int(idle_bits)
    for byte in payload:
        bits += [0] + [(byte >> k) & 1 for k in range(8)] + [1, 1]
    bits += [1] * int(idle_bits)
    idx = (np.arange(int(len(bits) * spb)) / spb).astype(int)
    return np.where(np.asarray(bits)[idx] == 1, high, low).astype(float)


def synth_i2c(addr: int, payload: bytes, read: bool, sr: float, rate: float = 100e3, high=3.3) -> tuple[np.ndarray, np.ndarray]:
    q = max(int(sr / rate / 4), 1)  # samples per quarter clock
    scl: list[int] = []
    sda: list[int] = []

    def put(c, d, k=1):
        scl.extend([c] * (q * k))
        sda.extend([d] * (q * k))

    put(1, 1, 4)
    put(1, 0, 2)  # START: SDA falls while SCL high
    put(0, 0, 1)
    for byte in [(addr << 1) | int(read)] + list(payload):
        for bit in [(byte >> (7 - k)) & 1 for k in range(8)] + [0]:  # 8 bits + ACK
            put(0, bit, 1)
            put(1, bit, 2)
            put(0, bit, 1)
    put(0, 0, 1)
    put(1, 0, 2)
    put(1, 1, 4)  # STOP: SDA rises while SCL high
    return np.array(scl, float) * high, np.array(sda, float) * high


def synth_spi(payload: bytes, sr: float, rate: float = 1e6, high=3.3) -> tuple[np.ndarray, np.ndarray]:
    h = max(int(sr / rate / 2), 1)
    clk: list[int] = [0] * (h * 6)
    dat: list[int] = [0] * (h * 6)
    for byte in payload:
        for k in range(8):
            b = (byte >> (7 - k)) & 1
            clk += [0] * h + [1] * h
            dat += [b] * (2 * h)
        clk += [0] * (h * 8)  # gap between words
        dat += [0] * (h * 8)
    return np.array(clk, float) * high, np.array(dat, float) * high
