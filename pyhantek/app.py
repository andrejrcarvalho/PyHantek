"""Command line entry point."""

from __future__ import annotations

import argparse
import logging
import sys
import threading

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox, QProgressDialog, QWidget

from . import __version__
from .device import DemoDevice, DeviceError, ScopeDevice, find_devices, upload_firmware

log = logging.getLogger("pyhantek")


def _run_blocking(parent: QWidget | None, label: str, fn):
    """Run `fn(progress)` in a thread while showing a busy dialog."""
    dlg = QProgressDialog(label, None, 0, 0, parent)
    dlg.setWindowTitle("PyHantek")
    dlg.setMinimumDuration(0)
    dlg.setCancelButton(None)
    dlg.show()
    result: dict = {}
    messages: list[str] = []

    def work():
        try:
            result["value"] = fn(messages.append)
        except Exception as exc:  # reported to the user below
            result["error"] = exc

    t = threading.Thread(target=work, daemon=True)
    t.start()
    loop = QEventLoop()
    timer = QTimer(interval=50)

    def poll():
        if messages:
            dlg.setLabelText(messages[-1])
        if not t.is_alive():
            loop.quit()

    timer.timeout.connect(poll)
    timer.start()
    loop.exec()
    timer.stop()
    dlg.close()
    if "error" in result:
        raise result["error"]
    return result.get("value")


def open_scope(parent: QWidget | None = None, quiet: bool = False):
    """Find, prepare (firmware) and open a scope. Returns None if there is none."""
    found = find_devices()
    if not found:
        if not quiet:
            QMessageBox.information(parent, "PyHantek", "No supported scope found.\n\nCheck the USB cable and (on Linux) the udev rule.")
        return None
    choice = found[0]
    if len(found) > 1:
        labels = [f.label for f in found]
        item, ok = QInputDialog.getItem(parent, "Select scope", "Several scopes are connected:", labels, 0, False)
        if not ok:
            return None
        choice = found[labels.index(item)]
    try:
        if choice.needs_firmware:
            choice = _run_blocking(parent, "Uploading firmware…", lambda p: upload_firmware(choice, p))
        return ScopeDevice(choice)
    except DeviceError as exc:
        QMessageBox.warning(parent, "PyHantek", str(exc))
        return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pyhantek", description="Oscilloscope app for Hantek DSO-6022 family scopes")
    ap.add_argument("--demo", action="store_true", help="start with the simulated demo device")
    ap.add_argument("-v", "--verbose", action="count", default=0, help="more log output (-vv for debug)")
    ap.add_argument("--version", action="version", version=f"pyhantek {__version__}")
    args = ap.parse_args(argv)
    logging.basicConfig(level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
                        format="%(levelname)s %(name)s: %(message)s")

    QCoreApplication.setOrganizationName("pyhantek")
    QCoreApplication.setApplicationName("PyHantek")
    app = QApplication(sys.argv[:1])
    from .ui import theme
    from .ui.mainwindow import MainWindow

    theme.apply(app)
    device = None if args.demo else open_scope(None, quiet=True)
    if device is None:
        device = DemoDevice()
    win = MainWindow(device, reconnect=lambda parent: open_scope(parent))
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
