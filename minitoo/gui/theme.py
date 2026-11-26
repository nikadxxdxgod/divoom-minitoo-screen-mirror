"""Dark theme (dock-based layout in the spirit of streaming studio apps)."""

import os

BG = "#1b1c21"
CANVAS = "#0f1013"
DOCK = "#24262c"
DOCK_TITLE = "#2c2f37"
INPUT = "#30333b"
BORDER = "#3a3e48"
TEXT = "#e8eaee"
MUTED = "#9299a6"
ACCENT = "#4c7dff"
ACCENT_HOVER = "#668fff"
SUCCESS = "#33c27f"
WARNING = "#f2b84b"
DANGER = "#e5534b"


def apply_palette(app) -> None:
    """Dark Fusion palette - check boxes, arrows etc. stay visible."""
    from PyQt6.QtGui import QColor, QPalette

    p = QPalette()
    R = QPalette.ColorRole
    for role, color in ((R.Window, BG), (R.WindowText, TEXT), (R.Base, INPUT),
                        (R.AlternateBase, DOCK), (R.Text, TEXT), (R.Button, INPUT),
                        (R.ButtonText, TEXT), (R.Highlight, ACCENT), (R.HighlightedText, "#ffffff"),
                        (R.ToolTipBase, DOCK_TITLE), (R.ToolTipText, TEXT), (R.Light, "#454a56"),
                        (R.Midlight, "#3a3e48"), (R.Mid, BORDER), (R.Dark, "#121317"),
                        (R.Shadow, "#000000"), (R.PlaceholderText, "#707784"), (R.Link, ACCENT)):
        p.setColor(role, QColor(color))
    for role in (R.Text, R.WindowText, R.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor("#5f6573"))
    app.setPalette(p)


QSS = f"""
* {{ font-family: "Segoe UI", "Sylfaen", "Microsoft YaHei UI", "Yu Gothic UI", "Malgun Gothic"; font-size: 9.5pt; }}
QMainWindow, QDialog {{ background: {BG}; color: {TEXT}; }}
QMainWindow::separator {{ background: {BG}; width: 4px; height: 4px; }}
QMainWindow::separator:hover {{ background: {ACCENT}; }}
QWidget {{ color: {TEXT}; }}
QLabel {{ background: transparent; }}
QLabel[muted="true"] {{ color: {MUTED}; }}
QLabel[heading="true"] {{ font-size: 13pt; font-weight: 600; }}

QMenuBar {{ background: {DOCK}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 5px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: {DOCK_TITLE}; }}
QMenu {{ background: {DOCK}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 26px 6px 22px; border-radius: 4px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}

QDockWidget {{ color: {TEXT}; titlebar-close-icon: none; }}
QDockWidget::title {{ background: {DOCK_TITLE}; padding: 6px 10px; text-align: left;
    border-bottom: 1px solid {BORDER}; font-weight: 600; }}
QDockWidget > QWidget {{ background: {DOCK}; }}
QWidget#dockBody {{ background: {DOCK}; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QTabBar::tab {{ background: {DOCK_TITLE}; color: {MUTED}; padding: 6px 12px; border: none;
    border-right: 1px solid {BORDER}; }}
QTabBar::tab:selected {{ background: {DOCK}; color: {TEXT}; border-top: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabWidget::pane {{ border: 1px solid {BORDER}; background: {DOCK}; }}

QGroupBox {{ border: 1px solid {BORDER}; border-radius: 6px; margin-top: 12px;
    padding: 10px 8px 8px 8px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 5px; color: {MUTED}; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit, QListWidget {{
    background: {INPUT}; border: 1px solid {BORDER}; border-radius: 4px; padding: 4px 6px;
    selection-background-color: {ACCENT};
}}
QListWidget {{ padding: 2px; outline: none; }}
QListWidget::item {{ padding: 6px 6px; border-radius: 4px; }}
QListWidget::item:selected {{ background: {ACCENT}; color: white; }}
QListWidget::item:hover:!selected {{ background: {DOCK_TITLE}; }}
QComboBox QAbstractItemView {{ background: {INPUT}; border: 1px solid {BORDER};
    selection-background-color: {ACCENT}; outline: none; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border-color: {ACCENT}; }}

QPushButton {{ background: {INPUT}; border: 1px solid {BORDER}; border-radius: 4px;
    padding: 6px 12px; }}
QPushButton:hover {{ border-color: {ACCENT}; background: #363a44; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:disabled {{ color: #5f6573; border-color: #2e3139; background: #2a2c33; }}
QPushButton[primary="true"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[primary="true"]:disabled {{ background: #2b3a5c; border-color: #2b3a5c; color: #8b97b0; }}
QPushButton[active="true"] {{ background: {DANGER}; border-color: {DANGER}; color: white; font-weight: 600; }}
QPushButton[big="true"] {{ padding: 9px 12px; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {TEXT}; width: 14px; height: 14px; margin: -5px 0;
    border-radius: 7px; }}
QSlider::handle:horizontal:hover {{ background: white; }}
QCheckBox, QRadioButton {{ spacing: 7px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{ width: 15px; height: 15px; }}
QCheckBox::indicator {{ border: 1px solid #6a7387; border-radius: 3px; background: {INPUT}; }}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url(%CHECK%); }}
QCheckBox::indicator:disabled {{ border-color: #3a4050; }}
QRadioButton::indicator {{ border: 1px solid #6a7387; border-radius: 8px; background: {INPUT}; }}
QRadioButton::indicator:checked {{ background: #ffffff; border: 4px solid {ACCENT}; width: 9px; height: 9px; }}
QProgressBar {{ background: {INPUT}; border: 1px solid {BORDER}; border-radius: 4px; height: 10px;
    text-align: center; font-size: 8pt; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 3px; }}
QPlainTextEdit#log {{ font-family: "Consolas", "Sylfaen", "Microsoft YaHei UI"; font-size: 9pt;
    background: #1d1f24; }}
QStatusBar {{ background: {DOCK}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ padding: 0 8px; }}
QToolTip {{ background: {DOCK_TITLE}; color: {TEXT}; border: 1px solid {BORDER}; padding: 5px; }}
QFrame#tile {{ background: {INPUT}; border: 1px solid {BORDER}; border-radius: 6px; }}
QLabel#tileValue {{ font-size: 14pt; font-weight: 600; }}
QLabel#tileTitle {{ color: {MUTED}; font-size: 8.5pt; }}
QAbstractSpinBox {{ padding-right: 20px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{
    subcontrol-origin: border; width: 18px; border-left: 1px solid {BORDER}; background: transparent; }}
QAbstractSpinBox::up-button {{ subcontrol-position: top right; border-top-right-radius: 4px; }}
QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; border-bottom-right-radius: 4px; }}
QAbstractSpinBox::up-button:hover, QAbstractSpinBox::down-button:hover {{ background: {BORDER}; }}
QAbstractSpinBox::up-arrow {{ image: url(%UP%); width: 9px; height: 9px; }}
QAbstractSpinBox::down-arrow {{ image: url(%DOWN%); width: 9px; height: 9px; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{ image: url(%DOWN%); width: 10px; height: 10px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
QScrollBar::handle:vertical {{ background: #474c58; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #596070; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #474c58; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def build_qss(assets_dir: str) -> str:
    def p(name: str) -> str:
        return os.path.join(assets_dir, name).replace("\\", "/")

    return (QSS.replace("%CHECK%", p("check.svg")).replace("%UP%", p("up.svg"))
            .replace("%DOWN%", p("down.svg")))
