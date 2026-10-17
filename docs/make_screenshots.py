"""Render the README screenshots with the demo device.

Usage:  QT_QPA_PLATFORM=offscreen uv run python docs/make_screenshots.py docs/screenshots
"""
import sys
from PySide6.QtCore import QCoreApplication, QSettings, QTimer
from PySide6.QtWidgets import QApplication

QCoreApplication.setOrganizationName("pyhantek-test")
QCoreApplication.setApplicationName("readme-shots")
QSettings().clear()
app = QApplication([])
from pyhantek.ui import theme
from pyhantek.ui.mainwindow import MainWindow
from pyhantek.device import DemoDevice

theme.apply(app)
w = MainWindow(DemoDevice(), mcp_enabled=False)
w.resize(1440, 900)
w.show()
out = sys.argv[1]
s = w.settings


def apply():
    w.panel.refresh()
    w._settings_changed()
    w.banner.hide()


def main_view():
    s.calfreq = 1000.0
    s.timebase = 500e-6
    s.channels[0].vdiv, s.channels[0].offset = 1.0, 1.6
    s.channels[1].vdiv, s.channels[1].offset = 1.0, -2.6
    s.cursors = True
    apply()


def decoding():
    s.cursors = False
    w.set_demo_signal("i2c")
    s.timebase = 50e-6
    s.channels[0].vdiv, s.channels[0].offset = 2.0, 0.6
    s.channels[1].vdiv, s.channels[1].offset = 2.0, -2.4
    s.trigger.source, s.trigger.slope, s.trigger.level, s.trigger.position = 1, "Falling", 1.6, 0.1
    s.decoder.enabled, s.decoder.protocol, s.decoder.format = True, "I2C", "Hex"
    apply()
    w.tabs.setCurrentIndex(1)


def xy_persist():
    w.tabs.setCurrentIndex(0)
    s.decoder.enabled = False
    w.set_demo_signal("lissajous")
    s.timebase = 1e-3
    s.xy_mode = True
    s.persistence = 5.0
    for ch in s.channels:
        ch.vdiv, ch.offset = 0.5, 0.0
    s.trigger.source, s.trigger.slope, s.trigger.level, s.trigger.position = 0, "Rising", 0.0, 0.5
    apply()


def spectrum_view():
    w.set_demo_signal("sine")
    s.xy_mode, s.persistence = False, 0.0
    s.timebase = 1e-3
    s.channels[0].vdiv, s.channels[0].offset = 1.0, 1.5
    s.channels[1].vdiv, s.channels[1].offset = 1.0, -2.5
    s.spectrum_enabled, s.spectrum_view, s.spectrum_window = True, "Both", "Hann"
    s.spectrum_channels = [True, False, False]
    apply()


def math_zoom():
    s.spectrum_enabled = False
    s.timebase = 500e-6
    s.channels[1].enabled = False
    s.channels[0].offset = 1.6
    s.math_enabled, s.math_expr, s.math_vdiv, s.math_offset = True, "diff(lowpass(ch1, fs / 50))", 1e4, -2.0
    s.zoom_enabled, s.zoom_start, s.zoom_end = True, 4.6, 5.6
    s.persistence = 1.0
    apply()


shots = [("main", main_view, 4000), ("decoding", decoding, 4000), ("xy-persistence", xy_persist, 6000),
         ("spectrum", spectrum_view, 14000), ("math-zoom", math_zoom, 5000)]
k = [0]


def run():
    if k[0] > 0:
        name = shots[k[0] - 1][0]
        w.grab().save(f"{out}/{name}.png")
        print("saved", name, flush=True)
    if k[0] < len(shots):
        name, fn, wait = shots[k[0]]
        fn()
        k[0] += 1
        QTimer.singleShot(wait, run)
    else:
        w.close()
        app.quit()


QTimer.singleShot(1500, run)
app.exec()
