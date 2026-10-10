from PyQt6.QtWidgets import (
    QDialog, QFormLayout, QSpinBox, QDoubleSpinBox, QPushButton, QColorDialog, QHBoxLayout,
    QVBoxLayout, QLabel, QComboBox, QCheckBox, QDialogButtonBox, QWidget,
    QRadioButton, QButtonGroup, QListWidget, QListWidgetItem, QLineEdit,
    QGridLayout, QFileDialog, QMessageBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QFrame
)
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QPainter, QLinearGradient, QPen, QBrush, QImage, QIcon
from PyQt6.QtCore import pyqtSignal, Qt, QPoint, QSize, QEvent
from pathlib import Path

import font_settings
import theme

# A background image only ever needs to be as sharp as the page it's
# behind, and even the largest page preset here (A4 Print at 300dpi) is
# well under this on its longest side — a source photo straight off a
# phone or camera can easily be 10-50x that, which would otherwise bloat
# every save (background images are embedded in .utw files, not
# referenced by path — see project_io.py) for zero visible benefit.
MAX_BACKGROUND_IMAGE_DIMENSION = 2400


def _downscale_if_needed(image: QImage) -> QImage:
    longest = max(image.width(), image.height())
    if longest <= MAX_BACKGROUND_IMAGE_DIMENSION:
        return image
    scale = MAX_BACKGROUND_IMAGE_DIMENSION / longest
    new_size = (max(1, round(image.width() * scale)), max(1, round(image.height() * scale)))
    return image.scaled(new_size[0], new_size[1], Qt.AspectRatioMode.KeepAspectRatio,
                         Qt.TransformationMode.SmoothTransformation)


