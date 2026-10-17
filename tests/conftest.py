import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def qapp():
    """One QApplication for the whole session (widgets crash under a plain QCoreApplication)."""
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])
