"""Render the PyHantek icon (PNG + multi-size ICO) with Qt."""
import math
import sys

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPainterPath, QPen

app = QGuiApplication(sys.argv[:1])


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 256
    m = 10 * s
    body = QRectF(m, m, size - 2 * m, size - 2 * m)
    p.setPen(QPen(QColor("#2a2f3a"), 4 * s))
    p.setBrush(QColor("#0f1115"))
    p.drawRoundedRect(body, 44 * s, 44 * s)
    scr = body.adjusted(26 * s, 34 * s, -26 * s, -34 * s)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#07090c"))
    p.drawRoundedRect(scr, 12 * s, 12 * s)
    if size >= 48:
        p.setPen(QPen(QColor("#2b3240"), max(1.0, 2 * s)))
        for k in range(1, 5):
            x = scr.left() + scr.width() * k / 5
            p.drawLine(QPointF(x, scr.top()), QPointF(x, scr.bottom()))
        for k in range(1, 4):
            y = scr.top() + scr.height() * k / 4
            p.drawLine(QPointF(scr.left(), y), QPointF(scr.right(), y))

    def trace(fn, color, width):
        path = QPainterPath()
        n = 200
        for i in range(n + 1):
            u = i / n
            x = scr.left() + 6 * s + u * (scr.width() - 12 * s)
            y = scr.center().y() + fn(u) * scr.height() * 0.36
            path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
        pen = QPen(QColor(color), width * s)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)

    trace(lambda u: 0.55 + 0.38 * (1 if (u * 2.5) % 1 < 0.5 else -1), "#22d3ee", 8)
    trace(lambda u: -0.25 - 0.62 * math.sin(2 * math.pi * 1.25 * u), "#facc15", 10)
    p.end()
    return img


out = sys.argv[1]
render(512).save(f"{out}/pyhantek.png")
# ICO with the standard Windows sizes (Qt's ico plugin writes one image per call, so write PNGs for PyInstaller)
for sz in (16, 24, 32, 48, 64, 128, 256):
    render(sz).save(f"{out}/icon_{sz}.png")
print("ok")
