"""
theme.py — a single, deliberate flat theme for the whole app.

Centralizing it here (rather than leaving widgets to inherit whatever the
OS theme happens to be) is also what fixes the color-picker contrast bug:
previously the app had no explicit palette, so on a dark-OS-theme machine
some native dialog chrome went dark while button text stayed black,
making it unreadable. Applying one explicit theme everywhere means every
widget's colors are always ones we chose on purpose.
"""

from PyQt6.QtGui import QPalette, QColor
from pathlib import Path

_ICONS = Path(__file__).parent / "icons"


def _icon_url(name):
    # QSS url() wants forward slashes even on Windows.
    return (_ICONS / name).as_posix()


# A deliberate UI-chrome font stack (toolbar, menus, dialogs — never the
# Urdu canvas text itself, which is governed entirely by canvas.py's own
# per-run QTextCharFormat and has nothing to do with this). Qt picks the
# first family actually installed, so this is one list covering each
# platform's native system font before falling back to something broadly
# available: Segoe UI (Windows), Ubuntu (common on GNOME/Ubuntu desktops),
# Noto Sans (wide Linux coverage), then universal fallbacks. Deliberately
# not the Qt/Fusion default of whatever bare sans-serif the OS hands back
# unstyled — a named, considered stack reads as more finished.
UI_FONT_STACK = '"Segoe UI", "Ubuntu", "Noto Sans", "Helvetica Neue", Arial, sans-serif'


BG = "#1B1C1F"  # app background ("the desk")
CANVAS_BACKDROP = "#141517"  # area behind the page itself, one shade darker
PANEL = "#242529"  # toolbars, dialogs, menus
FIELD = "#2C2D32"  # inputs, buttons, list rows
FIELD_HOVER = "#34353B"
FIELD_PRESSED = "#232428"
BORDER = "#38393F"
BORDER_HOVER = "#46474D"
TEXT = "#EDEBE6"  # warm off-white, not stark white
TEXT_MUTED = "#96938C"
TEXT_DISABLED = "#5B5954"
ACCENT = "#C9A15C"  # brass/gold — the one accent color, used sparingly
ACCENT_HOVER = "#D9B579"
ACCENT_PRESSED = "#B38A45"
ACCENT_ON = "#1B1C1F"  # text/icon color when sitting on top of ACCENT
WARNING_BG = "#3A2E12"
WARNING_TEXT = "#E8B84B"
WARNING_BORDER = "#6B5320"
DANGER = "#C75450"

