"""Dark theme, palette and trace colours."""

from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

BG = "#0f1115"
SURFACE = "#171a21"
SURFACE_2 = "#1f232c"
BORDER = "#2a2f3a"
TEXT = "#e6e8ee"
MUTED = "#8b93a5"
ACCENT = "#4f8cff"
GOOD = "#22c55e"
BAD = "#ef4444"
WARN = "#f59e0b"

SCREEN_BG = "#07090c"
GRID = "#2b3240"
GRID_CENTER = "#4a5466"

CH_COLORS = ["#facc15", "#22d3ee", "#e879f9"]  # CH1, CH2, MATH
SPECTRUM_COLORS = ["#fde68a", "#a5f3fc", "#f5d0fe"]


def color(i: int) -> str:
    return CH_COLORS[i]


QSS = f"""
* {{ font-size: 12px; }}
QMainWindow, QWidget#Root {{ background: {BG}; }}
QWidget {{ color: {TEXT}; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px 6px; }}

QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}

QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel#CardTitle {{ font-size: 11px; font-weight: 700; letter-spacing: 1px; color: {MUTED}; }}
QLabel#Muted {{ color: {MUTED}; }}
QLabel#Value {{ font-size: 15px; font-weight: 600; }}

QPushButton, QToolButton {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 5px 10px; color: {TEXT};
}}
QPushButton:hover, QToolButton:hover {{ border-color: #3b4252; background: #252a35; }}
QPushButton:pressed, QToolButton:pressed {{ background: #2c3240; }}
QPushButton:checked, QToolButton:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; }}
QPushButton:disabled {{ color: #555c6b; }}
QPushButton#Run {{ font-weight: 700; min-width: 86px; background: {GOOD}; border-color: {GOOD}; color: #04140a; }}
QPushButton#Run[running="false"] {{ background: {BAD}; border-color: {BAD}; color: white; }}
QPushButton#Segment {{ border-radius: 0; padding: 4px 8px; }}
QPushButton#SegmentFirst {{ border-top-right-radius: 0; border-bottom-right-radius: 0; padding: 4px 8px; }}
QPushButton#SegmentLast {{ border-top-left-radius: 0; border-bottom-left-radius: 0; padding: 4px 8px; }}
QPushButton#Step {{ padding: 2px 0; min-width: 26px; max-width: 26px; font-weight: 700; }}

QComboBox, QDoubleSpinBox, QSpinBox, QLineEdit {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 6px; padding: 4px 6px;
    selection-background-color: {ACCENT};
}}
QComboBox:hover, QDoubleSpinBox:hover, QSpinBox:hover {{ border-color: #3b4252; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 14px; border: none; }}

QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 30px; height: 16px; border-radius: 8px; background: #2c3240; border: 1px solid {BORDER}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 12px; margin: -5px 0; border-radius: 6px; }}

QToolBar {{ background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER}; spacing: 6px; padding: 6px; }}
QToolBar QToolButton {{ padding: 5px 10px; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {MUTED}; }}
QStatusBar QLabel {{ color: {MUTED}; padding: 0 8px; }}
QProgressBar {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: 4px; height: 10px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}

QSplitter::handle {{ background: {BG}; }}
QSplitter::handle:vertical {{ height: 6px; }}

QTableWidget {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: transparent; }}
QTableWidget::item {{ padding: 2px 8px; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 4px 8px; font-size: 11px; font-weight: 600; }}
QTableCornerButton::section {{ background: {SURFACE}; border: none; }}

QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 5px 14px; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}

QFrame#Banner {{ background: #3a2a0b; border: 1px solid {WARN}; border-radius: 8px; }}
QFrame#Banner QLabel {{ color: #fde68a; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 20px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QDialog {{ background: {SURFACE}; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, c in (
        (QPalette.Window, BG),
        (QPalette.WindowText, TEXT),
        (QPalette.Base, SURFACE_2),
        (QPalette.AlternateBase, SURFACE),
        (QPalette.Text, TEXT),
        (QPalette.Button, SURFACE_2),
        (QPalette.ButtonText, TEXT),
        (QPalette.Highlight, ACCENT),
        (QPalette.HighlightedText, "#ffffff"),
        (QPalette.ToolTipBase, SURFACE_2),
        (QPalette.ToolTipText, TEXT),
        (QPalette.PlaceholderText, MUTED),
    ):
        pal.setColor(role, QColor(c))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