class _SVSquare(QWidget):
    """Saturation/Value picking area for a given hue."""
    changed = pyqtSignal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(200, 200)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._hue = 0.0
        self._sat = 1.0
        self._val = 1.0

    def set_hue(self, hue):
        self._hue = hue
        self.update()

    def set_sv(self, s, v):
        self._sat, self._val = s, v
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        w, h = self.width(), self.height()
        # horizontal = saturation (white -> pure hue), vertical = value (top opaque -> bottom black)
        hue_grad = QLinearGradient(0, 0, w, 0)
        hue_grad.setColorAt(0, QColor.fromHsvF(self._hue, 0.0, 1.0))
        hue_grad.setColorAt(1, QColor.fromHsvF(self._hue, 1.0, 1.0))
        painter.fillRect(self.rect(), QBrush(hue_grad))
        val_grad = QLinearGradient(0, 0, 0, h)
        val_grad.setColorAt(0, QColor(0, 0, 0, 0))
        val_grad.setColorAt(1, QColor(0, 0, 0, 255))
        painter.fillRect(self.rect(), QBrush(val_grad))

        mx = int(self._sat * w)
        my = int((1 - self._val) * h)
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawEllipse(QPoint(mx, my), 6, 6)
        painter.setPen(QPen(QColor("black"), 1))
        painter.drawEllipse(QPoint(mx, my), 6, 6)

    def mousePressEvent(self, e):
        self._pick(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._pick(e.position())

    def _pick(self, pos):
        x = max(0.0, min(pos.x(), self.width()))
        y = max(0.0, min(pos.y(), self.height()))
        self._sat = x / self.width()
        self._val = 1.0 - y / self.height()
        self.update()
        self.changed.emit(self._sat, self._val)


class _HueStrip(QWidget):
    """Vertical hue slider, 0.0-1.0."""
    changed = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(26, 200)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hue = 0.0

    def set_hue(self, hue):
        self._hue = hue
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        grad = QLinearGradient(0, 0, 0, self.height())
        steps = 12
        for i in range(steps + 1):
            grad.setColorAt(i / steps, QColor.fromHsvF(i / steps, 1.0, 1.0))
        painter.fillRect(self.rect(), QBrush(grad))
        y = int(self._hue * self.height())
        painter.setPen(QPen(QColor("white"), 2))
        painter.drawLine(0, y, self.width(), y)
        painter.setPen(QPen(QColor("black"), 1))
        painter.drawLine(0, y - 1, self.width(), y - 1)

    def mousePressEvent(self, e):
        self._pick(e.position())

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._pick(e.position())

    def _pick(self, pos):
        y = max(0.0, min(pos.y(), self.height()))
        self._hue = y / self.height()
        self.update()
        self.changed.emit(self._hue)


_PRESET_COLORS = [
    "#EDEBE6", "#FFFFFF", "#C9A15C", "#E8B84B", "#C0392B", "#E67E22",
    "#F1C40F", "#27AE60", "#16A085", "#2980B9", "#8E44AD", "#2C3E50",
    "#000000", "#5B5954", "#96938C", "#0B3D2E",
]


class ColorPickerDialog(QDialog):
    """A fully custom color picker, themed the same as the rest of the app.

    Exists because the OS-native color dialog doesn't reliably pick up the
    app's dark theme on every platform — even with Qt's own cross-platform
    dialog forced on, some chrome apparently still renders light on at
    least one setup. Building the picker from the same widgets (and the two
    custom-painted ones above) used everywhere else in the app sidesteps
    that uncertainty entirely: every pixel here is ours."""

    def __init__(self, initial: QColor, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose color")
        self._color = QColor(initial)
        h, s, v, _ = self._color.getHsvF()
        self._hue = max(0.0, h)

        self.sv_square = _SVSquare()
        self.sv_square.set_hue(self._hue)
        self.sv_square.set_sv(s, v)
        self.sv_square.changed.connect(self._on_sv_changed)

        self.hue_strip = _HueStrip()
        self.hue_strip.set_hue(self._hue)
        self.hue_strip.changed.connect(self._on_hue_changed)

        picker_row = QHBoxLayout()
        picker_row.addWidget(self.sv_square)
        picker_row.addWidget(self.hue_strip)

        self.preview = QLabel()
        self.preview.setFixedHeight(32)

        self.hex_edit = QLineEdit()
        self.hex_edit.setMaxLength(7)
        self.hex_edit.editingFinished.connect(self._on_hex_edited)

        self.r_spin, self.g_spin, self.b_spin = QSpinBox(), QSpinBox(), QSpinBox()
        rgb_row = QHBoxLayout()
        for label, spin in (("R", self.r_spin), ("G", self.g_spin), ("B", self.b_spin)):
            spin.setRange(0, 255)
            spin.valueChanged.connect(self._on_rgb_edited)
            rgb_row.addWidget(QLabel(label))
            rgb_row.addWidget(spin)

        hex_row = QHBoxLayout()
        hex_row.addWidget(QLabel("Hex"))
        hex_row.addWidget(self.hex_edit)

        preset_grid = QGridLayout()
        preset_grid.setSpacing(4)
        for i, hex_color in enumerate(_PRESET_COLORS):
            btn = QPushButton()
            btn.setFixedSize(22, 22)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {hex_color}; border: 1px solid {theme.BORDER}; "
                f"border-radius: 4px; }}"
                f"QPushButton:hover {{ border: 2px solid {theme.ACCENT}; }}"
            )
            btn.clicked.connect(lambda _checked, c=hex_color: self._set_color(QColor(c)))
            preset_grid.addWidget(btn, i // 8, i % 8)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)
        layout.addLayout(picker_row)
        layout.addWidget(self.preview)
        layout.addLayout(hex_row)
        layout.addLayout(rgb_row)
        layout.addWidget(QLabel("Presets"))
        layout.addLayout(preset_grid)
        layout.addWidget(buttons)

        self._sync_fields()

    def _on_sv_changed(self, s, v):
        self._color = QColor.fromHsvF(self._hue, s, v)
        self._sync_fields(skip_square=True)

    def _on_hue_changed(self, hue):
        self._hue = hue
        self.sv_square.set_hue(hue)
        _h, s, v, _a = self._color.getHsvF()
        self._color = QColor.fromHsvF(hue, s, v)
        self._sync_fields(skip_square=True, skip_hue=True)

    def _on_hex_edited(self):
        text = self.hex_edit.text().strip()
        if not text.startswith("#"):
            text = "#" + text
        c = QColor(text)
        if c.isValid():
            self._set_color(c)
        else:
            self._sync_fields()  # revert to last valid value

    def _on_rgb_edited(self):
        c = QColor(self.r_spin.value(), self.g_spin.value(), self.b_spin.value())
        self._set_color(c)

    def _set_color(self, color: QColor):
        self._color = QColor(color)
        h, s, v, _a = self._color.getHsvF()
        if h >= 0:
            self._hue = h
        self._sync_fields()

    def _sync_fields(self, skip_square=False, skip_hue=False):
        c = self._color
        self.preview.setStyleSheet(
            f"background-color: {c.name()}; border: 1px solid {theme.BORDER}; border-radius: 4px;"
        )
        for w in (self.hex_edit, self.r_spin, self.g_spin, self.b_spin):
            w.blockSignals(True)
        self.hex_edit.setText(c.name())
        self.r_spin.setValue(c.red())
        self.g_spin.setValue(c.green())
        self.b_spin.setValue(c.blue())
        for w in (self.hex_edit, self.r_spin, self.g_spin, self.b_spin):
            w.blockSignals(False)
        if not skip_hue:
            self.hue_strip.set_hue(self._hue)
        if not skip_square:
            _h, s, v, _a = c.getHsvF()
            self.sv_square.set_hue(self._hue)
            self.sv_square.set_sv(s, v)

    def selected_color(self) -> QColor:
        return QColor(self._color)

    @staticmethod
    def getColor(initial: QColor, parent=None):
        dlg = ColorPickerDialog(initial, parent)
        if dlg.exec():
            return dlg.selected_color()
        return QColor()  # invalid, same convention as QColorDialog.getColor on cancel


class ColorSwatchButton(QPushButton):
    """A small clickable swatch that opens a color picker on click."""
    colorChanged = pyqtSignal(QColor)

    def __init__(self, initial: QColor, parent=None):
        super().__init__(parent)
        self._color = QColor(initial)
        self.setFixedSize(36, 24)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh()
        self.clicked.connect(self._pick)

    def _refresh(self):
        self.setStyleSheet(
            f"QPushButton {{ background-color: {self._color.name()}; "
            f"border: 1px solid {theme.BORDER_HOVER}; border-radius: 4px; }}"
            f"QPushButton:hover {{ border: 1px solid {theme.ACCENT}; }}"
        )

    def _pick(self):
        c = ColorPickerDialog.getColor(self._color, self)
        if c.isValid():
            self._color = c
            self._refresh()
            self.colorChanged.emit(self._color)

    def color(self) -> QColor:
        return QColor(self._color)

    def set_color(self, color: QColor):
        self._color = QColor(color)
        self._refresh()


PRESETS = [
    ("Custom", None, None),
    ("Instagram Post (1080 x 1080)", 1080, 1080),
    ("Instagram Story (1080 x 1920)", 1080, 1920),
    ("Facebook Post (1200 x 630)", 1200, 630),
    ("X / Twitter Post (1600 x 900)", 1600, 900),
    ("A4 Print @96dpi (794 x 1123)", 794, 1123),
]


PX_PER_UNIT = {
    "px": 1.0,
    "in": 96.0,
    "cm": 96.0 / 2.54,
    "mm": 96.0 / 25.4,
    "ft": 96.0 * 12.0,
}


def px_to_unit(px, unit):
    return px / PX_PER_UNIT[unit]


def unit_to_px(value, unit):
    return value * PX_PER_UNIT[unit]


class NewPageDialog(QDialog):
    """Also reused as "Page Setup" for an existing page."""

    IMAGE_MODES = [("Fit (whole image visible)", "fit"), ("Fill (crop to cover)", "fill"),
                   ("Center (no scaling)", "center"), ("Tile (repeat)", "tile")]
    BORDER_STYLES = [("Solid", Qt.PenStyle.SolidLine), ("Dashed", Qt.PenStyle.DashLine),
                      ("Dotted", Qt.PenStyle.DotLine), ("Dash-Dot", Qt.PenStyle.DashDotLine)]
    VERTICAL_ALIGNS = [("Top", "top"), ("Middle", "middle"), ("Bottom", "bottom")]

    def __init__(self, width_px=1080, height_px=1080, background=None,
                 margin_px=40, background_image=None, background_image_mode="fit",
                 border_width=0, border_color=None, border_style=Qt.PenStyle.SolidLine,
                 vertical_align="top", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Page Setup")
        self._unit = "px"
        self._width_px = width_px
        self._height_px = height_px
        self._image = background_image
        self._image_label_text = "Custom image set" if background_image is not None else None

        self.preset_box = QComboBox()
        for label, _, _ in PRESETS:
            self.preset_box.addItem(label)
        self.preset_box.currentIndexChanged.connect(self._preset_changed)
        for i, (_label, w, h) in enumerate(PRESETS):
            if w == width_px and h == height_px:
                self.preset_box.blockSignals(True)
                self.preset_box.setCurrentIndex(i)
                self.preset_box.blockSignals(False)
                break

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["px", "in", "cm", "mm", "ft"])
        self.unit_combo.currentTextChanged.connect(self._unit_changed)

        self.width_spin = QDoubleSpinBox()
        self.width_spin.setRange(0.01, 100000)
        self.width_spin.valueChanged.connect(self._width_edited)

        self.height_spin = QDoubleSpinBox()
        self.height_spin.setRange(0.01, 100000)
        self.height_spin.valueChanged.connect(self._height_edited)

        self._refresh_spin_display()

        self.margin_spin = QSpinBox()
        self.margin_spin.setRange(0, 1000)
        self.margin_spin.setValue(int(round(margin_px)))
        self.margin_spin.setSuffix(" px")

        self.bg_button = ColorSwatchButton(QColor(background) if background else QColor("white"))

        self.image_path_label = QLabel()
        self.image_path_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self._refresh_image_label()
        image_choose_btn = QPushButton("Choose Image…")
        image_choose_btn.clicked.connect(self._choose_image)
        image_clear_btn = QPushButton("Clear")
        image_clear_btn.clicked.connect(self._clear_image)
        image_btn_row = QHBoxLayout()
        image_btn_row.addWidget(image_choose_btn)
        image_btn_row.addWidget(image_clear_btn)

        self.image_mode_combo = QComboBox()
        for label, _key in self.IMAGE_MODES:
            self.image_mode_combo.addItem(label)
        for i, (_label, key) in enumerate(self.IMAGE_MODES):
            if key == background_image_mode:
                self.image_mode_combo.setCurrentIndex(i)
                break

        self.border_checkbox = QCheckBox("Show border")
        self.border_checkbox.setChecked(border_width > 0)
        self.border_checkbox.toggled.connect(self._border_toggled)
        self.border_width_spin = QSpinBox()
        self.border_width_spin.setRange(1, 200)
        self.border_width_spin.setValue(border_width if border_width > 0 else 6)
        self.border_width_spin.setSuffix(" px")
        self.border_color_button = ColorSwatchButton(QColor(border_color) if border_color else QColor(theme.ACCENT))
        self.border_style_combo = QComboBox()
        for label, _style in self.BORDER_STYLES:
            self.border_style_combo.addItem(label)
        for i, (_label, style) in enumerate(self.BORDER_STYLES):
            if style == border_style:
                self.border_style_combo.setCurrentIndex(i)
                break
        border_row = QHBoxLayout()
        border_row.addWidget(self.border_width_spin)
        border_row.addWidget(self.border_color_button)
        border_row.addWidget(self.border_style_combo)
        self._border_toggled(self.border_checkbox.isChecked())

        self.vertical_align_combo = QComboBox()
        for label, _key in self.VERTICAL_ALIGNS:
            self.vertical_align_combo.addItem(label)
        for i, (_label, key) in enumerate(self.VERTICAL_ALIGNS):
            if key == vertical_align:
                self.vertical_align_combo.setCurrentIndex(i)
                break

        form = QFormLayout()
        form.setSpacing(10)
        form.setContentsMargins(0, 0, 0, 0)
        form.addRow("Preset", self.preset_box)
        form.addRow("Unit", self.unit_combo)
        form.addRow("Width", self.width_spin)
        form.addRow("Height", self.height_spin)
        form.addRow("Margin", self.margin_spin)
        form.addRow("Text Vertical Align", self.vertical_align_combo)
        form.addRow("Background Color", self.bg_button)
        form.addRow("Background Image", self.image_path_label)
        form.addRow("", image_btn_row)
        form.addRow("Image Fit", self.image_mode_combo)
        form.addRow("", self.border_checkbox)
        form.addRow("Border", border_row)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _refresh_image_label(self):
        if self._image is not None:
            self.image_path_label.setText(self._image_label_text or "Custom image set")
        else:
            self.image_path_label.setText("None")

    def _choose_image(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose Background Image", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"
        )
        if not path:
            return
        img = QImage(path)
        if img.isNull():
            QMessageBox.warning(self, "Background Image", f"Couldn't read this image file:\n\n{path}")
            return
        self._image = _downscale_if_needed(img)
        self._image_label_text = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
        self._refresh_image_label()

    def _clear_image(self):
        self._image = None
        self._image_label_text = None
        self._refresh_image_label()

    def _border_toggled(self, checked):
        self.border_width_spin.setEnabled(checked)
        self.border_color_button.setEnabled(checked)
        self.border_style_combo.setEnabled(checked)

    def _refresh_spin_display(self):
        decimals = 0 if self._unit == "px" else 2
        for spin, px_value in ((self.width_spin, self._width_px), (self.height_spin, self._height_px)):
            spin.blockSignals(True)
            spin.setDecimals(decimals)
            spin.setSuffix(f" {self._unit}")
            spin.setValue(round(px_to_unit(px_value, self._unit), decimals))
            spin.blockSignals(False)

    def _unit_changed(self, unit):
        self._unit = unit
        self._refresh_spin_display()

    def _width_edited(self, value):
        self._width_px = round(unit_to_px(value, self._unit))
        self.preset_box.setCurrentIndex(0)

    def _height_edited(self, value):
        self._height_px = round(unit_to_px(value, self._unit))
        self.preset_box.setCurrentIndex(0)

    def _preset_changed(self, index):
        _label, w, h = PRESETS[index]
        if w is not None:
            self._width_px, self._height_px = w, h
            self._refresh_spin_display()

    def values(self):
        return {
            "width": round(self._width_px),
            "height": round(self._height_px),
            "margin": self.margin_spin.value(),
            "background": self.bg_button.color(),
            "background_image": self._image,
            "background_image_mode": self.IMAGE_MODES[self.image_mode_combo.currentIndex()][1],
            "border_width": self.border_width_spin.value() if self.border_checkbox.isChecked() else 0,
            "border_color": self.border_color_button.color(),
            "border_style": self.BORDER_STYLES[self.border_style_combo.currentIndex()][1],
            "vertical_align": self.VERTICAL_ALIGNS[self.vertical_align_combo.currentIndex()][1],
        }


class FindReplaceDialog(QDialog):
    """Non-modal (setModal(False) below) so the canvas stays interactive
    and visible while this is open -- a find dialog that blocks the rest
    of the app is a common annoyance. Holds no reference to the canvas
    itself: it emits signals and main.py's handlers do the actual
    finding/replacing, the same split of responsibility as every other
    dialog in this file (collect input here, act on it in main.py)."""

    find_next = pyqtSignal(str, bool, bool)       # text, match_case, whole_word
    find_previous = pyqtSignal(str, bool, bool)
    replace_current = pyqtSignal(str, str, bool, bool)  # find, replace, match_case, whole_word
    replace_all = pyqtSignal(str, str, bool, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find & Replace")
        self.setModal(False)

        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find…")
        self.find_edit.returnPressed.connect(self._emit_find_next)

        self.replace_edit = QLineEdit()
        self.replace_edit.setPlaceholderText("Replace with…")

        self.match_case = QCheckBox("Match case")
        self.whole_word = QCheckBox("Whole word")
        opts_row = QHBoxLayout()
        opts_row.addWidget(self.match_case)
        opts_row.addWidget(self.whole_word)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Find", self.find_edit)
        form.addRow("Replace", self.replace_edit)
        form.addRow("", opts_row)

        find_prev_btn = QPushButton("Previous")
        find_prev_btn.clicked.connect(lambda: self.find_previous.emit(
            self.find_edit.text(), self.match_case.isChecked(), self.whole_word.isChecked()))
        find_next_btn = QPushButton("Next")
        find_next_btn.clicked.connect(self._emit_find_next)
        replace_btn = QPushButton("Replace")
        replace_btn.clicked.connect(lambda: self.replace_current.emit(
            self.find_edit.text(), self.replace_edit.text(),
            self.match_case.isChecked(), self.whole_word.isChecked()))
        replace_all_btn = QPushButton("Replace All")
        replace_all_btn.clicked.connect(lambda: self.replace_all.emit(
            self.find_edit.text(), self.replace_edit.text(),
            self.match_case.isChecked(), self.whole_word.isChecked()))
        btn_row = QHBoxLayout()
        btn_row.addWidget(find_prev_btn)
        btn_row.addWidget(find_next_btn)
        btn_row.addWidget(replace_btn)
        btn_row.addWidget(replace_all_btn)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addLayout(form)
        layout.addLayout(btn_row)
        layout.addWidget(self.status_label)

    def _emit_find_next(self):
        self.find_next.emit(self.find_edit.text(), self.match_case.isChecked(),
                             self.whole_word.isChecked())

    def set_status(self, text: str):
        self.status_label.setText(text)


class ExportDialog(QDialog):
    FORMATS = ["PNG", "JPG", "SVG", "PDF", "EPS"]

    def __init__(self, page_width, page_height, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export")
        self.page_width = page_width
        self.page_height = page_height
        self._aspect = page_width / page_height if page_height else 1.0

        self.format_group = QButtonGroup(self)
        format_row = QHBoxLayout()
        self.format_radios = {}
        for i, fmt in enumerate(self.FORMATS):
            rb = QRadioButton(fmt)
            rb.setChecked(i == 0)
            rb.toggled.connect(self._format_changed)
            self.format_group.addButton(rb)
            self.format_radios[fmt] = rb
            format_row.addWidget(rb)

        self.width_spin = QSpinBox()
        self.width_spin.setRange(50, 20000)
        self.width_spin.setValue(page_width)
        self.width_spin.setSuffix(" px")
        self.width_spin.valueChanged.connect(self._width_changed)

        self.height_spin = QSpinBox()
        self.height_spin.setRange(50, 20000)
        self.height_spin.setValue(page_height)
        self.height_spin.setSuffix(" px")
        self.height_spin.valueChanged.connect(self._height_changed)

        self.lock_aspect = QCheckBox("Lock aspect ratio")
        self.lock_aspect.setChecked(True)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("Quick scale:"))
        for scale in (1, 2, 3, 4):
            btn = QPushButton(f"@{scale}x")
            btn.setFixedWidth(44)
            btn.clicked.connect(lambda _checked, s=scale: self._apply_scale(s))
            scale_row.addWidget(btn)
        scale_row.addStretch(1)

        self.vector_info = QLabel()
        self.vector_info.setWordWrap(True)
        self.vector_info.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        self.vector_info.setVisible(False)

        self.bg_checkbox = QCheckBox("Include background")
        self.bg_checkbox.setChecked(True)
        self.bg_note = QLabel("")
        self.bg_note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        self.bg_note.setWordWrap(True)

        res_row_w = QHBoxLayout()
        res_row_w.addWidget(QLabel("Width"))
        res_row_w.addWidget(self.width_spin)
        res_row_h = QHBoxLayout()
        res_row_h.addWidget(QLabel("Height"))
        res_row_h.addWidget(self.height_spin)

        self.raster_widget = QWidget()
        raster_layout = QVBoxLayout(self.raster_widget)
        raster_layout.setContentsMargins(0, 0, 0, 0)
        raster_layout.setSpacing(10)
        raster_layout.addLayout(res_row_w)
        raster_layout.addLayout(res_row_h)
        raster_layout.addWidget(self.lock_aspect)
        raster_layout.addLayout(scale_row)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        layout.addWidget(QLabel("Format"))
        layout.addLayout(format_row)
        layout.addWidget(self.raster_widget)
        layout.addWidget(self.vector_info)
        layout.addWidget(self.bg_checkbox)
        layout.addWidget(self.bg_note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._export_all_requested = False
        export_all_btn = buttons.addButton("Export All Formats", QDialogButtonBox.ButtonRole.ActionRole)
        export_all_btn.setToolTip("Exports PNG, JPG, SVG, PDF, and EPS together, next to each other")
        export_all_btn.clicked.connect(self._export_all_clicked)
        layout.addWidget(buttons)

        self._format_changed()

    def _export_all_clicked(self):
        self._export_all_requested = True
        self.accept()

    def export_all_requested(self) -> bool:
        return self._export_all_requested

    def _current_format(self):
        for fmt, rb in self.format_radios.items():
            if rb.isChecked():
                return fmt
        return "PNG"

    def _format_changed(self, *_):
        fmt = self._current_format()
        is_vector = fmt in ("SVG", "PDF", "EPS")
        self.raster_widget.setVisible(not is_vector)
        self.vector_info.setVisible(is_vector)
        if is_vector:
            self.vector_info.setText(
                f"{fmt} is a vector format — it stays crisp at any size, so there's no "
                f"resolution to set. Output page size: {self.page_width} x {self.page_height} px."
            )

        if fmt == "JPG":
            self.bg_checkbox.setChecked(True)
            self.bg_checkbox.setEnabled(False)
            self.bg_note.setText("JPG has no transparency channel, so the background is always included.")
        elif fmt == "EPS":
            self.bg_checkbox.setEnabled(True)
            self.bg_note.setText(
                "EPS can't store transparency. With the background off, nothing is painted "
                "behind the text — most programs show that as see-through.")
        else:
            self.bg_checkbox.setEnabled(True)
            self.bg_note.setText("")

    def _width_changed(self, value):
        if self.lock_aspect.isChecked():
            self.height_spin.blockSignals(True)
            self.height_spin.setValue(max(1, round(value / self._aspect)))
            self.height_spin.blockSignals(False)

    def _height_changed(self, value):
        if self.lock_aspect.isChecked():
            self.width_spin.blockSignals(True)
            self.width_spin.setValue(max(1, round(value * self._aspect)))
            self.width_spin.blockSignals(False)

    def _apply_scale(self, scale):
        # Set both dimensions directly from the page size rather than going
        # through _width_changed()'s aspect-lock rounding -- repeated
        # rounding through that path could drift a pixel or two off the
        # exact multiple after a couple of clicks.
        self.width_spin.setValue(round(self.page_width * scale))
        self.height_spin.setValue(round(self.page_height * scale))

    def values(self):
        return {
            "format": self._current_format(),
            "width": self.width_spin.value(),
            "height": self.height_spin.value(),
            "include_background": self.bg_checkbox.isChecked(),
        }


class FontManagerDialog(QDialog):
    """Lets the user pick which system fonts show up in the font picker —
    handy for hiding the hundred+ Latin fonts a system typically has when
    this app only ever needs Urdu ones. Pre-checks anything with detected
    Arabic support, but every font is listed and toggleable by hand, since
    detection isn't perfect and taste varies."""

    def __init__(self, current_family=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Font Manager")
        self.resize(420, 560)

        intro = QLabel(
            "Choose which fonts show up in the font picker. Fonts with detected "
            "Arabic/Urdu support are checked by default — uncheck anything you "
            "don't want cluttering the list, or check others by hand."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {theme.TEXT_MUTED};")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search fonts…")
        self.search.textChanged.connect(self._filter)

        btn_row = QHBoxLayout()
        arabic_only_btn = QPushButton("Check Arabic-script fonts")
        arabic_only_btn.clicked.connect(self._check_arabic_only)
        all_btn = QPushButton("Check all")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Uncheck all")
        none_btn.clicked.connect(lambda: self._set_all(False))
        btn_row.addWidget(arabic_only_btn)
        btn_row.addWidget(all_btn)
        btn_row.addWidget(none_btn)

        self.list_widget = QListWidget()
        self._arabic_families = set(font_settings.arabic_capable_families())
        enabled = font_settings.get_enabled_families(current_family)
        if current_family:
            enabled.add(current_family)  # never let the active font vanish silently

        for fam in font_settings.all_families():
            item = QListWidgetItem(fam)
            item.setFont(QFont(fam, 11))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if fam in enabled else Qt.CheckState.Unchecked
            )
            if fam in self._arabic_families:
                item.setToolTip("Detected Arabic/Urdu support")
            self.list_widget.addItem(item)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(10)
        layout.addWidget(intro)
        layout.addWidget(self.search)
        layout.addLayout(btn_row)
        layout.addWidget(self.list_widget)
        layout.addWidget(buttons)

    def _filter(self, text):
        text = text.lower()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setHidden(text not in item.text().lower())

    def _set_all(self, checked):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if not item.isHidden():
                item.setCheckState(state)

    def _check_arabic_only(self):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            state = Qt.CheckState.Checked if item.text() in self._arabic_families else Qt.CheckState.Unchecked
            item.setCheckState(state)

    def enabled_families(self):
        out = set()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                out.add(item.text())
        return out


class _FontSearchPopup(QFrame):
    """The dropdown SearchableFontCombo opens in place of Qt's plain list:
    a search box above a list of fonts that narrows as you type. Each font
    is drawn in its own typeface, like the combo's normal dropdown.

    Keyboard: type to filter (every word you type must appear in the
    name, in any order — "noto urdu" finds "Noto Nastaliq Urdu"); Up/Down
    and PageUp/PageDown move through the matches; Enter picks the
    highlighted one; Esc or clicking elsewhere closes without changing
    anything."""

    MIN_WIDTH = 300
    MAX_HEIGHT = 420
    VISIBLE_ROWS = 12

    def __init__(self, combo):
        super().__init__(combo, Qt.WindowType.Popup)
        self._combo = combo
        self.setObjectName("fontSearchPopup")
        self.setStyleSheet(
            f"QFrame#fontSearchPopup {{ background-color: {theme.PANEL}; "
            f"border: 1px solid {theme.BORDER_HOVER}; border-radius: 8px; }}"
        )

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search fonts…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        self.search.installEventFilter(self)

        self.list = QListWidget()
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.itemClicked.connect(self._choose)
        self.list.itemActivated.connect(self._choose)

        self.status = QLabel()
        self.status.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.search)
        layout.addWidget(self.list)
        layout.addWidget(self.status)

        self._current_family = combo.currentText()
        for i in range(combo.count()):
            family = combo.itemText(i)
            item = QListWidgetItem(family)
            item.setFont(QFont(family, 12))
            item.setData(Qt.ItemDataRole.UserRole, i)  # row in the combo to select on pick
            self.list.addItem(item)
        self._select_current_family()
        self._update_status()

    # ------------------------------------------------------------ layout --
    def open_below_combo(self):
        combo = self._combo
        width = max(combo.width(), self.MIN_WIDTH)
        row_height = max(self.list.sizeHintForRow(0), 24) if self.list.count() else 24
        wanted = (16 + self.search.sizeHint().height() + 12 + self.status.sizeHint().height()
                  + min(self.list.count(), self.VISIBLE_ROWS) * row_height + 8)
        height = min(self.MAX_HEIGHT, wanted)
        self.resize(width, height)

        below = combo.mapToGlobal(QPoint(0, combo.height()))
        screen = combo.screen().availableGeometry()
        x = max(screen.left(), min(below.x(), screen.right() - width))
        y = below.y()
        if y + height > screen.bottom():  # not enough room underneath: open above instead
            y = max(screen.top(), combo.mapToGlobal(QPoint(0, 0)).y() - height)
        self.move(x, y)
        self.show()
        self.search.setFocus()
        current = self.list.currentItem()
        if current is not None:
            self.list.scrollToItem(current, QAbstractItemView.ScrollHint.PositionAtCenter)

    # --------------------------------------------------------- filtering --
    def _select_current_family(self):
        for i in range(self.list.count()):
            if self.list.item(i).text() == self._current_family:
                self.list.setCurrentRow(i)
                return

    def _visible_rows(self):
        return [i for i in range(self.list.count()) if not self.list.item(i).isHidden()]

    def _filter(self, text):
        words = text.lower().split()
        for i in range(self.list.count()):
            item = self.list.item(i)
            name = item.text().lower()
            item.setHidden(not all(w in name for w in words))
        rows = self._visible_rows()
        if not words:
            # Search cleared: go back to showing where the current font is.
            self._select_current_family()
            current = self.list.currentItem()
            if current is not None:
                self.list.scrollToItem(current, QAbstractItemView.ScrollHint.PositionAtCenter)
        elif rows:
            # Highlight the top match, so typing a name and pressing Enter
            # just works.
            self.list.setCurrentRow(rows[0])
            self.list.scrollToItem(self.list.item(rows[0]))
        else:
            self.list.setCurrentItem(None)
        self._update_status()

    def _update_status(self):
        shown = len(self._visible_rows())
        total = self.list.count()
        if shown == 0:
            self.status.setText("No fonts match your search")
        elif shown == total:
            self.status.setText(f"{total} fonts")
        else:
            self.status.setText(f"{shown} of {total} fonts")

    # -------------------------------------------------------- navigation --
    def _step(self, delta):
        rows = self._visible_rows()
        if not rows:
            return
        current = self.list.currentRow()
        if current in rows:
            pos = rows.index(current)
        else:
            pos = -1 if delta > 0 else len(rows)
        pos = max(0, min(len(rows) - 1, pos + delta))
        self.list.setCurrentRow(rows[pos])
        self.list.scrollToItem(self.list.item(rows[pos]))

    def eventFilter(self, obj, event):
        # Up/Down/Enter typed into the search box drive the list, so the
        # whole thing works without ever leaving the keyboard.
        if obj is self.search and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Down:
                self._step(1)
                return True
            if key == Qt.Key.Key_Up:
                self._step(-1)
                return True
            if key == Qt.Key.Key_PageDown:
                self._step(8)
                return True
            if key == Qt.Key.Key_PageUp:
                self._step(-8)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._choose(self.list.currentItem())
                return True
            if key == Qt.Key.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, event)

    def _choose(self, item):
        if item is None or item.isHidden():
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        self.close()
        self._combo.setCurrentIndex(index)
        self._combo.activated.emit(index)


class SearchableFontCombo(QComboBox):
    """A QComboBox whose dropdown has a search box. Everything else about
    it — items, currentText/currentIndex, currentTextChanged, findText —
    is stock QComboBox, so it drops in wherever a plain font combo was
    used; only what appears when you click it changes (see
    _FontSearchPopup). With a long list of installed fonts, typing part of
    a name beats scrolling for it."""

    def showPopup(self):
        if self.count() == 0:
            return
        self._popup = _FontSearchPopup(self)
        self._popup.open_below_combo()


class AboutDialog(QDialog):
    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Urdu Typewriter")
        self.setFixedWidth(360)

        icon_label = QLabel()
        icon_path = str(Path(__file__).parent / "icons" / "app-icon.svg")
        icon_label.setPixmap(QIcon(icon_path).pixmap(QSize(64, 64)))
        icon_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        title = QLabel("Urdu Typewriter")
        title.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        title.setStyleSheet(f"font-size: 18px; font-weight: 600; color: {theme.TEXT};")

        version_label = QLabel(f"Version {version}")
        version_label.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        version_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")

        blurb = QLabel(
            "A desktop typewriter for Nastaliq Urdu, with Roman-Urdu conversion, "
            "voice dictation, and export to PNG, JPG, SVG, PDF, and EPS."
        )
        blurb.setWordWrap(True)
        blurb.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        blurb.setStyleSheet(f"color: {theme.TEXT_MUTED};")

        credits = QLabel(
            "Built with PyQt6 and Qt. See README.md for third-party font and library credits."
        )
        credits.setWordWrap(True)
        credits.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        credits.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(10)
        layout.addWidget(icon_label)
        layout.addWidget(title)
        layout.addWidget(version_label)
        layout.addSpacing(8)
        layout.addWidget(blurb)
        layout.addWidget(credits)
        layout.addSpacing(8)
        layout.addWidget(buttons)


class PreferencesDialog(QDialog):
    """Defaults applied to brand-new pages (File > Page Setup's own values
    still win for the page you're currently on) plus a couple of
    app-behavior settings. Persisted via QSettings by main.py, which reads
    current values back to pre-fill this dialog and writes whatever the
    person confirms with OK — this dialog itself is display/collection
    only, the same split of responsibility as every other dialog here."""

    def __init__(self, default_family="Noto Nastaliq Urdu", default_size=36,
                 default_width=1080, default_height=1080, autosave_interval_sec=60,
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")

        self.family_combo = SearchableFontCombo()
        families = sorted(font_settings.get_enabled_families(default_family))
        for fam in families:
            self.family_combo.addItem(fam)
        idx = self.family_combo.findText(default_family)
        if idx >= 0:
            self.family_combo.setCurrentIndex(idx)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 400)
        self.size_spin.setValue(default_size)
        self.size_spin.setSuffix(" pt")

        self.width_spin = QSpinBox()
        self.width_spin.setRange(10, 20000)
        self.width_spin.setValue(default_width)
        self.width_spin.setSuffix(" px")
        self.height_spin = QSpinBox()
        self.height_spin.setRange(10, 20000)
        self.height_spin.setValue(default_height)
        self.height_spin.setSuffix(" px")
        size_row = QHBoxLayout()
        size_row.addWidget(self.width_spin)
        size_row.addWidget(QLabel("x"))
        size_row.addWidget(self.height_spin)

        self.autosave_spin = QSpinBox()
        self.autosave_spin.setRange(15, 3600)
        self.autosave_spin.setSingleStep(15)
        self.autosave_spin.setValue(autosave_interval_sec)
        self.autosave_spin.setSuffix(" sec")

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Default Font", self.family_combo)
        form.addRow("Default Size", self.size_spin)
        form.addRow("Default Page Size", size_row)
        form.addRow("Autosave Every", self.autosave_spin)

        note = QLabel(
            "Default font, size and page size are used every time the app starts, "
            "and apply right away if the page is still blank. Page Setup changes "
            "a page you've already started on."
        )
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

    def values(self):
        return {
            "default_family": self.family_combo.currentText(),
            "default_size": self.size_spin.value(),
            "default_width": self.width_spin.value(),
            "default_height": self.height_spin.value(),
            "autosave_interval_sec": self.autosave_spin.value(),
        }


class DocumentStatisticsDialog(QDialog):
    """Purely a display of numbers main.py already computed (word/char
    counts, etc.) -- no canvas access here, same reasoning as
    FindReplaceDialog: collection/display in this file, actual document
    logic stays in main.py."""

    def __init__(self, stats: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Document Statistics")
        self.setFixedWidth(300)

        form = QFormLayout()
        form.setSpacing(10)
        form.addRow("Words", QLabel(str(stats.get("words", 0))))
        form.addRow("Characters (with spaces)", QLabel(str(stats.get("characters", 0))))
        form.addRow("Characters (no spaces)", QLabel(str(stats.get("characters_no_spaces", 0))))
        form.addRow("Lines", QLabel(str(stats.get("lines", 0))))
        form.addRow("Paragraphs", QLabel(str(stats.get("paragraphs", 0))))
        form.addRow("Est. reading time", QLabel(stats.get("reading_time", "< 1 min")))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)
        layout.addLayout(form)
        layout.addWidget(buttons)


# ----------------------------------------------------------------------
# Roman -> Urdu typing guide data. Kept as plain module-level tuples
# (rather than generated from roman_urdu.py's internal tables) so this
# reference stays a stable, curated "quick lookup" independent of the
# transliteration engine's internal representation -- the single-letter
# and digraph/trigraph mappings here are fixed facts about Urdu spelling,
# verified against roman_urdu.py's actual behavior.
#
# Each row: (roman spelling, Urdu, short note).
ROMAN_TO_URDU_COMBINATIONS = (
    ("a", "ا", "Short a — often silent in the middle of a word (e.g. \"karta\")"),
    ("aa", "آ / ا", "Long a, as in \"father\" — آ at the start of a word, ا elsewhere"),
    ("ai", "ای / ے", "As in \"my\" — kaisay, chahiye"),
    ("au / aw", "او", "As in \"how\" — aurat, mauka"),
    ("e", "ای / ی / ے", "As in \"bed\"/\"day\" — changes form at start, middle, and end of a word"),
    ("ee", "ای / ی", "Long e, as in \"feet\" — deen, jeena"),
    ("i", "ا / ی", "Short i — often silent in the middle, ی at the end of a word"),
    ("o", "او / و", "As in \"go\" — omeed, sona"),
    ("oo", "او / و", "Long u, as in \"food\" — same sound as \"u\""),
    ("u", "ا / و", "Short u — often silent in the middle, و at the end of a word"),
    ("b", "ب", "As in \"bat\""),
    ("bh", "بھ", "Aspirated b — bhai, bhool"),
    ("c", "ک / س", "ک before a/o/u, س before e/i/y"),
    ("ch", "چ", "As in \"church\""),
    ("chh", "چھ", "Aspirated ch — chhota, chhori"),
    ("d", "د", "As in \"dog\""),
    ("dd", "ڈ", "Retroflex d — dda, gaddi"),
    ("ddh", "ڈھ", "Aspirated retroflex d — ddhol"),
    ("dh", "دھ", "Aspirated d — dhoop, dhoka"),
    ("f", "ف", "As in \"fan\""),
    ("g", "گ", "As in \"go\""),
    ("gh", "غ / گھ", "گھ at the very start of a word (ghar, ghanta); غ elsewhere (ghalat, baagh)"),
    ("h", "ہ", "As in \"hat\""),
    ("j", "ج", "As in \"jam\""),
    ("jh", "جھ", "Aspirated j — jhoot, jhaadu"),
    ("k", "ک", "As in \"kite\""),
    ("kh", "خ / کھ", "خ at the start of most words (khabar, khoobsurat); کھ inside common verbs (dekh, likh, khana)"),
    ("l", "ل", "As in \"lamp\""),
    ("m", "م", "As in \"man\""),
    ("n", "ن", "As in \"net\""),
    ("p", "پ", "As in \"pen\""),
    ("ph", "پھ", "Aspirated p — phool, phenk"),
    ("q", "ق", "As in \"queen\" (throat q, not kw)"),
    ("r", "ر", "As in \"run\""),
    ("rr", "ڑ", "Retroflex r — ladka, gaddi"),
    ("rrh", "ڑھ", "Aspirated retroflex r — barrhna"),
    ("s", "س", "As in \"sun\""),
    ("sh", "ش", "As in \"ship\""),
    ("t", "ت", "As in \"top\""),
    ("tt", "ٹ", "Retroflex t — tez feel, tokri"),
    ("tth", "ٹھ", "Aspirated retroflex t — tthos"),
    ("th", "تھ", "Aspirated t — theek, thanda"),
    ("w / v", "و", "As in \"win\""),
    ("x", "خ / کس", "خ at the start of a word, کس elsewhere"),
    ("y", "ی", "As in \"yes\""),
    ("z", "ز", "As in \"zoo\""),
    ("na", "نا", "Infinitive ending — karna, jaana"),
    ("ta / ti / te", "تا / تی / تے", "Present tense — karta, karti, karte"),
    ("o", "و / ؤ", "Imperative — karo, jao"),
    ("ega / egi", "ے گا / ے گی", "Future tense — karega, karegi"),
    ("ein", "یں", "Polite / plural — karein, aein"),
)

# The traditional Urdu alphabet, in its standard dictionary order.
# Each row: (Urdu letter, traditional name, common Roman spelling(s)).
URDU_ALPHABET = (
    ("ا", "Alif", "a"),
    ("آ", "Alif Madd", "aa"),
    ("ب", "Bay", "b"),
    ("پ", "Pay", "p"),
    ("ت", "Tay", "t"),
    ("ٹ", "Ttay", "tt"),
    ("ث", "Say", "s"),
    ("ج", "Jeem", "j"),
    ("چ", "Che", "ch"),
    ("ح", "Bari Hay", "h"),
    ("خ", "Khay", "kh"),
    ("د", "Daal", "d"),
    ("ڈ", "Ddaal", "dd"),
    ("ذ", "Zaal", "z"),
    ("ر", "Ray", "r"),
    ("ڑ", "Rray", "rr"),
    ("ز", "Zay", "z"),
    ("ژ", "Zhay", "zh"),
    ("س", "Seen", "s"),
    ("ش", "Sheen", "sh"),
    ("ص", "Swaad", "s"),
    ("ض", "Zwaad", "z"),
    ("ط", "Toey", "t"),
    ("ظ", "Zoey", "z"),
    ("ع", "Ain", "' (apostrophe, mid-word)"),
    ("غ", "Ghain", "gh"),
    ("ف", "Fay", "f"),
    ("ق", "Qaaf", "q"),
    ("ک", "Kaaf", "k"),
    ("گ", "Gaaf", "g"),
    ("ل", "Laam", "l"),
    ("م", "Meem", "m"),
    ("ن", "Noon", "n"),
    ("ں", "Noon Ghunna", "n (word-final nasal)"),
    ("و", "Wao", "w / v / o / oo / u"),
    ("ہ", "Choti Hay", "h"),
    ("ھ", "Do Chashmi Hay", "h (in aspirated digraphs, e.g. bh, kh)"),
    ("ء", "Hamza", "' (apostrophe) / often silent"),
    ("ی", "Choti Ye", "i / y"),
    ("ے", "Bari Ye", "e / ay (word-final)"),
)


class RomanUrduGuideDialog(QDialog):
    """A quick-reference table for the Roman -> Urdu typing scheme, with a
    toggle between two views: every recognized Roman letter/combination
    (what the app actually converts as you type) and the standard Urdu
    alphabet on its own. Pure display -- no canvas access, same split of
    responsibility as every other dialog here."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Roman → Urdu Typing Guide")
        self.resize(560, 620)

        title = QLabel("Roman → Urdu Typing Guide")
        title.setStyleSheet(f"font-size: 16px; font-weight: 600; color: {theme.TEXT};")

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")

        # Toggle: two checkable, mutually-exclusive buttons styled as a
        # single segmented control, matching the app's flat/accent theme.
        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(0)
        self.combos_btn = QPushButton("Roman → Urdu Combinations")
        self.alphabet_btn = QPushButton("Urdu Alphabet Only")
        for b in (self.combos_btn, self.alphabet_btn):
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
        self._toggle_group = QButtonGroup(self)
        self._toggle_group.setExclusive(True)
        self._toggle_group.addButton(self.combos_btn)
        self._toggle_group.addButton(self.alphabet_btn)
        self.combos_btn.setChecked(True)
        self._style_toggle()
        self.combos_btn.toggled.connect(self._on_toggle)
        toggle_row.addWidget(self.combos_btn)
        toggle_row.addWidget(self.alphabet_btn)

        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {theme.FIELD};
                alternate-background-color: {theme.PANEL};
                gridline-color: {theme.BORDER};
                border: 1px solid {theme.BORDER};
                border-radius: 6px;
            }}
            QHeaderView::section {{
                background-color: {theme.PANEL};
                color: {theme.TEXT_MUTED};
                padding: 6px;
                border: none;
                border-bottom: 1px solid {theme.BORDER};
                font-weight: 600;
            }}
        """)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        buttons.accepted.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(12)
        layout.addWidget(title)
        layout.addLayout(toggle_row)
        layout.addWidget(self.note)
        layout.addWidget(self.table, 1)
        layout.addWidget(buttons)

        self._show_combinations()

    def _style_toggle(self):
        left = f"""
            QPushButton {{
                background-color: {theme.FIELD}; color: {theme.TEXT_MUTED};
                border: 1px solid {theme.BORDER}; border-right: none;
                border-top-left-radius: 6px; border-bottom-left-radius: 6px;
                border-top-right-radius: 0; border-bottom-right-radius: 0;
                padding: 7px 10px;
            }}
            QPushButton:checked {{ background-color: {theme.ACCENT}; color: {theme.ACCENT_ON}; }}
            QPushButton:hover:!checked {{ background-color: {theme.FIELD_HOVER}; }}
        """
        right = f"""
            QPushButton {{
                background-color: {theme.FIELD}; color: {theme.TEXT_MUTED};
                border: 1px solid {theme.BORDER};
                border-top-right-radius: 6px; border-bottom-right-radius: 6px;
                border-top-left-radius: 0; border-bottom-left-radius: 0;
                padding: 7px 10px;
            }}
            QPushButton:checked {{ background-color: {theme.ACCENT}; color: {theme.ACCENT_ON}; }}
            QPushButton:hover:!checked {{ background-color: {theme.FIELD_HOVER}; }}
        """
        self.combos_btn.setStyleSheet(left)
        self.alphabet_btn.setStyleSheet(right)

    def _on_toggle(self, checked):
        if checked:
            self._show_combinations()
        else:
            self._show_alphabet()

    def _fill(self, headers, rows, urdu_columns):
        """urdu_columns: indices of columns that hold Urdu script, so they
        get the RTL alignment + a larger font for legibility."""
        self.table.clear()
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        urdu_font = QFont()
        urdu_font.setPointSize(14)
        for r, row in enumerate(rows):
            for c, value in enumerate(row):
                item = QTableWidgetItem(value)
                if c in urdu_columns:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                    item.setFont(urdu_font)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self.table.setItem(r, c, item)
        header = self.table.horizontalHeader()
        for c in range(len(headers)):
            mode = QHeaderView.ResizeMode.ResizeToContents if c != len(headers) - 1 \
                else QHeaderView.ResizeMode.Stretch
            header.setSectionResizeMode(c, mode)

    def _show_combinations(self):
        self.note.setText(
            "Every Roman letter and letter-combination the app recognizes while "
            "you type, with the Urdu it converts to."
        )
        rows = [[roman, urdu, note] for roman, urdu, note in ROMAN_TO_URDU_COMBINATIONS]
        self._fill(["Roman", "Urdu", "Notes"], rows, urdu_columns={1})

    def _show_alphabet(self):
        self.note.setText(
            "The standard Urdu alphabet on its own, in traditional order, with each "
            "letter's name and its usual Roman spelling."
        )
        rows = [[urdu, name, roman] for urdu, name, roman in URDU_ALPHABET]
        self._fill(["Urdu", "Name", "Roman"], rows, urdu_columns={0})