QSS = f"""
QMainWindow {{
    background-color: {BG};
    color: {TEXT};
}}
QWidget {{
    color: {TEXT};
    font-family: {UI_FONT_STACK};
    font-size: 13px;
}}
/* PageCanvas (the page itself) deliberately has NO background rule here —
   it's user content (their chosen page color/image via canvas.py's
   set_background()/set_background_image()), not app chrome. A widget with
   no matching QSS background falls back to its own palette Base role,
   which is what those methods set. Actual typed text always carries an
   explicit QTextCharFormat foreground color, so the "color"/"font-family"
   rules above only matter as a fallback and don't affect displayed Urdu
   text — that's governed entirely by canvas.py's own per-run formatting. */

QToolBar {{
    background-color: {PANEL};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px;
    spacing: 6px;
}}
QToolBar QLabel {{
    color: {TEXT_MUTED};
    padding: 0 4px;
    background: transparent;
}}
QToolBar::separator {{
    background-color: {BORDER};
    width: 1px;
    margin: 6px 6px;
}}

QStatusBar {{
    background-color: {PANEL};
    color: {TEXT_MUTED};
    border-top: 1px solid {BORDER};
}}

QMenuBar {{
    background-color: {PANEL};
    color: {TEXT};
    border-bottom: 1px solid {BORDER};
    padding: 2px;
}}
QMenuBar::item {{
    background: transparent;
    padding: 4px 10px;
    border-radius: 4px;
}}
QMenuBar::item:selected {{ background-color: {FIELD_HOVER}; }}
QMenu {{
    background-color: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 26px 6px 14px;
    border-radius: 4px;
}}
QMenu::item:selected {{ background-color: {ACCENT}; color: {ACCENT_ON}; }}
QMenu::item:disabled {{ color: {TEXT_DISABLED}; }}
QMenu::separator {{
    height: 1px;
    background: {BORDER};
    margin: 4px 8px;
}}

QToolButton {{
    background: transparent;
    color: {TEXT};
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 5px 10px;
}}
QToolButton:hover {{ background-color: {FIELD}; border-color: {BORDER}; }}
QToolButton:pressed {{ background-color: {FIELD_PRESSED}; }}
QToolButton:checked {{ background-color: {ACCENT}; color: {ACCENT_ON}; }}
QToolButton:disabled {{ color: {TEXT_DISABLED}; }}

QPushButton {{
    background-color: {FIELD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 16px;
}}
QPushButton:hover {{ background-color: {FIELD_HOVER}; border-color: {BORDER_HOVER}; }}
QPushButton:pressed {{ background-color: {FIELD_PRESSED}; }}
QPushButton:disabled {{ color: {TEXT_DISABLED}; background-color: {PANEL}; }}
QPushButton:default {{
    background-color: {ACCENT};
    color: {ACCENT_ON};
    border: 1px solid {ACCENT};
    font-weight: 600;
}}
QPushButton:default:hover {{ background-color: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QPushButton:default:pressed {{ background-color: {ACCENT_PRESSED}; }}

QComboBox {{
    background-color: {FIELD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 10px;
    min-width: 70px;
}}
QComboBox:hover {{ border-color: {BORDER_HOVER}; }}
QComboBox::drop-down {{ border: none; width: 24px; }}
QComboBox::down-arrow {{
    image: url({_icon_url("chevron-down.svg")});
    width: 12px; height: 12px;
}}
QComboBox QAbstractItemView {{
    background-color: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_ON};
    outline: none;
    padding: 4px;
}}

QSpinBox {{
    background-color: {FIELD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 5px 8px;
}}
QSpinBox:hover {{ border-color: {BORDER_HOVER}; }}
QSpinBox::up-button, QSpinBox::down-button {{ background: transparent; border: none; width: 20px; }}
QSpinBox::up-arrow {{
    image: url({_icon_url("chevron-up-small.svg")});
    width: 11px; height: 11px;
}}
QSpinBox::down-arrow {{
    image: url({_icon_url("chevron-down-small.svg")});
    width: 11px; height: 11px;
}}

QLineEdit {{
    background-color: {FIELD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: {ACCENT};
    selection-color: {ACCENT_ON};
}}
QLineEdit:focus {{ border-color: {ACCENT}; }}

QCheckBox, QRadioButton {{ color: {TEXT}; spacing: 8px; background: transparent; }}
QCheckBox::indicator, QRadioButton::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDER_HOVER};
    background-color: {FIELD};
}}
QCheckBox::indicator {{ border-radius: 4px; }}
QRadioButton::indicator {{ border-radius: 8px; }}
QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {ACCENT}; border-color: {ACCENT};
}}
QCheckBox::indicator:hover, QRadioButton::indicator:hover {{ border-color: {ACCENT}; }}

QDialog {{ background-color: {BG}; }}

QScrollArea, QGraphicsView {{ background-color: {CANVAS_BACKDROP}; border: none; }}
QScrollArea > QWidget > QWidget {{ background-color: {CANVAS_BACKDROP}; }}

QScrollBar:vertical {{ background: {BG}; width: 13px; margin: 0; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 5px; min-height: 24px; margin: 2px; }}
QScrollBar::handle:vertical:hover {{ background: {BORDER_HOVER}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: {BG}; height: 13px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: {BORDER}; border-radius: 5px; min-width: 24px; margin: 2px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

QListWidget {{
    background-color: {FIELD};
    color: {TEXT};
    border: 1px solid {BORDER};
    border-radius: 6px;
    outline: none;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
QListWidget::item:hover {{ background-color: {FIELD_HOVER}; }}
QListWidget::item:selected {{ background-color: {FIELD_HOVER}; color: {TEXT}; }}
QListView::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDER_HOVER};
    border-radius: 4px;
    background-color: {FIELD};
}}
QListView::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; }}

QLabel#fontWarning {{
    background-color: {WARNING_BG};
    color: {WARNING_TEXT};
    border-bottom: 1px solid {WARNING_BORDER};
    padding: 8px 14px;
}}

QToolTip {{
    background-color: {PANEL};
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 6px;
}}
"""


def apply_theme(app):
    app.setStyle("Fusion")  # flattest cross-platform base style to start from

    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Base, QColor(FIELD))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(FIELD))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(PANEL))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(ACCENT_ON))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(TEXT_MUTED))
    pal.setColor(
        QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor(TEXT_DISABLED)
    )
    pal.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.WindowText,
        QColor(TEXT_DISABLED),
    )
    app.setPalette(pal)

    app.setStyleSheet(QSS)
