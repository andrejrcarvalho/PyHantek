"""Persistence (phosphor), decoder bus lane, zoom window and spectrogram."""

from __future__ import annotations

import math
import time

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from ..processing import si_format
from ..settings import DIVS_X, DIVS_Y
from . import theme

HY = DIVS_Y / 2


# ------------------------------------------------------------------ persistence


class Phosphor:
    """Accumulates traces into an intensity image that fades over time, like an analog scope."""

    def __init__(self, plot: pg.PlotItem, width: int = 1000, height: int = 400):
        self.w, self.h = width, height
        self.buf = np.zeros((4, height, width), np.float32)  # CH1, CH2, Math, XY
        self.item = pg.ImageItem(axisOrder="row-major")
        self.item.setZValue(2)
        plot.addItem(self.item)
        self.last = None
        self.colors = np.array([pg.mkColor(c).getRgbF()[:3] for c in (*theme.CH_COLORS, theme.XY_COLOR)], np.float32)

    def clear(self):
        self.buf[:] = 0
        self.last = None
        self.item.clear()

    def set_visible(self, on: bool):
        self.item.setVisible(on)
        if not on:
            self.clear()

    def add(self, traces, tau: float):
        """traces: list of (x_div, y_div) or None; tau = decay time constant in s (<0 = infinite)."""
        now = time.monotonic()
        if self.last is not None and tau > 0:
            self.buf *= math.exp(-(now - self.last) / tau)
        self.last = now
        for i, tr in enumerate(traces):
            if tr is None:
                continue
            self._raster(i, *tr)
        self._render()

    def _raster(self, i, x, y):
        x = np.asarray(x, np.float64)
        y = np.asarray(y, np.float64)
        ok = np.isfinite(x) & np.isfinite(y)
        x, y = x[ok], y[ok]
        if x.size < 2:
            return
        cx = np.clip(np.round(x / DIVS_X * (self.w - 1)), 0, self.w - 1).astype(np.int64)
        cy = np.clip(np.round((y + HY) / DIVS_Y * (self.h - 1)), 0, self.h - 1).astype(np.int64)
        # connect consecutive points: each segment becomes max(|dx|,|dy|)+1 pixels
        dx = np.diff(cx)
        dy = np.diff(cy)
        steps = np.maximum(np.abs(dx), np.abs(dy)) + 1
        total = int(steps.sum())
        if total > 4_000_000:  # absurdly dense; just plot the points
            np.add.at(self.buf[i], (cy, cx), 1.0)
            return
        seg = np.repeat(np.arange(len(steps)), steps)
        offs = np.arange(total) - np.repeat(np.cumsum(steps) - steps, steps)
        frac = offs / np.maximum(steps[seg] - 1, 1)
        px = np.round(cx[seg] + frac * dx[seg]).astype(np.int64)
        py = np.round(cy[seg] + frac * dy[seg]).astype(np.int64)
        flat = np.bincount(py * self.w + px, minlength=self.w * self.h).astype(np.float32)
        self.buf[i] += np.minimum(flat.reshape(self.h, self.w), 4.0)

    def _render(self):
        a = 1.0 - np.exp(-self.buf * 0.5)  # (4, h, w) 0..1
        alpha = a.max(axis=0)
        rgb = np.einsum("chw,ck->hwk", a, self.colors) / np.maximum(a.sum(axis=0), 1e-6)[..., None]
        img = np.empty((self.h, self.w, 4), np.uint8)
        img[..., :3] = np.clip(rgb * 255, 0, 255)
        img[..., 3] = np.clip(alpha * 235, 0, 255)
        self.item.setImage(img, autoLevels=False, levels=(0, 255), rect=(0, -HY, DIVS_X, DIVS_Y))


# ------------------------------------------------------------------ decoder bus lane


KIND_COLORS = {
    "data": "#38bdf8",
    "addr": "#f59e0b",
    "start": "#22c55e",
    "stop": "#ef4444",
    "ack": "#64748b",
    "nak": "#ef4444",
    "error": "#ef4444",
}


