"""Log file and crash reporting.

The Windows .exe has no console, so everything useful for diagnosing problems
goes to a log file:
  Windows: %LOCALAPPDATA%\\PyHantek\\pyhantek.log
  Linux:   ~/.local/state/pyhantek/pyhantek.log
  macOS:   ~/Library/Logs/PyHantek/pyhantek.log
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import sys
import traceback
from pathlib import Path

from . import __version__

log = logging.getLogger("pyhantek")


def log_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "PyHantek"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Logs" / "PyHantek"
    else:
        base = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "pyhantek"
    return base / "pyhantek.log"


def is_admin() -> bool | None:
    try:
        if sys.platform == "win32":
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return None


def setup(verbosity: int = 0) -> Path | None:
    """Console logging at the requested level plus a rotating log file (INFO, or DEBUG with -vv)."""
    console_level = [logging.WARNING, logging.INFO, logging.DEBUG][min(verbosity, 2)]
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.setFormatter(fmt)
    root.addHandler(console)
    path = None
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setLevel(logging.DEBUG if verbosity >= 2 else logging.INFO)
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as exc:
        log.warning("cannot write log file %s: %s", path, exc)
        path = None
    # third-party chatter
    for noisy in ("uvicorn.access", "mcp", "httpx", "PIL"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    log.info("PyHantek %s starting", __version__)
    log.info("python %s, %s, frozen=%s, admin=%s", platform.python_version(), platform.platform(),
             getattr(sys, "frozen", False), is_admin())
    sys.excepthook = _excepthook
    return path


def _excepthook(exc_type, exc, tb):
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    log.critical("unhandled exception:\n%s", text)
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        if QApplication.instance() is not None:
            box = QMessageBox(QMessageBox.Critical, "PyHantek error",
                              f"An unexpected error occurred:\n\n{exc_type.__name__}: {exc}\n\n"
                              f"Details were written to:\n{log_path()}")
            box.setDetailedText(text)
            box.exec()
    except Exception:
        pass
