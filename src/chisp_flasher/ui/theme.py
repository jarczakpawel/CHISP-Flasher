
from __future__ import annotations

from pathlib import Path


def _asset_url(name: str) -> str:
    path = (Path(__file__).resolve().parent / 'assets' / name).as_posix()
    return path


def build_stylesheet() -> str:
    combo_arrow = _asset_url('combo_arrow.svg')
    check_mark = _asset_url('check_mark.svg')
    return f"""
QWidget {{
    background: #ddd5ca;
    color: #302923;
    font-size: 13px;
}}
QMainWindow, QStatusBar, QScrollArea, QSplitter, QTabWidget::pane {{
    background: #ddd5ca;
}}
QLabel {{
    background: transparent;
    color: #302923;
}}
QLabel[role="title"] {{
    font-size: 28px;
    font-weight: 700;
    color: #241f1a;
}}
QLabel[role="subtitle"] {{
    color: #645b51;
    font-size: 14px;
}}
QLabel[role="section"] {{
    font-size: 15px;
    font-weight: 700;
    color: #2d2721;
}}
QLabel[role="muted"] {{
    color: #6c6258;
    font-size: 12px;
}}
QLabel[role="pill"] {{
    background: #efe7dc;
    border: 1px solid #d0c1b2;
    border-radius: 14px;
    padding: 8px 12px;
    font-weight: 600;
}}
QFrame[card="true"], QGroupBox {{
    background: #f5efe8;
    border: 1px solid #cfbfae;
    border-radius: 15px;
}}
QWidget[flatRow="true"], QWidget[panel="true"], QWidget[flatField="true"] {{
    background: #f5efe8;
}}
QGroupBox {{
    margin-top: 10px;
    padding-top: 12px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
    background: transparent;
}}
QGroupBox[logSection="true"] {{
    background: #fcfaf7;
}}
QGroupBox[logSection="true"]::title {{
    left: 12px;
}}
QGroupBox[logSection="true"] QPlainTextEdit[embedded="true"] {{
    background: #fcfaf7;
    border: none;
    border-radius: 0px;
    padding: 0;
}}
QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QListWidget {{
    background: #fcfaf7;
    border: 1px solid #cfbfaf;
    border-radius: 10px;
    padding: 8px 10px;
    selection-background-color: #cfbead;
}}
QPlainTextEdit[embedded="true"] {{
    background: #fcfaf7;
    border: none;
    border-radius: 0px;
    padding: 0;
}}
QComboBox QLineEdit {{
    background: #fcfaf7;
    border: none;
    padding: 0;
    margin: 0;
    color: #302923;
}}
QLineEdit[readOnly="true"] {{
    background: #f7f1ea;
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox:editable {{
    background: #fcfaf7;
}}
QComboBox::drop-down {{
    width: 28px;
    border: none;
    background: transparent;
}}
QComboBox::down-arrow {{
    image: url({combo_arrow});
    width: 10px;
    height: 6px;
}}
QAbstractItemView {{
    background: #fcfaf7;
    border: 1px solid #cfbfaf;
    selection-background-color: #e2d4c6;
}}

QFileDialog QWidget, QFileDialog QSplitter, QFileDialog QFrame {{
    background: #ddd5ca;
    color: #302923;
}}
QFileDialog QListView, QFileDialog QTreeView {{
    background: #fcfaf7;
    border: 1px solid #cfbfaf;
    border-radius: 10px;
    selection-background-color: #e2d4c6;
}}
QFileDialog QHeaderView::section {{
    background: #efe7dc;
    color: #302923;
    border: none;
    border-bottom: 1px solid #cfbfaf;
    padding: 6px 8px;
}}
QFileDialog QToolButton {{
    background: #eee5da;
    color: #302923;
    border: 1px solid #cdbdab;
    border-radius: 10px;
    padding: 8px 10px;
    min-width: 34px;
    min-height: 30px;
}}
QFileDialog QToolButton:hover {{
    background: #e7ddd1;
    border-color: #b8a898;
}}
QFileDialog QToolButton:pressed {{
    background: #ddd1c4;
}}
QPushButton, QToolButton {{
    background: #eee5da;
    border: 1px solid #cdbdab;
    border-radius: 10px;
    padding: 8px 12px;
    min-height: 30px;
}}
QPushButton:hover, QToolButton:hover {{
    background: #e7ddd1;
    border-color: #b8a898;
}}
QPushButton:pressed, QToolButton:pressed {{
    background: #ddd1c4;
}}
QPushButton[accent="true"], QToolButton[accent="true"] {{
    background: #8b6a53;
    color: #ffffff;
    border-color: #7e5e47;
    font-weight: 700;
}}
QPushButton[accent="true"]:hover, QToolButton[accent="true"]:hover {{
    background: #7e5e47;
}}
QPushButton[ghost="true"], QToolButton[ghost="true"] {{
    background: #f2eadf;
}}
QPushButton[ghost="true"]:hover, QToolButton[ghost="true"]:hover {{
    background: #e3d7c9;
    border-color: #b8a898;
}}
QPushButton[ghost="true"]:pressed, QToolButton[ghost="true"]:pressed {{
    background: #d7cabd;
}}
QPushButton[tabLike="true"], QToolButton[tabLike="true"] {{
    background: transparent;
    border: 1px solid #cfbfaf;
    border-bottom: none;
    border-top-left-radius: 11px;
    border-top-right-radius: 11px;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    padding: 0px 14px 0px 14px;
    min-height: 34px;
    text-align: center;
}}
QPushButton[tabLike="true"]::menu-indicator, QToolButton[tabLike="true"]::menu-indicator {{
    image: none;
    width: 0px;
}}
QPushButton[tabLike="true"][tabActive="true"], QToolButton[tabLike="true"][tabActive="true"] {{
    background: #f5efe8;
    font-weight: 700;
}}
QPushButton[tabLike="true"]:hover, QToolButton[tabLike="true"]:hover {{
    background: #ece2d6;
}}
QPushButton[tabLike="true"][tabActive="true"]:hover, QToolButton[tabLike="true"][tabActive="true"]:hover {{
    background: #f0e8de;
}}
QPushButton[tabLike="true"][tabStatic="true"], QToolButton[tabLike="true"][tabStatic="true"] {{
    background: #f5efe8;
    font-weight: 700;
}}
QPushButton[tabLike="true"][tabStatic="true"]:hover, QToolButton[tabLike="true"][tabStatic="true"]:hover {{
    background: #f5efe8;
}}
QFrame[logPane="true"] {{
    background: #fcfaf7;
    border: 1px solid #cfbfae;
    border-radius: 15px;
}}
QCheckBox {{
    spacing: 8px;
    background: transparent;
}}
QCheckBox[flat="true"] {{
    background: #f5efe8;
}}
QCheckBox::indicator {{
    width: 18px;
    height: 18px;
    border-radius: 5px;
    border: 1px solid #baa896;
    background: #fcfaf7;
}}
QCheckBox::indicator:checked {{
    background: #8b6a53;
    border-color: #7e5e47;
    image: url({check_mark});
}}
QTabWidget::pane {{
    border: none;
}}
QTabBar::tab {{
    background: transparent;
    border: 1px solid #cfbfaf;
    border-bottom: none;
    border-top-left-radius: 11px;
    border-top-right-radius: 11px;
    padding: 0px 14px 0px 14px;
    min-height: 34px;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    background: #f5efe8;
    font-weight: 700;
}}
QTabBar::tab:hover {{
    background: #ece2d6;
}}
QTabBar::tab:selected:hover {{
    background: #f0e8de;
}}
QScrollBar:vertical {{
    width: 12px;
    background: transparent;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #cab9a7;
    border-radius: 6px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
    background: transparent;
    border: none;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    height: 12px;
    background: transparent;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: #cab9a7;
    border-radius: 6px;
    min-width: 30px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
    background: transparent;
    border: none;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
QProgressBar {{
    border: 1px solid #cdbdaa;
    border-radius: 10px;
    background: #fcfaf7;
    text-align: center;
    min-height: 18px;
}}
QProgressBar::chunk {{
    background: #8b6a53;
    border-radius: 9px;
}}
QMenu {{
    background: #fbf7f2;
    border: 1px solid #cfbfaf;
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 18px;
    border-radius: 8px;
}}
QMenu::item:selected {{
    background: #e7ddd1;
}}
"""


APP_QSS = build_stylesheet()