class BusLane:
    """Draws decoded protocol annotations as labelled boxes in a lane near the bottom of a plot."""

    def __init__(self, plot: pg.PlotItem, y: float = -HY + 0.55, height: float = 0.5, max_labels: int = 160):
        self.plot = plot
        self.y = y
        self.hh = height / 2
        self.outline = pg.PlotCurveItem(connect="pairs", pen=pg.mkPen("#38bdf8", width=1))
        self.outline.setZValue(28)
        plot.addItem(self.outline)
        self.fills = {}
        self.labels: list[pg.TextItem] = []
        font = QFont("monospace")
        font.setStyleHint(QFont.Monospace)
        font.setPointSizeF(8)
        for _ in range(max_labels):
            t = pg.TextItem(anchor=(0.5, 0.5), color="#e6e8ee")
            t.setFont(font)
            t.setZValue(29)
            t.setVisible(False)
            plot.addItem(t)
            self.labels.append(t)
        self.name = pg.TextItem(anchor=(0, 0.5), color=theme.MUTED)
        self.name.setFont(font)
        self.name.setZValue(29)
        self.name.setPos(0.05, y + self.hh + 0.18)
        plot.addItem(self.name)

    def clear(self):
        self.outline.setData([], [])
        for t in self.labels:
            t.setVisible(False)
        self.name.setText("")

    def set_annotations(self, items, title: str, px_per_unit: float, x_range=(0, DIVS_X)):
        """items: list of (x0, x1, kind, text) in plot x units."""
        self.name.setText(title)
        x0r, x1r = x_range
        vis = [it for it in items if it[1] >= x0r and it[0] <= x1r]
        xs, ys = [], []
        y, h = self.y, self.hh
        for a, b, kind, _ in vis[:2000]:
            a, b = max(a, x0r), min(b, x1r)
            e = min((b - a) * 0.15, 0.05)
            # hexagon-ish box
            pts = [(a, y), (a + e, y + h), (b - e, y + h), (b, y), (b - e, y - h), (a + e, y - h), (a, y)]
            for p, q in zip(pts[:-1], pts[1:]):
                xs += [p[0], q[0]]
                ys += [p[1], q[1]]
        self.outline.setData(np.array(xs, float), np.array(ys, float))
        used = 0
        for a, b, kind, text in vis:
            if used >= len(self.labels):
                break
            width_px = (min(b, x1r) - max(a, x0r)) * px_per_unit
            if width_px < 7.5 * len(text) + 4:
                continue  # too narrow for the text
            t = self.labels[used]
            t.setText(text, color=KIND_COLORS.get(kind, "#e6e8ee"))
            t.setPos((max(a, x0r) + min(b, x1r)) / 2, y)
            t.setVisible(True)
            used += 1
        for t in self.labels[used:]:
            t.setVisible(False)


# ------------------------------------------------------------------ zoom window


def _pen(c, width=1.0, style=Qt.SolidLine, alpha=255):
    col = pg.mkColor(c)
    col.setAlpha(alpha)
    return pg.mkPen(col, width=width, style=style)


class ZoomView(pg.PlotWidget):
    """Magnified view of the region selected on the main screen."""

    def __init__(self, parent=None):
        super().__init__(parent)
        p = self.getPlotItem()
        p.hideAxis("left")
        p.hideAxis("bottom")
        p.hideButtons()
        p.setMenuEnabled(False)
        p.setMouseEnabled(False, False)
        p.layout.setContentsMargins(6, 6, 6, 6)
        self.vb = p.getViewBox()
        self.vb.disableAutoRange()
        self.vb.setBorder(_pen(theme.ACCENT, 1, alpha=150))
        self.setMinimumHeight(130)
        self.grid = pg.PlotCurveItem(connect="pairs", pen=_pen(theme.GRID, 1, Qt.DotLine))
        p.addItem(self.grid)
        self.axis = pg.PlotCurveItem(connect="pairs", pen=_pen(theme.GRID_CENTER, 1))
        p.addItem(self.axis)
        self.curves = []
        for i in range(3):
            c = pg.PlotDataItem(pen=_pen(theme.CH_COLORS[i], 1.4), connect="finite")
            c.setZValue(10 + i)
            p.addItem(c)
            self.curves.append(c)
        self.label = pg.TextItem(anchor=(0, 0), color=theme.ACCENT)
        self.label.setZValue(30)
        p.addItem(self.label)
        self.bus = BusLane(p, max_labels=60)
        self.region = (4.0, 6.0)

    def set_region(self, a: float, b: float, timebase: float):
        self.region = (a, b)
        self.vb.setRange(xRange=(a, b), yRange=(-HY, HY), padding=0)
        xs, ys = [], []
        step = (b - a) / DIVS_X
        for k in range(1, DIVS_X):
            xs += [a + k * step] * 2
            ys += [-HY, HY]
        for yv in range(-int(HY) + 1, int(HY)):
            xs += [a, b]
            ys += [yv, yv]
        self.grid.setData(np.array(xs, float), np.array(ys, float))
        self.axis.setData(np.array([a, b, (a + b) / 2, (a + b) / 2], float), np.array([0, 0, -HY, HY], float))
        zoom = DIVS_X / max(b - a, 1e-9)
        self.label.setPos(a + step * 0.08, HY - 0.08)
        self.label.setText(f"ZOOM ×{zoom:.1f}  ·  {si_format(timebase * step, 's')}/div")

    def set_traces(self, traces):
        a, b = self.region
        for curve, tr in zip(self.curves, traces):
            if tr is None:
                curve.setData([], [])
                continue
            x, y = tr
            k = (x >= a - (b - a) * 0.02) & (x <= b + (b - a) * 0.02)
            curve.setData(x[k], y[k])


