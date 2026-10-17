"""The oscilloscope screen: graticule, traces, markers and cursors."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtGui import QFont

from ..mathexpr import unit_for
from ..processing import si_format
from ..settings import CH_NAMES, DIVS_X, DIVS_Y, Settings
from . import theme
from .extra_views import BusLane, Phosphor

pg.setConfigOptions(antialias=True, foreground=theme.TEXT, background=theme.SCREEN_BG)

HX = DIVS_X / 2
HY = DIVS_Y / 2
NAMES = CH_NAMES + ["M"]


def _pen(c, width=1.0, style=Qt.SolidLine, alpha=255):
    col = pg.mkColor(c)
    col.setAlpha(alpha)
    return pg.mkPen(col, width=width, style=style)


class ScopeView(pg.PlotWidget):
    trigger_level_moved = Signal(float)  # volts
    trigger_position_moved = Signal(float)  # 0..1
    offset_moved = Signal(int, float)  # trace (0, 1, 2=math), divisions
    wheel_steps = Signal(int, object)  # steps, keyboard modifiers
    zoom_moved = Signal(float, float)  # region start/end in divisions

    def __init__(self, parent=None):
        super().__init__(parent)
        self.settings: Settings | None = None
        p = self.getPlotItem()
        p.hideAxis("left")
        p.hideAxis("bottom")
        p.hideButtons()
        p.setMenuEnabled(False)
        p.setMouseEnabled(False, False)
        p.layout.setContentsMargins(6, 6, 6, 6)
        self.vb = p.getViewBox()
        self.vb.setRange(xRange=(0, DIVS_X), yRange=(-HY, HY), padding=0)
        self.vb.disableAutoRange()
        self.vb.setBorder(_pen(theme.GRID_CENTER))
        self.setMinimumHeight(220)
        self._build_graticule()

        self.phosphor = Phosphor(p)
        self.phosphor.set_visible(False)
        self.bus = BusLane(p)
        self.xy_curve = pg.PlotDataItem(pen=_pen(theme.XY_COLOR, 1.4), connect="finite")
        self.xy_curve.setZValue(14)
        self.xy_curve.setVisible(False)
        p.addItem(self.xy_curve)
        self.zoom_region = pg.LinearRegionItem(
            (4, 6), brush=pg.mkBrush(79, 140, 255, 28), hoverBrush=pg.mkBrush(79, 140, 255, 50),
            pen=_pen(theme.ACCENT, 1, alpha=160), bounds=(0, DIVS_X))
        self.zoom_region.setZValue(3)
        self.zoom_region.setVisible(False)
        self.zoom_region.sigRegionChanged.connect(lambda r: self.zoom_moved.emit(*r.getRegion()))
        p.addItem(self.zoom_region)
        self.xy_label = pg.TextItem(anchor=(1, 1), color=theme.MUTED)
        self.xy_label.setPos(DIVS_X - 0.1, -HY + 0.1)
        self.xy_label.setZValue(30)
        p.addItem(self.xy_label)

        self.curves = []
        for i in range(3):
            c = pg.PlotDataItem(pen=_pen(theme.CH_COLORS[i], 1.4), connect="finite", skipFiniteCheck=False)
            c.setZValue(10 + i)
            p.addItem(c)
            self.curves.append(c)

        # ground (offset) markers
        self.ground = []
        for i in range(3):
            line = pg.InfiniteLine(
                0,
                angle=0,
                movable=True,
                pen=_pen(theme.CH_COLORS[i], 1, Qt.DashLine, 70),
                hoverPen=_pen(theme.CH_COLORS[i], 1.5),
                label=f" {NAMES[i][-1] if i < 2 else 'M'} ▸",
                labelOpts={"position": 0.0, "anchors": [(0, 0.5), (0, 0.5)], "color": "#000", "fill": theme.CH_COLORS[i]},
            )
            line.setBounds((-HY, HY))
            line.setZValue(5)
            line.sigDragged.connect(lambda l, i=i: self.offset_moved.emit(i, l.value()))
            p.addItem(line)
            self.ground.append(line)

        self.trig_level = pg.InfiniteLine(
            0,
            angle=0,
            movable=True,
            pen=_pen("#ffffff", 1, Qt.DotLine, 90),
            hoverPen=_pen("#ffffff", 1.5),
            label="◂ T",
            labelOpts={"position": 1.0, "anchors": [(1, 0.5), (1, 0.5)], "color": "#000", "fill": "#ffffff"},
        )
        self.trig_level.setBounds((-HY, HY))
        self.trig_level.setZValue(20)
        self.trig_level.sigDragged.connect(self._on_trig_level)
        p.addItem(self.trig_level)

        self.trig_pos = pg.InfiniteLine(
            HX,
            angle=90,
            movable=True,
            pen=_pen(theme.ACCENT, 1, Qt.DotLine, 120),
            hoverPen=_pen(theme.ACCENT, 1.5),
            label="T ▾",
            labelOpts={"position": 1.0, "anchors": [(0.5, 0), (0.5, 0)], "color": "#fff", "fill": theme.ACCENT},
        )
        self.trig_pos.setBounds((0, DIVS_X))
        self.trig_pos.setZValue(20)
        self.trig_pos.sigDragged.connect(lambda l: self.trigger_position_moved.emit(l.value() / DIVS_X))
        p.addItem(self.trig_pos)

        # cursors
        cur_pen = _pen("#cbd5e1", 1, Qt.DashLine, 160)
        hov = _pen("#ffffff", 1.5)
        self.cursors_x = [
            pg.InfiniteLine(x, angle=90, movable=True, pen=cur_pen, hoverPen=hov, label=n,
                            labelOpts={"position": 0.04, "color": "#cbd5e1"})
            for x, n in ((3, "a"), (7, "b"))
        ]
        self.cursors_y = [
            pg.InfiniteLine(y, angle=0, movable=True, pen=cur_pen, hoverPen=hov, label=n,
                            labelOpts={"position": 0.96, "color": "#cbd5e1"})
            for y, n in ((1.5, "c"), (-1.5, "d"))
        ]
        for c in self.cursors_x:
            c.setBounds((0, DIVS_X))
            c.sigPositionChanged.connect(self._update_cursor_text)
        for c in self.cursors_y:
            c.setBounds((-HY, HY))
            c.sigPositionChanged.connect(self._update_cursor_text)
        for c in self.cursors_x + self.cursors_y:
            c.setZValue(25)
            c.setVisible(False)
            p.addItem(c)

        mono = QFont("monospace")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSizeF(9)
        self.badge = pg.TextItem(anchor=(0, 0))
        self.badge.setPos(0.08, HY - 0.08)
        self.badge.setZValue(30)
        p.addItem(self.badge)
        self.cursor_text = pg.TextItem(anchor=(1, 1), color="#cbd5e1", fill=pg.mkBrush(7, 9, 12, 200))
        self.cursor_text.setFont(mono)
        self.cursor_text.setPos(DIVS_X - 0.08, -HY + 0.08)
        self.cursor_text.setZValue(30)
        self.cursor_text.setVisible(False)
        p.addItem(self.cursor_text)
        self.hover = pg.TextItem(anchor=(0, 1), color=theme.MUTED)
        self.hover.setFont(mono)
        self.hover.setPos(0.08, -HY + 0.08)
        self.hover.setZValue(30)
        p.addItem(self.hover)
        self.scene().sigMouseMoved.connect(self._on_mouse)

    # ------------------------------------------------------------ graticule

    def _build_graticule(self):
        p = self.getPlotItem()
        xs, ys = [], []
        for x in range(1, DIVS_X):
            xs += [x, x]
            ys += [-HY, HY]
        for y in range(-int(HY) + 1, int(HY)):
            xs += [0, DIVS_X]
            ys += [y, y]
        grid = pg.PlotCurveItem(np.array(xs, float), np.array(ys, float), connect="pairs", pen=_pen(theme.GRID, 1, Qt.DotLine))
        p.addItem(grid)
        # center axes with minor ticks every 0.2 div
        tx, ty = [], []
        for x in np.arange(0, DIVS_X + 1e-9, 0.2):
            tx += [x, x]
            ty += [-0.08, 0.08]
        for y in np.arange(-HY, HY + 1e-9, 0.2):
            tx += [HX - 0.08, HX + 0.08]
            ty += [y, y]
        tx += [0, DIVS_X, HX, HX]
        ty += [0, 0, -HY, HY]
        ticks = pg.PlotCurveItem(np.array(tx), np.array(ty), connect="pairs", pen=_pen(theme.GRID_CENTER, 1))
        p.addItem(ticks)

    # ------------------------------------------------------------ updates

    def apply_settings(self, s: Settings) -> None:
        self.settings = s
        xy = s.xy_mode
        for i in range(2):
            ch = s.channels[i]
            self.ground[i].setVisible(ch.enabled and not xy)
            if not self.ground[i].moving:
                self.ground[i].setValue(ch.offset)
        self.ground[2].setVisible(s.math_enabled and not xy)
        if not self.ground[2].moving:
            self.ground[2].setValue(s.math_offset)
        t = s.trigger
        roll = t.mode == "Roll"
        src = s.channels[t.source]
        col = theme.CH_COLORS[t.source]
        self.trig_level.setVisible(not roll and src.enabled and not xy)
        self.trig_level.setPen(_pen(col, 1, Qt.DotLine, 110))
        self.trig_level.label.fill = pg.mkBrush(col)
        self.trig_level.label.update()
        if not self.trig_level.moving:
            self.trig_level.setValue(max(-HY, min(HY, t.level / src.vdiv + src.offset)))
        self.trig_pos.setVisible(not roll and not xy)
        if not self.trig_pos.moving:
            self.trig_pos.setValue(t.position * DIVS_X)
        for c in self.cursors_x + self.cursors_y:
            c.setVisible(s.cursors and not xy)
        self.cursor_text.setVisible(s.cursors and not xy)
        self._update_cursor_text()
        self.xy_curve.setVisible(xy)
        self.xy_label.setText("XY  ·  X = CH1   Y = CH2" if xy else "")
        for c in self.curves:
            if xy:
                c.setVisible(False)
        self.zoom_region.setVisible(s.zoom_enabled and not xy)
        if s.zoom_enabled and not self.zoom_region.mouseHovering:
            self.zoom_region.blockSignals(True)
            self.zoom_region.setRegion((s.zoom_start, s.zoom_end))
            self.zoom_region.blockSignals(False)
        if not (s.decoder.enabled and not xy):
            self.bus.clear()

    def set_xy(self, xy) -> None:
        if xy is None:
            self.xy_curve.setData([], [])
        else:
            self.xy_curve.setData(*xy)

    def px_per_div(self) -> float:
        return self.vb.width() / DIVS_X if self.vb.width() > 0 else 80.0

    def set_traces(self, traces, dots: bool = False) -> None:
        """traces: list of 3 entries, each (x_div, y_div) or None."""
        for curve, i, tr in zip(self.curves, range(3), traces):
            if tr is None:
                curve.setData([], [])
                curve.setVisible(False)
                continue
            x, y = tr
            curve.setVisible(True)
            if dots:
                curve.setData(x, y, pen=None, symbol="o", symbolSize=3, symbolPen=None,
                              symbolBrush=pg.mkBrush(theme.CH_COLORS[i]))
            else:
                curve.setData(x, y, pen=_pen(theme.CH_COLORS[i], 1.4), symbol=None)

    def set_badge(self, text: str, color: str) -> None:
        self.badge.setHtml(
            f'<span style="background:{color}; color:#05070a; font-weight:700; padding:2px 6px;">&nbsp;{text}&nbsp;</span>'
        )

    # ------------------------------------------------------------ interaction

    def _on_trig_level(self, line):
        s = self.settings
        if s is None:
            return
        src = s.channels[s.trigger.source]
        self.trigger_level_moved.emit((line.value() - src.offset) * src.vdiv)

    def _volts(self, y_div: float, i: int) -> float:
        s = self.settings
        if i < 2:
            return (y_div - s.channels[i].offset) * s.channels[i].vdiv
        return (y_div - s.math_offset) * s.math_vdiv

    def _time(self, x_div: float) -> float:
        s = self.settings
        return (x_div - s.trigger.position * DIVS_X) * s.timebase

    def _update_cursor_text(self, *_):
        s = self.settings
        if s is None or not s.cursors:
            return
        xa, xb = (c.value() for c in self.cursors_x)
        yc, yd = (c.value() for c in self.cursors_y)
        dt = abs(xb - xa) * s.timebase
        lines = [f"Δt   {si_format(dt, 's'):>10}", f"1/Δt {si_format(1 / dt if dt else float('nan'), 'Hz'):>10}"]
        for i in range(3):
            on = s.channels[i].enabled if i < 2 else s.math_enabled
            if on:
                dv = abs(self._volts(yc, i) - self._volts(yd, i))
                unit = (unit_for(s.math_expr) or "u") if i == 2 else "V"
                lines.append(f"ΔV{NAMES[i][-1] if i < 2 else 'M'}  {si_format(dv, unit):>10}")
        self.cursor_text.setText("\n".join(lines))

    def _on_mouse(self, pos: QPointF):
        s = self.settings
        if s is None or not self.vb.sceneBoundingRect().contains(pos):
            self.hover.setText("")
            return
        pt = self.vb.mapSceneToView(pos)
        parts = [f"t {si_format(self._time(pt.x()), 's')}"]
        for i in range(2):
            if s.channels[i].enabled:
                parts.append(f"{CH_NAMES[i]} {si_format(self._volts(pt.y(), i), 'V')}")
        self.hover.setText("   ".join(parts))

    def leaveEvent(self, ev):
        self.hover.setText("")
        super().leaveEvent(ev)

    def wheelEvent(self, ev):
        steps = 1 if ev.angleDelta().y() > 0 else -1
        self.wheel_steps.emit(steps, ev.modifiers())
        ev.accept()


class FreqAxis(pg.AxisItem):
    """Log frequency axis that only labels decades, with SI units."""

    def logTickStrings(self, values, scale, spacing):
        return [si_format(10**v, "Hz") if abs(v - round(v)) < 1e-6 else "" for v in values]


class SpectrumView(pg.PlotWidget):
    def __init__(self, parent=None):
        super().__init__(parent, axisItems={"bottom": FreqAxis("bottom")})
        p = self.getPlotItem()
        p.showGrid(x=True, y=True, alpha=0.18)
        p.setMenuEnabled(False)
        p.hideButtons()
        p.setLabel("left", "dBV")
        p.setLabel("bottom", "Frequency")
        for ax in ("left", "bottom"):
            a = p.getAxis(ax)
            a.setPen(_pen(theme.GRID_CENTER))
            a.setTextPen(_pen(theme.MUTED))
        p.setMouseEnabled(x=True, y=False)
        p.setLogMode(x=True, y=False)
        p.setYRange(-100, 20, padding=0)
        self.setMinimumHeight(140)
        self.phosphor = Phosphor(p)
        self.phosphor.set_visible(False)
        self.bus = BusLane(p)
        self.xy_curve = pg.PlotDataItem(pen=_pen(theme.XY_COLOR, 1.4), connect="finite")
        self.xy_curve.setZValue(14)
        self.xy_curve.setVisible(False)
        p.addItem(self.xy_curve)
        self.zoom_region = pg.LinearRegionItem(
            (4, 6), brush=pg.mkBrush(79, 140, 255, 28), hoverBrush=pg.mkBrush(79, 140, 255, 50),
            pen=_pen(theme.ACCENT, 1, alpha=160), bounds=(0, DIVS_X))
        self.zoom_region.setZValue(3)
        self.zoom_region.setVisible(False)
        self.zoom_region.sigRegionChanged.connect(lambda r: self.zoom_moved.emit(*r.getRegion()))
        p.addItem(self.zoom_region)
        self.xy_label = pg.TextItem(anchor=(1, 1), color=theme.MUTED)
        self.xy_label.setPos(DIVS_X - 0.1, -HY + 0.1)
        self.xy_label.setZValue(30)
        p.addItem(self.xy_label)

        self.curves = []
        for i in range(3):
            c = p.plot(pen=_pen(theme.SPECTRUM_COLORS[i], 1.1))
            self.curves.append(c)
        self.peak = pg.TextItem(anchor=(0, 1), color=theme.TEXT)
        p.addItem(self.peak)
        self._nyquist = 0.0

    def set_spectra(self, spectra, nyquist: float) -> None:
        best = None
        for i, (curve, sp) in enumerate(zip(self.curves, spectra)):
            if sp is None:
                curve.setData([], [])
                continue
            f, db = sp
            f, db = f[1:], db[1:]  # skip DC for the log axis
            curve.setData(f, db)
            if len(db) > 2:
                k = int(np.argmax(db))
                if best is None or db[k] > best[2]:
                    best = (i, f[k], db[k])
        if nyquist != self._nyquist:
            self._nyquist = nyquist
            lo = max(nyquist / 1e4, 1.0)
            self.getPlotItem().setXRange(np.log10(lo), np.log10(nyquist), padding=0)
            self.getPlotItem().setLimits(xMin=np.log10(lo) - 1, xMax=np.log10(nyquist))
        if best:
            i, f, db = best
            self.peak.setPos(np.log10(max(f, 1e-3)), db)
            self.peak.setHtml(f'<span style="color:{theme.SPECTRUM_COLORS[i]}">▾ {si_format(f, "Hz")}  {db:.1f} dBV</span>')
        else:
            self.peak.setText("")
