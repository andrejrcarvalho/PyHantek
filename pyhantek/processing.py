"""Numeric signal processing: conversion, triggering, measurements, FFT, math."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------- raw data


def split_channels(raw: np.ndarray, channels: int) -> list[np.ndarray]:
    n = len(raw) // channels * channels
    return [raw[ch:n:channels] for ch in range(channels)]


def downsample(samples: np.ndarray, factor: int) -> np.ndarray:
    """Average blocks of `factor` raw samples (oversampling improves SNR)."""
    if factor <= 1:
        return samples.astype(np.float64)
    n = len(samples) // factor * factor
    return samples[:n].reshape(-1, factor).mean(axis=1)


# --------------------------------------------------------------- trigger


def find_trigger(x: np.ndarray, level: float, rising: bool, begin: int, end: int, average: int = 1) -> int | None:
    """First index in [begin, end) where `x` crosses `level` in the given direction.

    With `average` > 1 the mean of the `average` samples before the crossing
    must be on the other side of the level and the mean of the samples after
    it on the trigger side, which rejects noise-induced crossings.
    """
    s = 1.0 if rising else -1.0
    y = s * x
    lv = s * level
    n = len(y)
    begin = max(begin, average, 1)
    end = min(end, n - average - 1)
    if end <= begin:
        return None
    seg = y[begin:end]
    prev = y[begin - 1 : end - 1]
    idx = np.flatnonzero((seg >= lv) & (prev < lv)) + begin
    if idx.size == 0:
        return None
    if average > 1:
        cs = np.concatenate(([0.0], np.cumsum(y)))
        before = (cs[idx] - cs[idx - average]) / average
        after = (cs[idx + 1 + average] - cs[idx + 1]) / average
        idx = idx[(before < lv) & (after > lv)]
        if idx.size == 0:
            return None
    return int(idx[0])


# --------------------------------------------------------------- measurements


@dataclass
class Measurement:
    vmax: float = math.nan
    vmin: float = math.nan
    vpp: float = math.nan
    mean: float = math.nan
    rms: float = math.nan
    ac_rms: float = math.nan
    freq: float = math.nan
    period: float = math.nan
    duty: float = math.nan
    rise: float = math.nan
    fall: float = math.nan


def _crossings(x: np.ndarray, lo: float, hi: float) -> tuple[np.ndarray, np.ndarray]:
    """Rising/falling crossing positions with hysteresis (fractional sample index)."""
    state = np.zeros(len(x), dtype=np.int8)
    state[x >= hi] = 1
    state[x <= lo] = -1
    known = np.flatnonzero(state)
    if known.size < 2:
        return np.empty(0), np.empty(0)
    st = state[known]
    change = np.flatnonzero(np.diff(st) != 0)
    mid = (lo + hi) / 2
    rising, falling = [], []
    for c in change:
        a, b = known[c], known[c + 1]
        # interpolate the midpoint crossing between a and b
        seg = x[a : b + 1]
        if st[c + 1] > 0:
            k = int(np.argmax(seg >= mid))
        else:
            k = int(np.argmax(seg <= mid))
        k = max(k, 1)
        x0, x1 = seg[k - 1], seg[k]
        frac = (mid - x0) / (x1 - x0) if x1 != x0 else 0.0
        pos = a + k - 1 + frac
        (rising if st[c + 1] > 0 else falling).append(pos)
    return np.asarray(rising), np.asarray(falling)


def measure(x: np.ndarray, samplerate: float, lsb: float = 0.0) -> Measurement:
    m = Measurement()
    x = x[np.isfinite(x)]
    if x.size < 4:
        return m
    m.vmax = float(x.max())
    m.vmin = float(x.min())
    m.vpp = m.vmax - m.vmin
    m.mean = float(x.mean())
    m.rms = float(np.sqrt(np.mean(x * x)))
    m.ac_rms = float(x.std())
    if m.vpp <= max(4 * lsb, 1e-9):
        return m
    lo = m.vmin + 0.4 * m.vpp
    hi = m.vmin + 0.6 * m.vpp
    rising, falling = _crossings(x, lo, hi)
    edges = rising if rising.size >= falling.size else falling
    if edges.size < 2:
        edges = falling if edges is rising else rising
    if edges.size >= 2:
        period = (edges[-1] - edges[0]) / (edges.size - 1) / samplerate
        m.period = period
        m.freq = 1.0 / period
    if rising.size >= 2:
        # duty cycle: high time between the first and last rising edge
        f_in = falling[(falling > rising[0]) & (falling < rising[-1])]
        if f_in.size == rising.size - 1:
            m.duty = float(np.mean(f_in - rising[:-1]) / ((rising[-1] - rising[0]) / (rising.size - 1)))
    elif rising.size == 1 and falling.size >= 2 and math.isfinite(m.period):
        # one full low phase framed by two falling edges
        r = rising[0]
        if falling[0] < r < falling[-1]:
            nxt = falling[falling > r][0]
            m.duty = float((nxt - r) / (m.period * samplerate))
    # 10 % / 90 % edge times around the first full edge
    p10 = m.vmin + 0.1 * m.vpp
    p90 = m.vmin + 0.9 * m.vpp
    m.rise = _edge_time(x, p10, p90, samplerate)
    m.fall = _edge_time(-x, -p90, -p10, samplerate)
    return m


def _edge_time(x: np.ndarray, lo: float, hi: float, sr: float) -> float:
    below = np.flatnonzero(x <= lo)
    if below.size == 0:
        return math.nan
    above = np.flatnonzero(x >= hi)
    times = []
    # walk through a few edges for a stable result
    start = 0
    for _ in range(8):
        b = below[below >= start]
        if b.size == 0:
            break
        a = above[above > b[0]]
        if a.size == 0:
            break
        # last sample below `lo` before reaching `hi`
        b_last = below[(below >= b[0]) & (below < a[0])][-1]
        times.append((a[0] - b_last) / sr)
        start = a[0]
    return float(np.median(times)) if times else math.nan


# --------------------------------------------------------------- spectrum


def window(name: str, n: int) -> np.ndarray:
    if name == "Hann":
        return np.hanning(n)
    if name == "Hamming":
        return np.hamming(n)
    if name == "Blackman":
        return np.blackman(n)
    if name == "Flat top":
        k = np.arange(n) / max(n - 1, 1) * 2 * np.pi
        return 0.21557895 - 0.41663158 * np.cos(k) + 0.277263158 * np.cos(2 * k) - 0.083578947 * np.cos(3 * k) + 0.006947368 * np.cos(4 * k)
    return np.ones(n)


def spectrum(x: np.ndarray, samplerate: float, win: str = "Hann") -> tuple[np.ndarray, np.ndarray]:
    """Return (frequencies, magnitude in dBV rms)."""
    x = x[np.isfinite(x)]
    n = len(x)
    if n < 16:
        return np.empty(0), np.empty(0)
    w = window(win, n)
    spec = np.fft.rfft(x * w)
    amp = 2.0 * np.abs(spec) / w.sum()
    amp[0] /= 2.0
    freqs = np.fft.rfftfreq(n, 1.0 / samplerate)
    return freqs, 20.0 * np.log10(np.maximum(amp / math.sqrt(2), 1e-9))


# --------------------------------------------------------------- math channel


def math_channel(mode: int, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    n = min(len(a), len(b))
    a, b = a[:n], b[:n]
    if mode == 0:
        return a + b
    if mode == 1:
        return a - b
    if mode == 2:
        return b - a
    return a * b


# --------------------------------------------------------------- display


def minmax_decimate(x: np.ndarray, y: np.ndarray, buckets: int) -> tuple[np.ndarray, np.ndarray]:
    """Reduce a trace to min/max pairs per bucket, keeping peaks and glitches visible."""
    n = len(y)
    if n <= 2 * buckets:
        return x, y
    size = -(-n // buckets)  # ceil
    buckets = -(-n // size)
    yb = np.full(buckets * size, np.nan)
    yb[:n] = y
    yb = yb.reshape(buckets, size)
    lo = np.argmin(np.where(np.isnan(yb), np.inf, yb), axis=1)
    hi = np.argmax(np.where(np.isnan(yb), -np.inf, yb), axis=1)
    base = np.arange(buckets) * size
    idx = np.empty(2 * buckets, dtype=np.int64)
    idx[0::2] = base + np.minimum(lo, hi)
    idx[1::2] = base + np.maximum(lo, hi)
    idx = np.minimum(idx, n - 1)
    return x[idx], y[idx]


def sinc_interpolate(y: np.ndarray, factor: int) -> np.ndarray:
    """Band-limited interpolation via FFT zero padding."""
    n = len(y)
    if n < 4 or factor <= 1:
        return y
    spec = np.fft.rfft(y)
    return np.fft.irfft(spec, n * factor) * factor


def si_format(value: float, unit: str = "", digits: int = 3) -> str:
    """Format a number with SI prefix, e.g. 0.0012 V -> '1.20 mV'."""
    if value is None or not math.isfinite(value):
        return "—"
    if value == 0:
        return f"0 {unit}".strip()
    prefixes = [(1e9, "G"), (1e6, "M"), (1e3, "k"), (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")]
    av = abs(value)
    for scale, p in prefixes:
        if av >= scale * 0.9995:
            break
    v = value / scale
    decimals = max(0, digits - 1 - int(math.floor(math.log10(abs(v)))) if v else 0)
    return f"{v:.{decimals}f} {p}{unit}".strip()