# ------------------------------------------------------------------ spectrogram


class SpectrogramView(pg.PlotWidget):
    """Waterfall of FFT history: frequency on a log x axis, newest spectrum at the top."""

    ROWS = 100
    COLS = 600

    def __init__(self, parent=None):
        from .scope_view import FreqAxis

        super().__init__(parent, axisItems={"bottom": FreqAxis("bottom")})
        p = self.getPlotItem()
        p.setMenuEnabled(False)
        p.hideButtons()
        p.setMouseEnabled(False, False)
        p.setLogMode(x=True, y=False)
        p.setLabel("bottom", "Frequency")
        p.setLabel("left", "History")
        p.getAxis("left").setStyle(showValues=False)
        for ax in ("left", "bottom"):
            a = p.getAxis(ax)
            a.setPen(_pen(theme.GRID_CENTER))
            a.setTextPen(_pen(theme.MUTED))
        self.setMinimumHeight(140)
        self.img = pg.ImageItem(axisOrder="row-major")
        p.addItem(self.img)
        self.cmap = pg.colormap.get("inferno")
        self.lut = self.cmap.getLookupTable(0.0, 1.0, 256)
        self.data = np.full((self.ROWS, self.COLS), -120.0, np.float32)
        self.key = None
        self.title = pg.TextItem(anchor=(0, 0), color=theme.TEXT)
        self.title.setZValue(10)
        p.addItem(self.title)
        self.db_range = (-100.0, 10.0)

    def reset_history(self):
        # not called clear(): PlotWidget puts PlotItem.clear on each instance, which would remove our image
        self.data[:] = -120.0
        self.key = None

    def add(self, freqs: np.ndarray, db: np.ndarray, nyquist: float, label: str, color: str):
        lo = max(nyquist / 1e4, 1.0)
        key = (round(nyquist), label)
        if key != self.key:
            self.data[:] = -120.0
            self.key = key
            self.getPlotItem().setXRange(math.log10(lo), math.log10(nyquist), padding=0)
            self.getPlotItem().setYRange(0, self.ROWS, padding=0)
        # resample onto log-spaced columns, keeping peaks (max within each column)
        edges = np.logspace(math.log10(lo), math.log10(nyquist), self.COLS + 1)
        idx = np.searchsorted(freqs, edges)
        row = np.full(self.COLS, -120.0, np.float32)
        for c in range(self.COLS):
            a, b = idx[c], max(idx[c + 1], idx[c] + 1)
            if a < len(db):
                row[c] = db[a:b].max()
        # columns narrower than one FFT bin get interpolated values
        centers = np.sqrt(edges[:-1] * edges[1:])
        empty = (idx[1:] - idx[:-1]) == 0
        if empty.any():
            row[empty] = np.interp(centers[empty], freqs, db)
        self.data = np.roll(self.data, -1, axis=0)
        self.data[-1] = row
        lo_db, hi_db = self.db_range
        norm = np.clip((self.data - lo_db) / (hi_db - lo_db), 0, 1)
        self.img.setImage((norm * 255).astype(np.uint8), lut=self.lut, autoLevels=False, levels=(0, 255),
                          rect=(math.log10(lo), 0, math.log10(nyquist) - math.log10(lo), self.ROWS))
        self.title.setPos(math.log10(lo), self.ROWS)
        self.title.setHtml(f'<span style="color:{color}">{label}</span>')
