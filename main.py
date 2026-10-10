import sys
import json
import collections
from pathlib import Path
from datetime import datetime

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QToolBar, QComboBox, QSpinBox, QLabel,
    QFileDialog, QMessageBox, QGraphicsScene, QGraphicsView, QWidget,
    QVBoxLayout, QSizePolicy, QInputDialog, QGraphicsDropShadowEffect
)
from PyQt6.QtGui import (
    QAction, QActionGroup, QFontDatabase, QFont, QColor, QPainter, QPixmap,
    QKeySequence, QIcon, QImage, QTextDocument,
)
from PyQt6.QtCore import (
    Qt, pyqtSignal, QThread, QEvent, QTimer, QSize, QSettings, QStandardPaths,
)
from PyQt6.QtPrintSupport import QPrinter, QPrintDialog

from canvas import PageCanvas
from dialogs import (
    NewPageDialog, ExportDialog, ColorSwatchButton, FontManagerDialog,
    FindReplaceDialog, ColorPickerDialog, SearchableFontCombo, _downscale_if_needed,
)
from font_check import supports_arabic_joining
from voice_overlay import VoiceOverlayButton
import font_gap
import font_settings
import project_io
import exporter
import theme

APP_TITLE = "Urdu Typewriter"
APP_VERSION = "2.3"
BUNDLED_FONT_PATH = Path(__file__).parent / "fonts" / "NotoNastaliqUrdu-Regular.ttf"
ICONS_DIR = Path(__file__).parent / "icons"


def load_bundled_fonts():
    """Registers every bundled font weight and returns the base family name
    Qt actually assigned (never hard-code the family string)."""
    folder = Path(__file__).parent / "fonts"
    family = None
    if folder.exists():
        for f in sorted(folder.glob("*.ttf")):
            font_id = QFontDatabase.addApplicationFont(str(f))
            if font_id != -1:
                fams = QFontDatabase.applicationFontFamilies(font_id)
                if fams and "Nastaliq" in fams[0]:
                    family = fams[0]
    return family or "Arial"


class ClickableLabel(QLabel):
    """A QLabel that emits clicked on a left-click — QLabel has no such
    signal built in. Used for the zoom percentage: click it to reset to
    100%, the same convenience most creative apps offer next to a zoom
    readout rather than making a trip to a menu for it."""
    clicked = pyqtSignal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class ZoomableGraphicsView(QGraphicsView):
    """Hosts the page via a QGraphicsProxyWidget so it can be zoomed with a
    transform (Ctrl+scroll, or the toolbar +/-) without touching the page's
    actual pixel dimensions — zoom is purely a viewing convenience, export
    size is set separately in the Export dialog.

    Zoom-to-cursor is done by hand (_zoom_to below) instead of via Qt's
    built-in AnchorUnderMouse. AnchorUnderMouse only tracks the cursor
    correctly when the wheel event travels through QGraphicsView's own
    event pipeline — but the page widget (a QTextEdit) swallows wheel
    events itself, so MainWindow's event filter catches them on the
    widget and replicates the zoom here instead of redelivering the event
    (redelivering would recurse — see MainWindow.eventFilter). Qt never
    sees that as "the mouse is over the view," so AnchorUnderMouse
    anchored on the wrong point (this is the bug being fixed here).
    Comparing scene coordinates before/after and correcting the
    scrollbars ourselves works regardless of where the event came from.

    That fix alone isn't sufficient, though: it corrects the scrollbars,
    but there's nothing to correct if there's no scrollable range to
    begin with. Whenever the (zoomed) page fits inside the viewport on
    some axis, QGraphicsView's alignment forces that axis to dead center
    no matter what value we set — Qt considers there to be "nothing to
    scroll" and ignores it entirely. See MainWindow.PASTEBOARD_MARGIN for
    the other half of this fix: a large scrollable margin kept around the
    page so that condition essentially never arises.
    """
    zoomChanged = pyqtSignal(float)
    MIN_ZOOM = 0.1
    MAX_ZOOM = 6.0

    def __init__(self, scene, parent=None):
        super().__init__(scene, parent)
        self._zoom = 1.0
        # Set right after construction, in MainWindow.__init__, so
        # wheelEvent below can check the same context_menu_open flag
        # PageCanvas.contextMenuEvent sets (see that method's docstring).
        # Stays None (and is checked defensively below) if this view is
        # ever used without a canvas, e.g. under test.
        self.canvas = None
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # NoAnchor: we manage the anchor point ourselves for every zoom
        # (see _zoom_to), so Qt doesn't need to do its own adjustment
        # first — see the class docstring for why the built-in anchors
        # aren't reliable here.
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)

    def wheelEvent(self, event):
        # Skip entirely while the standard right-click menu is open (see
        # PageCanvas.contextMenuEvent). MainWindow.eventFilter has the
        # same check for wheel events landing on the embedded page, but
        # this wheelEvent is the *other* path — wheel events that land
        # directly on the view, e.g. the backdrop around the page —
        # which is exactly where the cursor tends to be at a high zoom
        # level (the page fills less of the window, so more of it is
        # backdrop). Without this, scrolling there while the menu is
        # open still zoomed/scrolled the page out from under it.
        if self.canvas is not None and self.canvas.context_menu_open:
            event.accept()
            return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Zoom around wherever the cursor is — the standard Adobe/
            # Affinity-style behavior — rather than the viewport center.
            # Only for wheel-triggered zoom: a toolbar +/- click or a
            # keyboard shortcut has no meaningful "cursor over the canvas"
            # position, so those keep the view centered instead (see
            # set_zoom below). This handles wheel events that land
            # directly on the view (e.g. the backdrop around the page);
            # zoom_from_canvas_wheel below handles the far more common
            # case of wheel events over the page itself.
            factor = 1.15 if event.angleDelta().y() > 0 else (1 / 1.15)
            anchor = self.mapToScene(event.position().toPoint())
            self._zoom_to(self._zoom * factor, anchor)
            event.accept()
        else:
            super().wheelEvent(event)

    def zoom_from_canvas_wheel(self, canvas_local_pos, angle_delta_y, canvas_proxy):
        """Cursor-anchored zoom for a wheel event that landed on the
        embedded page widget instead of the view directly — relayed here
        from MainWindow.eventFilter. `canvas_local_pos` is the event's
        position in the page widget's own local coordinates; `canvas_proxy`
        is the QGraphicsProxyWidget hosting that widget, used to translate
        those coordinates into the scene."""
        factor = 1.15 if angle_delta_y > 0 else (1 / 1.15)
        anchor = canvas_proxy.mapToScene(canvas_local_pos)
        self._zoom_to(self._zoom * factor, anchor)

    def set_zoom(self, zoom):
        """Sets an absolute zoom level, anchored on the current view
        center — used by the toolbar +/- buttons, the View menu, and
        Ctrl+=/Ctrl+-/Ctrl+0, none of which have a meaningful cursor
        position over the page to anchor to instead."""
        center_anchor = self.mapToScene(self.viewport().rect().center())
        self._zoom_to(zoom, center_anchor)

    def _zoom_to(self, new_zoom: float, scene_anchor):
        """Changes the zoom level while keeping `scene_anchor` (a point in
        scene coordinates) visually fixed at the same viewport pixel it
        occupied before the change: record where it sits before, scale,
        then nudge the scrollbars by however far that same point moved."""
        new_zoom = max(self.MIN_ZOOM, min(new_zoom, self.MAX_ZOOM))
        if abs(new_zoom - self._zoom) < 1e-9:
            return
        before = self.mapFromScene(scene_anchor)
        factor = new_zoom / self._zoom
        self.scale(factor, factor)
        self._zoom = new_zoom
        after = self.mapFromScene(scene_anchor)
        delta = after - before
        if delta.x() or delta.y():
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() + delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + delta.y())
        self.zoomChanged.emit(new_zoom)

    def zoom(self):
        return self._zoom


class TranscribeThread(QThread):
    """Runs (potentially slow, CPU-bound) Whisper transcription off the GUI
    thread, for one audio segment at a time — a whole recording in the old
    click-twice flow, or a single live-dictation chunk in the current one
    (see UtteranceSegmenter in voice_to_urdu.py and the recording queue
    below). Recording itself doesn't need this — sounddevice captures via
    its own internal audio thread — only the transcription step does."""
    finished_text = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, audio, parent=None):
        super().__init__(parent)
        self.audio = audio

    def run(self):
        try:
            import voice_to_urdu
            text = voice_to_urdu.transcribe_urdu(self.audio)
            self.finished_text.emit(text)
        except Exception as e:
            self.error.emit(str(e))


class ModelLoadThread(QThread):
    """Loads the Whisper model in the background at startup, so it's warm
    before the user's first click rather than making them wait for it."""
    loaded = pyqtSignal()
    failed = pyqtSignal(str)

    def run(self):
        try:
            import voice_to_urdu
            voice_to_urdu.get_model()
            self.loaded.emit()
        except Exception as e:
            self.failed.emit(str(e))


class ExportThread(QThread):
    """Runs a file export (or an SVG clipboard copy) off the GUI thread --
    SVG rendering in particular walks the document's glyph outlines (see
    text_outlines.py), which can take a real, noticeable moment on a long
    page and would otherwise freeze the window for it.

    Callers must build `export_fn` from data that's already been *copied*
    off the live canvas on the GUI thread first (QTextDocument.clone(),
    QImage.copy()) -- see open_export_dialog() below. QTextDocument isn't
    safe to touch concurrently from two threads, so this thread must never
    reach back into self.canvas or anything else still live on the GUI
    side; it only touches the independent copies baked into export_fn."""
    succeeded = pyqtSignal()
    failed = pyqtSignal(str)

    def __init__(self, export_fn, parent=None):
        super().__init__(parent)
        self._export_fn = export_fn

    def run(self):
        try:
            self._export_fn()
            self.succeeded.emit()
        except Exception as e:
            self.failed.emit(str(e))


class MainWindow(QMainWindow):
    POLL_INTERVAL_MS = 200  # how often we check the mic buffer for a finished utterance

    # Scrollable margin kept around the page in every direction (scene-
    # space px). Without this, once the (zoomed) page is smaller than the
    # window in some dimension, QGraphicsView's AlignCenter forces that
    # axis back to dead-center no matter what scrollbar position we ask
    # for -- there's simply no scrollable range to move within, since
    # Qt considers there's "nothing to scroll" when content <= viewport.
    # That's what made Ctrl+scroll zoom always anchor on the page's
    # center instead of the cursor: this app's default page (794px wide)
    # is narrower than its default window, so that "nothing to scroll
    # horizontally" case was the normal starting condition, not an edge
    # case. 20000px comfortably keeps the (padded) scene bigger than the
    # viewport even on a 4K-wide window at the most zoomed-out level this
    # app allows (ZoomableGraphicsView.MIN_ZOOM = 0.1).
    PASTEBOARD_MARGIN = 20000

    def __init__(self):
        super().__init__()
        self.setAcceptDrops(True)
        self.current_project_path = None
        self.voice_recorder = None
        self._transcribe_thread = None
        self._segmenter = None
        self._pending_chunks = collections.deque()
        self._silence_warning_shown = False
        self._recording_poll_ticks = 0
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
        self._poll_timer.timeout.connect(self._poll_recording)

        self._export_thread = None
        self._find_dialog = None
        self._recent_files = self._load_recent_files()
        self._autosave_warned = False
        self._format_painter_active = False
        self._format_painter_source = None
        self._last_export_paths = []

        default_family = load_bundled_fonts()

        # Preferences (Tools > Preferences…) were being saved but never read
        # back anywhere except to pre-fill the dialog itself, so changing
        # them appeared to do nothing. Read them here, where the first page
        # is created. The fallbacks are this app's original built-in start
        # (A4-ish 794x1123 page, 36pt, the bundled Nastaliq font), so a
        # fresh install with nothing saved behaves exactly as before.
        prefs = QSettings(font_settings.ORG, font_settings.APP)
        saved_family = prefs.value("prefs/defaultFamily", "", type=str)
        # Only adopt a saved font that's still available and enabled: one
        # that's since been uninstalled or hidden would otherwise leave the
        # page (and the font picker) pointing at a family that isn't there.
        if saved_family and saved_family in font_settings.get_enabled_families(default_family):
            default_family = saved_family
        default_size = prefs.value("prefs/defaultSize", 36, type=int)
        page_width = prefs.value("prefs/defaultWidth", 794, type=int)
        page_height = prefs.value("prefs/defaultHeight", 1123, type=int)

        self.canvas = PageCanvas(page_width, page_height, QColor("white"), 40,
                                  default_family=default_family, default_style="Regular",
                                  default_size=default_size)

        # EXPERIMENTAL (see font_gap.py): a small automatic gap between
        # neighbouring words written in different fonts. Lives purely in the
        # page layout -- not in the document, its undo history, or saved
        # files -- and is switched from Tools > Gap Between Fonts. The saved
        # level is read here so even the first page has it.
        gap_level = prefs.value("experimental/fontGapLevel", font_gap.DEFAULT_LEVEL, type=str)
        self.font_gap = font_gap.FontGapManager(self.canvas.document(), gap_level, parent=self)
        self.font_gap.failed.connect(self._on_font_gap_failed)

        self.scene = QGraphicsScene()
        self.scene.setBackgroundBrush(QColor(theme.CANVAS_BACKDROP))
        self.canvas_proxy = self.scene.addWidget(self.canvas)
        page_shadow = QGraphicsDropShadowEffect()
        page_shadow.setBlurRadius(32)
        page_shadow.setColor(QColor(0, 0, 0, 160))
        page_shadow.setOffset(0, 6)
        self.canvas_proxy.setGraphicsEffect(page_shadow)
        self._sync_scene_rect()

        self.view = ZoomableGraphicsView(self.scene)
        self.view.canvas = self.canvas
        self.view.zoomChanged.connect(self._on_zoom_changed)
        self.canvas.installEventFilter(self)
        self._sync_scene_rect()  # re-run now that self.view exists, to set the initial scroll position

        self.font_warning = QLabel()
        self.font_warning.setObjectName("fontWarning")
        self.font_warning.setWordWrap(True)
        self.font_warning.setVisible(False)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.font_warning)
        central_layout.addWidget(self.view)
        self.setCentralWidget(central)

        self._build_menu()
        self._build_toolbar(default_family)
        self._init_voice_overlay()
        self._refresh_recent_files_menu()

        self.word_count_label = QLabel()
        self.word_count_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; padding-right: 6px;")
        self.statusBar().addPermanentWidget(self.word_count_label)
        self._word_count_timer = QTimer(self)
        self._word_count_timer.setSingleShot(True)
        self._word_count_timer.setInterval(300)  # debounced -- see _refresh_word_count's caller below
        self._word_count_timer.timeout.connect(self._refresh_word_count)
        self.canvas.textChanged.connect(self._word_count_timer.start)
        self.canvas.selectionChanged.connect(self._on_selection_changed_for_format_painter)
        self._refresh_word_count()

        self._autosave_timer = QTimer(self)
        # The saved interval (Preferences), not a hardcoded 60s: this used
        # to only take effect after reopening Preferences in the same
        # session, and reset to 60s on every launch. Floor at 5s so a
        # corrupted/zero value can't turn autosave into a tight loop.
        self._autosave_timer.setInterval(
            max(5, prefs.value("prefs/autosaveIntervalSec", 60, type=int)) * 1000)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._autosave_timer.start()

        self.canvas.document().setModified(False)  # toolbar setup touches char formats; that isn't "user modified"
        self.canvas.document().modificationChanged.connect(self._update_title)
        self.canvas.document().modificationChanged.connect(self.save_menu_action.setEnabled)
        self._update_title()
        self._update_status()
        self._update_font_warning(default_family)
        self._restore_window_geometry()
        # Re-run once more once the event loop has actually laid the
        # window out (self.resize() above doesn't synchronously give the
        # viewport its final size — only processing events does), so the
        # initial "show the page's top, centered" positioning uses the
        # real viewport size instead of whatever default/interim size
        # the view happened to have immediately after construction. The
        # voice button's default bottom-right position has the exact same
        # issue for the exact same reason, so it gets the same fix here.
        QTimer.singleShot(0, self._sync_scene_rect)
        QTimer.singleShot(0, self.voice_overlay.reposition_default)
        # Same reasoning again: deferred so the recovery-prompt dialog (if
        # any) appears on top of an already-shown, already-positioned
        # window instead of racing its construction.
        QTimer.singleShot(0, self._check_for_recovery_file)

    # ------------------------------------------------------------- setup --
    def _build_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("&File")

        page_setup_act = QAction("Page Setup…", self)
        page_setup_act.setShortcut(QKeySequence("Ctrl+N"))
        page_setup_act.triggered.connect(self.open_new_page_dialog)
        file_menu.addAction(page_setup_act)
        file_menu.addSeparator()

        open_act = QAction("Open Project…", self)
        open_act.setShortcut(QKeySequence.StandardKey.Open)
        open_act.triggered.connect(self.open_project)
        file_menu.addAction(open_act)

        self.recent_files_menu = file_menu.addMenu("Open Recent")

        self.save_menu_action = QAction("Save Project", self)
        self.save_menu_action.setShortcut(QKeySequence.StandardKey.Save)
        self.save_menu_action.triggered.connect(self.save_project)
        self.save_menu_action.setEnabled(False)  # nothing modified yet -- see modificationChanged wiring below
        file_menu.addAction(self.save_menu_action)

        save_as_act = QAction("Save Project As…", self)
        save_as_act.setShortcut(QKeySequence("Ctrl+Shift+S"))
        save_as_act.triggered.connect(self.save_project_as)
        file_menu.addAction(save_as_act)
        file_menu.addSeparator()

        self.export_menu_action = QAction("Export…", self)
        self.export_menu_action.setShortcut(QKeySequence("Ctrl+E"))
        self.export_menu_action.triggered.connect(self.open_export_dialog)
        file_menu.addAction(self.export_menu_action)

        copy_image_menu_act = QAction("Copy as Image", self)
        copy_image_menu_act.setShortcut(QKeySequence("Ctrl+Shift+C"))
        copy_image_menu_act.triggered.connect(self.copy_as_image)
        file_menu.addAction(copy_image_menu_act)

        self.copy_svg_menu_act = QAction("Copy as SVG (Curves)", self)
        self.copy_svg_menu_act.setShortcut(QKeySequence("Ctrl+Alt+Shift+C"))
        self.copy_svg_menu_act.setToolTip(
            "Copies the selected text (or all the text on the page, if nothing is "
            "selected) as scalable vector curves — outlined shapes, not font "
            "references, so it looks identical on any machine, even without the font "
            "installed. Only the letterforms are copied, cropped tight: no page "
            "background, border, or empty page area. The clipboard gets real vector "
            "data in every form an app might look for — SVG, plus a PDF of the same "
            "curves (on Windows also under the name \"Portable Document Format\", "
            "the name Affinity itself uses for PDF) — and never a picture. If an app "
            "still pastes nothing, try its Paste Special, or use Export ▸ SVG / PDF / "
            "EPS and the app's Open/Place instead. Use Copy as Image for a plain picture."
        )
        self.copy_svg_menu_act.triggered.connect(self.copy_as_svg)
        file_menu.addAction(self.copy_svg_menu_act)
        # ...and the same action in the page's right-click menu.
        self.canvas.set_extra_context_actions([self.copy_svg_menu_act])

        print_act = QAction("Print…", self)
        print_act.setShortcut(QKeySequence.StandardKey.Print)
        print_act.triggered.connect(self.print_page)
        file_menu.addAction(print_act)
        file_menu.addSeparator()

        exit_act = QAction("Exit", self)
        exit_act.setShortcut(QKeySequence.StandardKey.Quit)
        exit_act.triggered.connect(self.close)
        file_menu.addAction(exit_act)

        edit_menu = menubar.addMenu("&Edit")

        self.undo_action = QAction("Undo", self)
        self.undo_action.setShortcut(QKeySequence.StandardKey.Undo)
        self.undo_action.triggered.connect(self.canvas.undo)
        self.undo_action.setEnabled(False)
        edit_menu.addAction(self.undo_action)

        self.redo_action = QAction("Redo", self)
        self.redo_action.setShortcut(QKeySequence.StandardKey.Redo)
        self.redo_action.triggered.connect(self.canvas.redo)
        self.redo_action.setEnabled(False)
        edit_menu.addAction(self.redo_action)
        edit_menu.addSeparator()

        self.canvas.document().undoAvailable.connect(self.undo_action.setEnabled)
        self.canvas.document().redoAvailable.connect(self.redo_action.setEnabled)

        cut_act = QAction("Cut", self)
        cut_act.setShortcut(QKeySequence.StandardKey.Cut)
        cut_act.triggered.connect(self.canvas.cut)
        edit_menu.addAction(cut_act)
        copy_act = QAction("Copy", self)
        copy_act.setShortcut(QKeySequence.StandardKey.Copy)
        copy_act.triggered.connect(self.canvas.copy)
        edit_menu.addAction(copy_act)
        paste_act = QAction("Paste", self)
        paste_act.setShortcut(QKeySequence.StandardKey.Paste)
        paste_act.triggered.connect(self.canvas.paste)
        edit_menu.addAction(paste_act)
        edit_menu.addSeparator()

        select_all_act = QAction("Select All", self)
        select_all_act.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all_act.triggered.connect(self.canvas.selectAll)
        edit_menu.addAction(select_all_act)
        edit_menu.addSeparator()

        find_act = QAction("Find && Replace…", self)
        find_act.setShortcut(QKeySequence.StandardKey.Find)
        find_act.triggered.connect(self.open_find_replace)
        edit_menu.addAction(find_act)
        edit_menu.addSeparator()

        convert_sel_act = QAction("Convert Selection to Urdu", self)
        convert_sel_act.setToolTip("Transliterates a Roman-Urdu selection to Urdu script")
        convert_sel_act.triggered.connect(self.convert_selection_to_urdu)
        edit_menu.addAction(convert_sel_act)
        edit_menu.addSeparator()

        insert_date_act = QAction("Insert Date", self)
        insert_date_act.triggered.connect(self.insert_date)
        edit_menu.addAction(insert_date_act)

        # -- Format menu ---------------------------------------------------
        # underline_action/strikeout_action are shared with the toolbar
        # (_build_toolbar() below adds these same QAction objects rather
        # than creating separate ones) — one QAction added to two widgets
        # stays in sync automatically, so the menu checkmark and the
        # toolbar button never need to be reconciled by hand.
        format_menu = menubar.addMenu("F&ormat")

        self.underline_action = QAction("Underline", self)
        self.underline_action.setCheckable(True)
        self.underline_action.setShortcut(QKeySequence("Ctrl+U"))
        self.underline_action.toggled.connect(self.canvas.set_pending_underline)
        format_menu.addAction(self.underline_action)

        self.strikeout_action = QAction("Strikethrough", self)
        self.strikeout_action.setCheckable(True)
        self.strikeout_action.toggled.connect(self.canvas.set_pending_strikeout)
        format_menu.addAction(self.strikeout_action)
        format_menu.addSeparator()

        highlight_act = QAction("Highlight Color…", self)
        highlight_act.triggered.connect(self.choose_highlight_color)
        format_menu.addAction(highlight_act)
        clear_highlight_act = QAction("Clear Highlight", self)
        clear_highlight_act.triggered.connect(lambda: self.canvas.set_pending_highlight(None))
        format_menu.addAction(clear_highlight_act)
        format_menu.addSeparator()

        letter_spacing_act = QAction("Letter Spacing…", self)
        letter_spacing_act.triggered.connect(self.choose_letter_spacing)
        format_menu.addAction(letter_spacing_act)
        line_spacing_act = QAction("Line Height…", self)
        line_spacing_act.triggered.connect(self.choose_line_spacing)
        format_menu.addAction(line_spacing_act)
        word_spacing_act = QAction("Word Spacing…", self)
        word_spacing_act.triggered.connect(self.choose_word_spacing)
        format_menu.addAction(word_spacing_act)
        format_menu.addSeparator()

        self.superscript_action = QAction("Superscript", self)
        self.superscript_action.setCheckable(True)
        self.superscript_action.toggled.connect(self._on_superscript_toggled)
        format_menu.addAction(self.superscript_action)

        self.subscript_action = QAction("Subscript", self)
        self.subscript_action.setCheckable(True)
        self.subscript_action.toggled.connect(self._on_subscript_toggled)
        format_menu.addAction(self.subscript_action)
        format_menu.addSeparator()

        clear_format_act = QAction("Clear Formatting", self)
        clear_format_act.setShortcut(QKeySequence("Ctrl+\\"))
        clear_format_act.triggered.connect(self.clear_formatting)
        format_menu.addAction(clear_format_act)

        format_painter_act = QAction("Format Painter", self)
        format_painter_act.setToolTip(
            "Copies the formatting at the cursor or selection, then applies it to the next thing you select")
        format_painter_act.triggered.connect(self.activate_format_painter)
        format_menu.addAction(format_painter_act)

        view_menu = menubar.addMenu("&View")
        zoom_in_act = QAction("Zoom In", self)
        zoom_in_act.setShortcut(QKeySequence("Ctrl+="))
        zoom_in_act.triggered.connect(lambda: self.view.set_zoom(self.view.zoom() * 1.2))
        view_menu.addAction(zoom_in_act)
        zoom_out_act = QAction("Zoom Out", self)
        zoom_out_act.setShortcut(QKeySequence("Ctrl+-"))
        zoom_out_act.triggered.connect(lambda: self.view.set_zoom(self.view.zoom() / 1.2))
        view_menu.addAction(zoom_out_act)
        zoom_reset_act = QAction("Reset Zoom (100%)", self)
        zoom_reset_act.setShortcut(QKeySequence("Ctrl+0"))
        zoom_reset_act.triggered.connect(lambda: self.view.set_zoom(1.0))
        view_menu.addAction(zoom_reset_act)
        fit_window_act = QAction("Fit to Window", self)
        fit_window_act.setShortcut(QKeySequence("Ctrl+9"))
        fit_window_act.triggered.connect(self.fit_zoom_to_window)
        view_menu.addAction(fit_window_act)
        view_menu.addSeparator()
        for pct in (25, 50, 100, 150, 200, 400):
            preset_act = QAction(f"{pct}%", self)
            preset_act.triggered.connect(lambda checked=False, p=pct: self.view.set_zoom(p / 100.0))
            view_menu.addAction(preset_act)
        view_menu.addSeparator()
        stats_act = QAction("Document Statistics…", self)
        stats_act.triggered.connect(self.show_document_statistics)
        view_menu.addAction(stats_act)

        fonts_menu = menubar.addMenu("F&onts")
        manage_fonts_act = QAction("Manage Fonts…", self)
        manage_fonts_act.triggered.connect(self.open_font_manager)
        fonts_menu.addAction(manage_fonts_act)

        tools_menu = menubar.addMenu("&Tools")
        prefs_act = QAction("Preferences…", self)
        prefs_act.setShortcut(QKeySequence("Ctrl+,"))
        prefs_act.triggered.connect(self.open_preferences)
        tools_menu.addAction(prefs_act)
        guide_act = QAction("Roman → Urdu Typing Guide…", self)
        guide_act.triggered.connect(self.open_roman_urdu_guide)
        tools_menu.addAction(guide_act)

        # EXPERIMENTAL -- see font_gap.py. Exclusive choice, Off to Wide.
        tools_menu.addSeparator()
        gap_menu = tools_menu.addMenu("Gap Between Fonts (Experimental)")
        gap_menu.setToolTipsVisible(True)
        gap_menu.setToolTip(
            "When words in different fonts sit next to each other on a line, adds a small "
            "extra gap between them so they don't touch. Affects the page, every export, "
            "Copy as SVG, and printing alike. Nothing is stored in your text or files — "
            "choose Off to see the page exactly as it was.")
        self._font_gap_group = QActionGroup(self)
        self._font_gap_group.setExclusive(True)
        self._font_gap_actions = {}
        for level in ("off", "subtle", "normal", "wide"):
            act = QAction(font_gap.LEVEL_LABELS[level], self)
            act.setCheckable(True)
            act.setChecked(level == self.font_gap.level())
            act.triggered.connect(lambda _checked=False, lv=level: self._set_font_gap_level(lv))
            self._font_gap_group.addAction(act)
            gap_menu.addAction(act)
            self._font_gap_actions[level] = act

        help_menu = menubar.addMenu("&Help")
        about_act = QAction("About Urdu Typewriter", self)
        about_act.triggered.connect(self.show_about)
        help_menu.addAction(about_act)

    def _set_font_gap_level(self, level):
        self.font_gap.set_level(level)
        QSettings(font_settings.ORG, font_settings.APP).setValue("experimental/fontGapLevel", level)
        for lv, act in self._font_gap_actions.items():
            act.setChecked(lv == level)
        self.statusBar().showMessage(
            "Gap between fonts: off" if level == "off"
            else f"Gap between fonts: {font_gap.LEVEL_LABELS[level].lower()}", 4000)

    def _on_font_gap_failed(self, message):
        # font_gap already restored the page and switched itself off.
        for lv, act in getattr(self, "_font_gap_actions", {}).items():
            act.setChecked(lv == "off")
        self.statusBar().showMessage(
            f"Gap between fonts was turned off after an error ({message})", 10000)

    def _build_toolbar(self, default_family):
        tb = QToolBar("Main")
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(16, 16))
        self.addToolBar(tb)

        self.font_combo = SearchableFontCombo()
        self.font_combo.setMinimumWidth(170)
        self.font_combo.setToolTip(
            "Font family — applies to the current selection, or what you type next. "
            "Click to open the list; type to search.")
        self.font_combo.currentTextChanged.connect(self._on_font_combo_changed)
        tb.addWidget(self.font_combo)

        self.style_combo = QComboBox()
        self.style_combo.setMinimumWidth(100)
        self.style_combo.setToolTip("Font style/weight, as offered by the selected font family")
        self.style_combo.currentTextChanged.connect(self._on_style_combo_changed)
        tb.addWidget(self.style_combo)

        self._refresh_font_combo(select=default_family)

        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 400)
        self.size_spin.setValue(self.canvas.pending_size())
        self.size_spin.setToolTip("Font size (points)")
        self.size_spin.valueChanged.connect(self.canvas.set_pending_size)
        tb.addWidget(self.size_spin)
        tb.addSeparator()

        tb.addWidget(QLabel("Color"))
        self.color_button = ColorSwatchButton(self.canvas.pending_color())
        self.color_button.setToolTip("Text color — click to choose")
        self.color_button.colorChanged.connect(self.canvas.set_pending_color)
        tb.addWidget(self.color_button)
        tb.addSeparator()

        align_group = QActionGroup(self)
        self.alignment_actions = []
        for label, alignment, icon_name in (
            ("Right", Qt.AlignmentFlag.AlignRight, "align-right"),
            ("Center", Qt.AlignmentFlag.AlignCenter, "align-center"),
            ("Left", Qt.AlignmentFlag.AlignLeft, "align-left"),
        ):
            act = QAction(QIcon(str(ICONS_DIR / f"{icon_name}.svg")), label, self)
            act.setToolTip(f"Align {label.lower()}")
            act.setCheckable(True)
            act.setChecked(label == "Right")
            act.triggered.connect(lambda checked, a=alignment: self.canvas.apply_alignment(a))
            align_group.addAction(act)
            tb.addAction(act)
            # Icon-only for this action specifically (its tooltip still
            # carries the label) rather than changing the whole toolbar's
            # button style, which would leave icon-less actions like
            # "Roman → Urdu" below with no visible label at all.
            tb.widgetForAction(act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
            self.alignment_actions.append(act)
        tb.addSeparator()

        # Line height and word spacing: an icon and a number box each, and
        # the page updates the instant the number changes (arrows, typing,
        # or the mouse wheel) -- no OK button. Word spacing moves whole
        # words apart only; letter spacing is a separate setting (Format >
        # Letter Spacing…) and deliberately isn't here.
        lh_lo, lh_hi = PageCanvas.LINE_HEIGHT_RANGE
        self.line_height_spin = self._add_spacing_control(
            tb, "line-height", lh_lo, lh_hi, 5, "%", self.canvas.apply_line_spacing,
            "Line height — space between lines, as a % of normal (100 = single, "
            "150 = 1.5×). Updates the page live; applies to the current paragraph, "
            "or every paragraph in the selection.")
        ws_lo, ws_hi = PageCanvas.WORD_SPACING_RANGE
        self.word_spacing_spin = self._add_spacing_control(
            tb, "word-spacing", ws_lo, ws_hi, 1, " px", self.canvas.set_word_spacing,
            "Word spacing — extra space between words, in pixels (0 = normal, "
            "negative = tighter). Updates the page live; applies to the selection, "
            "or the current paragraph if nothing is selected. Doesn't change the "
            "spacing between letters.")
        tb.addSeparator()

        # Keep both boxes showing the text under the cursor / selection
        # (and following undo/redo), not just whatever was typed last.
        self.canvas.cursorPositionChanged.connect(self._sync_spacing_controls)
        self.canvas.selectionChanged.connect(self._sync_spacing_controls)
        self.canvas.document().contentsChanged.connect(self._sync_spacing_controls)
        self._sync_spacing_controls()

        self.roman_urdu_act = QAction("Roman → Urdu", self)
        self.roman_urdu_act.setCheckable(True)
        self.roman_urdu_act.setToolTip(
            "When on, finishing a Latin word (space, punctuation, or clicking away) "
            "converts it to Urdu script automatically. Try: type 'school' then a space."
        )
        self.roman_urdu_act.toggled.connect(self._toggle_roman_urdu)
        tb.addAction(self.roman_urdu_act)

        guide_act = QAction("Guide", self)
        guide_act.setToolTip("Roman → Urdu typing guide — see every letter combination, or just the Urdu alphabet")
        guide_act.triggered.connect(self.open_roman_urdu_guide)
        tb.addAction(guide_act)
        tb.addSeparator()

        zoom_out_act = QAction(QIcon(str(ICONS_DIR / "zoom-out.svg")), "Zoom Out", self)
        zoom_out_act.setToolTip("Zoom out (Ctrl+-, or Ctrl+scroll)")
        zoom_out_act.triggered.connect(lambda: self.view.set_zoom(self.view.zoom() / 1.2))
        tb.addAction(zoom_out_act)
        tb.widgetForAction(zoom_out_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        self.zoom_label = ClickableLabel("100%")
        self.zoom_label.setMinimumWidth(44)
        self.zoom_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.zoom_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.zoom_label.setToolTip("Click to enter an exact zoom percentage")
        self.zoom_label.clicked.connect(self._enter_exact_zoom)
        tb.addWidget(self.zoom_label)

        zoom_in_act = QAction(QIcon(str(ICONS_DIR / "zoom-in.svg")), "Zoom In", self)
        zoom_in_act.setToolTip("Zoom in (Ctrl+=, or Ctrl+scroll)")
        zoom_in_act.triggered.connect(lambda: self.view.set_zoom(self.view.zoom() * 1.2))
        tb.addAction(zoom_in_act)
        tb.widgetForAction(zoom_in_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

        self.zoom_tool_act = QAction(QIcon(str(ICONS_DIR / "zoom-in.svg")), "Zoom Tool", self)
        self.zoom_tool_act.setCheckable(True)
        self.zoom_tool_act.setToolTip(
            "Zoom tool — while on, click the page to zoom in, right-click (or "
            "Alt+click) to zoom out, anchored where you click. Only changes the "
            "view, never the page's actual size. Click this button again, or press "
            "Esc, to go back to normal typing."
        )
        self.zoom_tool_act.toggled.connect(self._toggle_zoom_tool)
        tb.addAction(self.zoom_tool_act)
        tb.widgetForAction(self.zoom_tool_act).setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        tb.addSeparator()

        # Not on any menu -- just a quick, discoverable-by-convention way
        # out of the zoom tool back to normal typing, same as Escape
        # backs out of a lot of other "modal" tools in most editors.
        exit_zoom_tool_act = QAction(self)
        exit_zoom_tool_act.setShortcut(QKeySequence("Esc"))
        exit_zoom_tool_act.triggered.connect(lambda: self.zoom_tool_act.setChecked(False))
        self.addAction(exit_zoom_tool_act)

        fonts_act = QAction("Fonts…", self)
        fonts_act.setToolTip("Choose which fonts appear in the picker")
        fonts_act.triggered.connect(self.open_font_manager)
        tb.addAction(fonts_act)

        f = self.underline_action.font()
        f.setUnderline(True)
        f.setBold(True)
        self.underline_action.setFont(f)
        tb.addAction(self.underline_action)

        f2 = self.strikeout_action.font()
        f2.setStrikeOut(True)
        f2.setBold(True)
        self.strikeout_action.setFont(f2)
        tb.addAction(self.strikeout_action)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)

        self.export_toolbar_action = QAction("Export", self)
        self.export_toolbar_action.triggered.connect(self.open_export_dialog)
        tb.addAction(self.export_toolbar_action)

    # --------------------------------------------------- spacing controls --
    def _add_spacing_control(self, toolbar, icon_name, minimum, maximum, step, suffix,
                             on_change, tooltip):
        """An icon + number box pair on `toolbar`. `on_change(int)` is called
        on every change of the number, so the effect is live. Pressing Enter
        in the box hands focus back to the page, ready to keep typing."""
        icon = QLabel()
        icon_source = QIcon(str(ICONS_DIR / f"{icon_name}.svg"))
        try:
            pixmap = icon_source.pixmap(QSize(16, 16), self.devicePixelRatioF())
        except TypeError:  # older PyQt6 without the device-pixel-ratio overload
            pixmap = icon_source.pixmap(QSize(16, 16))
        icon.setPixmap(pixmap)
        icon.setToolTip(tooltip)
        icon.setContentsMargins(4, 0, 3, 0)
        toolbar.addWidget(icon)

        spin = QSpinBox()
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setSuffix(suffix)
        spin.setMinimumWidth(72)
        spin.setToolTip(tooltip)
        spin.valueChanged.connect(on_change)
        spin.lineEdit().returnPressed.connect(self.canvas.setFocus)
        toolbar.addWidget(spin)
        return spin

    def _set_spin_quietly(self, spin, value):
        """Show `value` in `spin` without firing valueChanged (which would
        re-apply it to the page) -- and never overwrite a number the person
        is partway through typing."""
        if spin.value() == value:
            return
        # If the box's text isn't what its current value would display,
        # the person is partway through typing a number (e.g. the "1" of
        # "150"): leave it alone rather than yank it out from under them.
        # Once they finish or click away, the box snaps back to a real
        # value and the next sync brings it in line with the page.
        shown = spin.prefix() + spin.textFromValue(spin.value()) + spin.suffix()
        if spin.lineEdit().text() != shown:
            return
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    def _sync_spacing_controls(self, *_ignored):
        if not hasattr(self, "word_spacing_spin"):
            return  # signal fired while the toolbar is still being built
        self._set_spin_quietly(self.line_height_spin, self.canvas.current_line_spacing())
        self._set_spin_quietly(self.word_spacing_spin, int(round(self.canvas.current_word_spacing())))

    # -------------------------------------------------------- font picker --
    def _refresh_font_combo(self, select=None):
        select = select or self.canvas.pending_family()
        enabled = sorted(font_settings.get_enabled_families(select))
        self.font_combo.blockSignals(True)
        self.font_combo.clear()
        for fam in enabled:
            self.font_combo.addItem(fam)
            self.font_combo.setItemData(self.font_combo.count() - 1, QFont(fam, 11), Qt.ItemDataRole.FontRole)
        idx = self.font_combo.findText(select)
        self.font_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.font_combo.blockSignals(False)
        self._refresh_style_combo(self.font_combo.currentText())

    def _refresh_style_combo(self, family):
        styles = list(QFontDatabase.styles(family)) if family else []
        if not styles:
            styles = ["Regular"]
        self.style_combo.blockSignals(True)
        self.style_combo.clear()
        self.style_combo.addItems(styles)
        current = self.canvas.pending_style()
        idx = self.style_combo.findText(current)
        self.style_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.style_combo.blockSignals(False)

    def _on_font_combo_changed(self, family):
        if not family:
            return
        self.canvas.set_pending_family(family)
        self._refresh_style_combo(family)
        self.canvas.set_pending_style(self.style_combo.currentText())
        self._update_font_warning(family)

    def _on_style_combo_changed(self, style):
        if style:
            self.canvas.set_pending_style(style)

    def open_font_manager(self):
        dlg = FontManagerDialog(self.canvas.pending_family(), self)
        if dlg.exec():
            font_settings.set_enabled_families(dlg.enabled_families())
            self._refresh_font_combo(select=self.canvas.pending_family())

    def _update_font_warning(self, family):
        if supports_arabic_joining(family):
            self.font_warning.setVisible(False)
        else:
            self.font_warning.setText(
                f"⚠ '{family}' doesn't appear to join Urdu letters correctly — text may render "
                f"as separated letters instead of proper Nastaliq/Naskh script. Try a different font."
            )
            self.font_warning.setVisible(True)

    # ------------------------------------------------------------- zoom --
    def eventFilter(self, obj, event):
        # The embedded QTextEdit consumes wheel events by default (its
        # own QAbstractScrollArea machinery accepts them unconditionally,
        # even with its scrollbars off, since it never has anything to
        # scroll internally). Without this, the surrounding view's own
        # scroll and Ctrl+zoom would never receive a wheel event fired
        # while the cursor is over the page — which is most of the time.
        #
        # This replicates the view's scroll/zoom directly rather than
        # re-dispatching a translated event to the view's viewport:
        # re-dispatching sends it right back into the same item-under-
        # cursor delivery path QGraphicsView uses for its own wheel
        # handling, which routes straight back to this same canvas —
        # infinite recursion.
        if obj is self.canvas and event.type() == QEvent.Type.Wheel:
            # Skip while the standard right-click menu is open (see
            # PageCanvas.contextMenuEvent) — that menu is a separate
            # top-level popup, so zooming or scrolling the page under it
            # would just visibly drift out of sync with it rather than
            # actually change anything useful about the menu itself.
            if not self.canvas.context_menu_open:
                if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                    self.view.zoom_from_canvas_wheel(
                        event.position(), event.angleDelta().y(), self.canvas_proxy
                    )
                else:
                    vsb = self.view.verticalScrollBar()
                    steps = event.angleDelta().y() / 120.0  # fractional for high-res trackpads
                    vsb.setValue(round(vsb.value() - steps * vsb.singleStep() * 3))
            return True
        # Zoom tool: while self.zoom_tool_act is checked, a click on the
        # page zooms instead of placing the text cursor — same reasoning
        # and the same relayed-through-zoom_from_canvas_wheel mechanism as
        # the Ctrl+scroll case just above (the embedded QTextEdit would
        # otherwise just place the cursor and never let a click-to-zoom
        # tool exist at all). Left click zooms in; right click, or
        # Alt+left click, zooms out — the standard Adobe/Affinity
        # convention. Consuming the press here also stops QTextEdit's own
        # right-click context menu from appearing while the tool is on.
        if (obj is self.canvas and event.type() == QEvent.Type.MouseButtonPress
                and self.zoom_tool_act.isChecked()):
            if not self.canvas.context_menu_open:
                button = event.button()
                zoom_out = (button == Qt.MouseButton.RightButton or
                            (button == Qt.MouseButton.LeftButton and
                             event.modifiers() & Qt.KeyboardModifier.AltModifier))
                if zoom_out or button == Qt.MouseButton.LeftButton:
                    self.view.zoom_from_canvas_wheel(
                        event.position(), -1 if zoom_out else 1, self.canvas_proxy
                    )
            return True
        return super().eventFilter(obj, event)

    def _on_zoom_changed(self, zoom):
        self.zoom_label.setText(f"{round(zoom * 100)}%")
        self._update_status()

    def _toggle_zoom_tool(self, checked):
        if checked:
            self.canvas.viewport().setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.canvas.viewport().unsetCursor()

    # ------------------------------------------------------ roman urdu --
    def _toggle_roman_urdu(self, checked):
        self.canvas.roman_urdu_mode = checked

    def convert_selection_to_urdu(self):
        if not self.canvas.convert_selection_to_urdu():
            QMessageBox.information(
                self, "Nothing selected",
                "Select some Roman-Urdu text first, then use this to convert it."
            )

    # ------------------------------------------------------------ voice --
    def _init_voice_overlay(self):
        self.voice_overlay = VoiceOverlayButton(ICONS_DIR, self.view)
        self.voice_overlay.clicked.connect(self._on_voice_button_clicked)
        self.voice_overlay.show()
        self.voice_overlay.reposition_default()
        self._model_load_thread = None
        self._voice_load_error = None
        self._start_model_preload()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "voice_overlay"):
            self.voice_overlay.reposition_default()

    def _start_model_preload(self):
        """Loads the Whisper model in the background right away, so it's
        already warm by the time the user actually wants to record —
        rather than making the first real use wait for it."""
        try:
            import voice_to_urdu  # noqa: F401 — just checking availability
        except ImportError:
            self.voice_overlay.set_state("error")
            self.voice_overlay.setToolTip(
                "Voice input needs extra packages — see requirements-voice.txt. Click for details."
            )
            return
        self.voice_overlay.set_state("loading")
        self._model_load_thread = ModelLoadThread(self)
        self._model_load_thread.loaded.connect(lambda: self.voice_overlay.set_state("idle"))
        self._model_load_thread.failed.connect(self._on_model_load_failed)
        self._model_load_thread.start()

    def _on_model_load_failed(self, message):
        self.voice_overlay.set_state("error")
        self._voice_load_error = message
        # set_state() above already set the generic "click to retry" tooltip
        # (STATE_TOOLTIPS in voice_overlay.py) -- replace it with the actual
        # reason, or there's no way to tell *why* short of reading source.
        # This is exactly the detail that was missing from every previous
        # "couldn't load model" report.
        self.voice_overlay.setToolTip(
            f"Couldn't load the speech model — click to retry.\n\n{message}"
        )

    def _on_voice_button_clicked(self):
        state = self.voice_overlay.state()
        if state == "error":
            if self._voice_load_error:
                self.statusBar().showMessage(
                    f"Retrying — last attempt failed: {self._voice_load_error}", 8000)
            self._start_model_preload()  # retry
            return
        if state == "loading":
            self.statusBar().showMessage(
                "Still loading the speech model — one moment, then click again.", 4000)
            return
        if state == "transcribing":
            self.statusBar().showMessage(
                "Still finishing up the last recording — one moment, then click again.", 4000)
            return
        if state == "listening":
            self._stop_recording()
        else:
            self._start_recording()

    def _start_recording(self):
        try:
            import voice_to_urdu
        except ImportError:
            QMessageBox.warning(
                self, "Voice input not installed",
                "Voice-to-Urdu needs a couple of extra packages that aren't installed.\n\n"
                "Run:  pip install -r requirements-voice.txt\n\n"
                "See that file for platform-specific notes (microphone support)."
            )
            return
        try:
            self.voice_recorder = voice_to_urdu.Recorder()
            self.voice_recorder.start()
        except voice_to_urdu.MicrophoneUnavailable as e:
            QMessageBox.critical(self, "Microphone error", str(e))
            self.voice_recorder = None
            return
        self._segmenter = voice_to_urdu.UtteranceSegmenter()
        self._silence_warning_shown = False
        self._recording_poll_ticks = 0
        self.voice_overlay.set_state("listening")
        self.voice_overlay.set_audio_level(0.0)
        self.statusBar().showMessage("Recording — transcribing live as you speak. Click again to stop.")
        self._poll_timer.start()

    def _poll_recording(self):
        """Runs every POLL_INTERVAL_MS while recording: pulls whatever
        audio has come in since the last tick and feeds it to the
        segmenter, queuing any utterance it decides is now complete."""
        if self.voice_recorder is None:
            return
        new_audio = self.voice_recorder.pull_new_audio()
        for segment in self._segmenter.feed(new_audio):
            self._queue_segment(segment)
        self.voice_overlay.set_audio_level(self._segmenter.last_rms)

        # A live level reading the person can actually see is the fastest
        # way to tell "the mic isn't picking anything up" from "it's
        # picking me up but not transcribing yet" — but for someone not
        # watching closely, add an explicit nudge too: several seconds in
        # with not one window ever crossing the speech threshold is worth
        # a proactive word rather than staying silent indefinitely.
        self._recording_poll_ticks += 1
        elapsed_ms = self._recording_poll_ticks * self.POLL_INTERVAL_MS
        if (not self._silence_warning_shown and elapsed_ms >= 4000
                and not self._segmenter.ever_heard_speech):
            self._silence_warning_shown = True
            self.statusBar().showMessage(
                "Not picking up any sound yet — check the right microphone is "
                "selected and its input level in your OS sound settings.", 6000)

    def _stop_recording(self):
        self._poll_timer.stop()
        self.voice_overlay.set_audio_level(0.0)
        audio_tail = self.voice_recorder.stop() if self.voice_recorder else None
        self.voice_recorder = None

        if audio_tail is not None and audio_tail.size and self._segmenter is not None:
            for segment in self._segmenter.feed(audio_tail):
                self._queue_segment(segment)
        final_segment = self._segmenter.flush() if self._segmenter is not None else None
        self._segmenter = None
        if final_segment is not None:
            self._queue_segment(final_segment)

        if self._pending_chunks or self._transcribe_thread is not None:
            self.voice_overlay.set_state("transcribing")
            self.statusBar().showMessage("Transcribing the rest…")
        else:
            self.voice_overlay.set_state("idle")
            self.statusBar().showMessage("Ready.", 3000)

    def _queue_segment(self, audio):
        """Adds a finished utterance to the transcription queue and kicks
        off processing if nothing is running yet. Chunks are always
        transcribed one at a time, in the order they were spoken — running
        several Whisper calls at once on CPU would just make all of them
        slower rather than actually going faster, and a queue keeps text
        landing on the page in the right order regardless of how the
        model's speed compares to how fast the person is talking."""
        self._pending_chunks.append(audio)
        self._drain_queue()

    def _drain_queue(self):
        if self._transcribe_thread is not None:
            return  # already transcribing one; it calls this again when done
        if not self._pending_chunks:
            if self.voice_recorder is None:
                # Recording has stopped and the queue is empty — the tail
                # end of _stop_recording's work is done.
                self.voice_overlay.set_state("idle")
                self.statusBar().clearMessage()
            return
        audio = self._pending_chunks.popleft()
        self._transcribe_thread = TranscribeThread(audio, self)
        self._transcribe_thread.finished_text.connect(self._on_chunk_transcribed)
        self._transcribe_thread.error.connect(self._on_transcribe_error)
        self._transcribe_thread.start()

    def _on_chunk_transcribed(self, text):
        self._transcribe_thread = None
        if text:
            self.canvas.insert_dictated_text(text)
        self._drain_queue()

    def _on_transcribe_error(self, message):
        self._transcribe_thread = None
        self._pending_chunks.clear()  # drop anything queued behind the failure
        still_recording = self.voice_recorder is not None
        self.voice_overlay.set_state("listening" if still_recording else "idle")
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "Transcription failed", message)

    # ------------------------------------------------------------- page --
    def _sync_scene_rect(self):
        m = self.PASTEBOARD_MARGIN
        self.scene.setSceneRect(
            -m, -m, self.canvas.page_width + 2 * m, self.canvas.page_height + 2 * m
        )
        # Default to showing the top of the page, horizontally centered —
        # without this the view can default to the (now much larger,
        # padded) scene's own center, which would show a stretch of empty
        # pasteboard instead of actual page content. centerOn takes scene
        # coordinates directly, so it does the right thing regardless of
        # how much padding surrounds the page.
        if hasattr(self, "view") and self.view.viewport().height() > 0:
            half_viewport_h = self.view.viewport().height() / (2 * self.view.zoom())
            self.view.centerOn(self.canvas.page_width / 2, half_viewport_h)

    def _update_status(self):
        zoom_pct = round(self.view.zoom() * 100) if hasattr(self, "view") else 100
        self.statusBar().showMessage(
            f"Page: {self.canvas.page_width} x {self.canvas.page_height} px   •   Zoom: {zoom_pct}%"
        )

    def _update_title(self):
        name = Path(self.current_project_path).name if self.current_project_path else "Untitled"
        star = "*" if self.canvas.document().isModified() else ""
        self.setWindowTitle(f"{star}{name} — {APP_TITLE}")

    # -------------------------------------------------------- window geometry --
    def _restore_window_geometry(self):
        settings = QSettings(font_settings.ORG, font_settings.APP)
        geometry = settings.value("window/geometry", None)
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1360, 850)

    def _save_window_geometry(self):
        settings = QSettings(font_settings.ORG, font_settings.APP)
        settings.setValue("window/geometry", self.saveGeometry())

    # ---------------------------------------------------------- word count --
    def _refresh_word_count(self):
        words = self.canvas.word_count()
        chars = self.canvas.character_count()
        self.word_count_label.setText(
            f"{words} word{'s' if words != 1 else ''}, {chars} character{'s' if chars != 1 else ''}"
        )

    # ------------------------------------------------------------ autosave --
    def _recovery_file_path(self) -> Path:
        data_dir = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir / "autosave_recovery.utw"

    def _check_for_recovery_file(self):
        path = self._recovery_file_path()
        if not path.exists():
            return
        resp = QMessageBox.question(
            self, "Recover unsaved work?",
            "It looks like the app didn't close normally last time, and there's an "
            "autosaved page from that session.\n\nOpen it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if resp == QMessageBox.StandardButton.Yes:
            try:
                loaded = project_io.load_project(str(path))
                project_io.apply_loaded_project(loaded, self.canvas)
                self._sync_scene_rect()
                self._sync_toolbar_to_canvas()
                # Recovered content is, by definition, never been saved as
                # its own file — keep the "unsaved changes" indicator (title
                # bar asterisk, close-time save prompt) honest about that
                # rather than marking it clean just because it was just loaded.
                self.canvas.document().setModified(True)
                self._update_title()
                self._update_status()
                self.statusBar().showMessage("Recovered your last unsaved page.", 5000)
            except Exception as e:
                QMessageBox.warning(self, "Recovery failed", f"Couldn't open the autosaved page.\n\n{e}")
        # Either way, this specific snapshot has now been offered and
        # decided on — delete it so it isn't offered again next launch.
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass

    def _autosave_tick(self):
        if not self.canvas.document().isModified():
            return  # nothing new to protect since the last autosave (or the last real Save)
        try:
            project_io.save_project(str(self._recovery_file_path()), self.canvas)
        except Exception:
            # A failed autosave should never interrupt typing or pop up a
            # dialog every minute -- but it's worth one unobtrusive,
            # non-blocking mention, since the person's own Ctrl+S still
            # works fine regardless and they may want to use it more often.
            if not self._autosave_warned:
                self._autosave_warned = True
                self.statusBar().showMessage(
                    "Autosave couldn't write a recovery file — your own Save (Ctrl+S) still works normally.",
                    6000,
                )

    def open_new_page_dialog(self):
        dlg = NewPageDialog(
            self.canvas.page_width, self.canvas.page_height,
            self.canvas.background_color(), self.canvas.document().documentMargin(),
            background_image=self.canvas.background_image(),
            background_image_mode=self.canvas.background_image_mode(),
            border_width=self.canvas.border_width(), border_color=self.canvas.border_color(),
            border_style=self.canvas.border_style(), vertical_align=self.canvas.vertical_align(),
            parent=self,
        )
        if dlg.exec():
            v = dlg.values()
            self.canvas.resize_page(v["width"], v["height"])
            self.canvas.set_margin(v["margin"])
            self.canvas.set_background(v["background"])
            self.canvas.set_background_image(v["background_image"], v["background_image_mode"])
            self.canvas.set_border(v["border_width"], v["border_color"], v["border_style"])
            self.canvas.set_vertical_align(v["vertical_align"])
            self._sync_scene_rect()
            self._update_status()

    # ---------------------------------------------------------- project --
    def save_project(self):
        if self.current_project_path is None:
            self.save_project_as()
            return
        self._do_save(self.current_project_path)

    def save_project_as(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Save Project", "untitled.utw", project_io.FILE_FILTER
        )
        if not path:
            return
        if not path.endswith(f".{project_io.EXTENSION}"):
            path += f".{project_io.EXTENSION}"
        self._do_save(path)

    def _do_save(self, path):
        try:
            project_io.save_project(path, self.canvas)
            self.current_project_path = path
            self.canvas.document().setModified(False)
            self._update_title()
            self.statusBar().showMessage(f"Saved to {path}", 5000)
            self._add_recent_file(path)
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def open_project(self):
        if not self._confirm_discard_unsaved():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", project_io.FILE_FILTER)
        if not path:
            return
        self._load_project_from_path(path)

    def _open_recent_file(self, path):
        if not self._confirm_discard_unsaved():
            return
        if not Path(path).exists():
            QMessageBox.warning(self, "File not found", f"This file no longer exists:\n\n{path}")
            self._recent_files = [p for p in self._recent_files if p != path]
            self._save_recent_files()
            self._refresh_recent_files_menu()
            return
        self._load_project_from_path(path)

    def _load_project_from_path(self, path):
        try:
            loaded = project_io.load_project(path)
        except project_io.ProjectLoadError as e:
            # Nothing has touched the canvas yet at this point — load_project
            # only reads and validates the file — so the document the user
            # already had open (if any) is untouched.
            QMessageBox.critical(self, "Couldn't open project", str(e))
            return
        try:
            project_io.apply_loaded_project(loaded, self.canvas)
            self._sync_scene_rect()
            self._sync_toolbar_to_canvas()
            self.current_project_path = path
            self.canvas.document().setModified(False)
            self._update_title()
            self._update_status()
            self._add_recent_file(path)
        except Exception as e:
            QMessageBox.critical(
                self, "Couldn't open project",
                f"The file was read, but something went wrong applying it to the "
                f"page, and the document may now be in a mixed state.\n\n"
                f"Worth undoing (Ctrl+Z) or starting a fresh page before continuing.\n\n"
                f"Details: {e}"
            )

    # ------------------------------------------------------------ recent files --
    MAX_RECENT_FILES = 8

    def _load_recent_files(self):
        settings = QSettings(font_settings.ORG, font_settings.APP)
        raw = settings.value("recentFiles", "[]", type=str)
        try:
            paths = json.loads(raw)
        except Exception:
            return []
        return [p for p in paths if isinstance(p, str)]

    def _save_recent_files(self):
        settings = QSettings(font_settings.ORG, font_settings.APP)
        settings.setValue("recentFiles", json.dumps(self._recent_files))

    def _add_recent_file(self, path):
        path = str(path)
        self._recent_files = [p for p in self._recent_files if p != path]
        self._recent_files.insert(0, path)
        self._recent_files = self._recent_files[:self.MAX_RECENT_FILES]
        self._save_recent_files()
        self._refresh_recent_files_menu()

    def _refresh_recent_files_menu(self):
        self.recent_files_menu.clear()
        # Drop entries for files that no longer exist rather than showing a
        # dead link every time — this menu is meant to save a trip through
        # a file browser, not to also double as a "files I used to have" log.
        existing = [p for p in self._recent_files if Path(p).exists()]
        if existing != self._recent_files:
            self._recent_files = existing
            self._save_recent_files()
        if not existing:
            empty_act = QAction("(No recent files)", self)
            empty_act.setEnabled(False)
            self.recent_files_menu.addAction(empty_act)
            return
        for path in existing:
            act = QAction(Path(path).name, self)
            try:
                mtime = datetime.fromtimestamp(Path(path).stat().st_mtime).strftime("%b %d, %Y %I:%M %p")
                act.setToolTip(f"{path}\nLast modified {mtime}")
            except OSError:
                act.setToolTip(path)
            act.triggered.connect(lambda checked=False, p=path: self._open_recent_file(p))
            self.recent_files_menu.addAction(act)
        self.recent_files_menu.addSeparator()
        clear_act = QAction("Clear Recent Files", self)
        clear_act.triggered.connect(self._clear_recent_files)
        self.recent_files_menu.addAction(clear_act)

    def _clear_recent_files(self):
        self._recent_files = []
        self._save_recent_files()
        self._refresh_recent_files_menu()

    def _sync_toolbar_to_canvas(self):
        self._refresh_font_combo(select=self.canvas.pending_family())
        idx = self.style_combo.findText(self.canvas.pending_style())
        if idx >= 0:
            self.style_combo.setCurrentIndex(idx)
        self.size_spin.blockSignals(True)
        self.size_spin.setValue(self.canvas.pending_size())
        self.size_spin.blockSignals(False)
        self.color_button.set_color(self.canvas.pending_color())
        self.underline_action.blockSignals(True)
        self.underline_action.setChecked(self.canvas.pending_underline())
        self.underline_action.blockSignals(False)
        self.strikeout_action.blockSignals(True)
        self.strikeout_action.setChecked(self.canvas.pending_strikeout())
        self.strikeout_action.blockSignals(False)
        self.superscript_action.blockSignals(True)
        self.superscript_action.setChecked(self.canvas.pending_superscript())
        self.superscript_action.blockSignals(False)
        self.subscript_action.blockSignals(True)
        self.subscript_action.setChecked(self.canvas.pending_subscript())
        self.subscript_action.blockSignals(False)
        self._sync_spacing_controls()

    def fit_zoom_to_window(self):
        viewport_size = self.view.viewport().size()
        if self.canvas.page_width <= 0 or self.canvas.page_height <= 0:
            return
        margin = 40  # a little breathing room around the page, not edge-to-edge
        available_w = max(1, viewport_size.width() - margin)
        available_h = max(1, viewport_size.height() - margin)
        scale = min(available_w / self.canvas.page_width, available_h / self.canvas.page_height)
        self.view.set_zoom(scale)

    def _enter_exact_zoom(self):
        current_pct = round(self.view.zoom() * 100)
        pct, ok = QInputDialog.getInt(
            self, "Zoom", "Zoom percentage:", current_pct,
            round(self.view.MIN_ZOOM * 100), round(self.view.MAX_ZOOM * 100), 5,
        )
        if ok:
            self.view.set_zoom(pct / 100.0)

    def choose_highlight_color(self):
        current = self.canvas.pending_highlight() or QColor("#FFFF00")
        c = ColorPickerDialog.getColor(current, self)
        if c.isValid():
            self.canvas.set_pending_highlight(c)

    def choose_letter_spacing(self):
        value, ok = QInputDialog.getInt(
            self, "Letter Spacing", "Spacing (% of normal — 100 = normal):",
            self.canvas.pending_letter_spacing(), 10, 500, 5,
        )
        if ok:
            self.canvas.set_pending_letter_spacing(value)

    def choose_line_spacing(self):
        lo, hi = PageCanvas.LINE_HEIGHT_RANGE
        value, ok = QInputDialog.getInt(
            self, "Line Height", "Line height (% of normal — 100 = single, 150 = 1.5x, ...):",
            self.canvas.current_line_spacing(), lo, hi, 5,
        )
        if ok:
            self.canvas.apply_line_spacing(value)

    def choose_word_spacing(self):
        lo, hi = PageCanvas.WORD_SPACING_RANGE
        value, ok = QInputDialog.getInt(
            self, "Word Spacing",
            "Extra space between words, in pixels (0 = normal, negative = tighter).\n"
            "Letters inside each word aren't affected:",
            int(round(self.canvas.current_word_spacing())), lo, hi, 1,
        )
        if ok:
            self.canvas.set_word_spacing(value)

    def _on_superscript_toggled(self, checked):
        self.canvas.set_pending_superscript(checked)
        if checked:
            # Mutually exclusive with subscript -- block its own toggled
            # signal while flipping it off so this doesn't recurse back
            # into _on_subscript_toggled and fight over canvas state.
            self.subscript_action.blockSignals(True)
            self.subscript_action.setChecked(False)
            self.subscript_action.blockSignals(False)

    def _on_subscript_toggled(self, checked):
        self.canvas.set_pending_subscript(checked)
        if checked:
            self.superscript_action.blockSignals(True)
            self.superscript_action.setChecked(False)
            self.superscript_action.blockSignals(False)

    def clear_formatting(self):
        self.canvas.clear_formatting()
        self._sync_toolbar_to_canvas()

    def activate_format_painter(self):
        cursor = self.canvas.textCursor()
        self._format_painter_source = cursor.charFormat()
        self._format_painter_active = True
        self.canvas.viewport().setCursor(Qt.CursorShape.CrossCursor)
        self.statusBar().showMessage(
            "Format Painter: select text to apply the copied formatting to it.")

    def _on_selection_changed_for_format_painter(self):
        if not self._format_painter_active:
            return
        cursor = self.canvas.textCursor()
        if not cursor.hasSelection():
            return  # still waiting for an actual selection, not just a cursor move
        fmt = self._format_painter_source
        # Deactivate *before* applying the format -- mergeCharFormat() below
        # can itself trigger a nested selectionChanged emission (the same
        # class of Qt re-entrancy that bit _apply_vertical_align in
        # canvas.py), and a still-"active" handler would try to merge the
        # format again inside that nested call, and again, recursing until
        # the stack overflows.
        self._format_painter_active = False
        self._format_painter_source = None
        cursor.mergeCharFormat(fmt)
        self.canvas.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.statusBar().showMessage("Formatting applied.", 3000)

    def insert_date(self):
        from datetime import date
        self.canvas.textCursor().insertText(date.today().strftime("%B %d, %Y"))

    def show_about(self):
        from dialogs import AboutDialog
        AboutDialog(APP_VERSION, self).exec()

    def open_roman_urdu_guide(self):
        from dialogs import RomanUrduGuideDialog
        RomanUrduGuideDialog(self).exec()

    def open_preferences(self):
        from dialogs import PreferencesDialog
        settings = QSettings(font_settings.ORG, font_settings.APP)
        # Fallbacks match this app's real built-in start (see
        # MainWindow.__init__), not arbitrary numbers: now that saved
        # defaults are applied on launch, opening Preferences on a fresh
        # install and just pressing OK must not quietly change the page
        # size to something the person never picked.
        old = {
            "default_family": settings.value("prefs/defaultFamily", self.canvas.pending_family(), type=str),
            "default_size": settings.value("prefs/defaultSize", 36, type=int),
            "default_width": settings.value("prefs/defaultWidth", 794, type=int),
            "default_height": settings.value("prefs/defaultHeight", 1123, type=int),
            "autosave_interval_sec": settings.value("prefs/autosaveIntervalSec", 60, type=int),
        }
        dlg = PreferencesDialog(parent=self, **old)
        if dlg.exec():
            v = dlg.values()
            settings.setValue("prefs/defaultFamily", v["default_family"])
            settings.setValue("prefs/defaultSize", v["default_size"])
            settings.setValue("prefs/defaultWidth", v["default_width"])
            settings.setValue("prefs/defaultHeight", v["default_height"])
            settings.setValue("prefs/autosaveIntervalSec", v["autosave_interval_sec"])
            settings.sync()  # write to disk now, not whenever Qt gets around to it
            self._autosave_timer.setInterval(max(5, v["autosave_interval_sec"]) * 1000)

            # A blank page is effectively a new page already, so apply what
            # was just changed to it immediately instead of making the
            # person restart to see anything happen. Only what actually
            # changed, so re-saving an unrelated setting never overrides a
            # page size or font someone set up on purpose.
            applied = []
            if self.canvas.document().isEmpty():
                idx = self.font_combo.findText(v["default_family"])
                if v["default_family"] != old["default_family"] and idx >= 0:
                    self.font_combo.setCurrentIndex(idx)
                    applied.append("font")
                if v["default_size"] != old["default_size"]:
                    self.size_spin.setValue(v["default_size"])
                    applied.append("size")
                if (v["default_width"], v["default_height"]) != (old["default_width"], old["default_height"]):
                    self.canvas.resize_page(v["default_width"], v["default_height"])
                    self._sync_scene_rect()
                    self._update_status()
                    applied.append("page size")
            if applied:
                self.statusBar().showMessage(
                    f"Preferences saved — default {', '.join(applied)} applied to this blank "
                    "page, and to every new session.", 5000)
            else:
                self.statusBar().showMessage(
                    "Preferences saved — defaults apply from the next launch (or a blank page); "
                    "the autosave interval applies now.", 5000)

    def show_document_statistics(self):
        from dialogs import DocumentStatisticsDialog
        text = self.canvas.toPlainText()
        words = self.canvas.word_count()
        characters = self.canvas.character_count()
        characters_no_spaces = len(text.replace(" ", "").replace("\n", ""))
        lines = text.count("\n") + 1 if text else 0
        paragraphs = self.canvas.document().blockCount()
        # Reading time has no agreed-on words-per-minute figure for Urdu
        # specifically, so this uses the commonly-cited ~200 wpm English
        # silent-reading average as a rough, clearly-labeled estimate
        # rather than presenting a precise-looking number.
        minutes = max(1, round(words / 200)) if words else 0
        reading_time = f"~{minutes} min" if minutes else "< 1 min"
        stats = {
            "words": words, "characters": characters,
            "characters_no_spaces": characters_no_spaces,
            "lines": lines, "paragraphs": paragraphs, "reading_time": reading_time,
        }
        DocumentStatisticsDialog(stats, self).exec()

    def open_find_replace(self):
        if self._find_dialog is None:
            self._find_dialog = FindReplaceDialog(self)
            self._find_dialog.find_next.connect(self._on_find_next)
            self._find_dialog.find_previous.connect(self._on_find_previous)
            self._find_dialog.replace_current.connect(self._on_replace_current)
            self._find_dialog.replace_all.connect(self._on_replace_all)
        self._find_dialog.show()
        self._find_dialog.raise_()
        self._find_dialog.activateWindow()
        self._find_dialog.find_edit.setFocus()
        self._find_dialog.find_edit.selectAll()

    def _find_flags(self, match_case, whole_word):
        flags = QTextDocument.FindFlag(0)
        if match_case:
            flags |= QTextDocument.FindFlag.FindCaseSensitively
        if whole_word:
            flags |= QTextDocument.FindFlag.FindWholeWords
        return flags

    def _on_find_next(self, text, match_case, whole_word):
        if not text:
            return
        found = self.canvas.find(text, self._find_flags(match_case, whole_word))
        if not found:
            # Wrap around: try again from the top of the document instead
            # of just reporting failure the moment the cursor passes the
            # last match — this is what "Find Next" means in most editors.
            cursor = self.canvas.textCursor()
            cursor.movePosition(cursor.MoveOperation.Start)
            self.canvas.setTextCursor(cursor)
            found = self.canvas.find(text, self._find_flags(match_case, whole_word))
        self._find_dialog.set_status("Found." if found else "Not found.")

    def _on_find_previous(self, text, match_case, whole_word):
        if not text:
            return
        flags = self._find_flags(match_case, whole_word) | QTextDocument.FindFlag.FindBackward
        found = self.canvas.find(text, flags)
        if not found:
            cursor = self.canvas.textCursor()
            cursor.movePosition(cursor.MoveOperation.End)
            self.canvas.setTextCursor(cursor)
            found = self.canvas.find(text, flags)
        self._find_dialog.set_status("Found." if found else "Not found.")

    def _on_replace_current(self, find_text, replace_text, match_case, whole_word):
        cursor = self.canvas.textCursor()
        if cursor.hasSelection() and cursor.selectedText() == find_text:
            cursor.insertText(replace_text)
            self._find_dialog.set_status("Replaced.")
        self._on_find_next(find_text, match_case, whole_word)

    def _on_replace_all(self, find_text, replace_text, match_case, whole_word):
        if not find_text:
            return
        cursor = self.canvas.textCursor()
        cursor.movePosition(cursor.MoveOperation.Start)
        self.canvas.setTextCursor(cursor)
        count = 0
        flags = self._find_flags(match_case, whole_word)
        outer_cursor = self.canvas.textCursor()
        outer_cursor.beginEditBlock()  # one undo step for the whole replace-all, not one per match
        try:
            while self.canvas.find(find_text, flags):
                self.canvas.textCursor().insertText(replace_text)
                count += 1
        finally:
            outer_cursor.endEditBlock()
        self._find_dialog.set_status(f"Replaced {count} occurrence{'s' if count != 1 else ''}.")

    def print_page(self):
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QPrintDialog.DialogCode.Accepted:
            return
        painter = QPainter()
        if not painter.begin(printer):
            QMessageBox.critical(self, "Print failed", "Couldn't start the printer.")
            return
        self.setCursor(Qt.CursorShape.WaitCursor)
        try:
            # Scale so the page fills the printable area at its own aspect
            # ratio (letterboxed if the paper's aspect doesn't match)
            # rather than printing at a fixed DPI that might be tiny or
            # run off the edge of the paper.
            target = painter.viewport()
            scale = min(target.width() / self.canvas.page_width,
                        target.height() / self.canvas.page_height)
            x_offset = (target.width() - self.canvas.page_width * scale) / 2
            y_offset = (target.height() - self.canvas.page_height * scale) / 2
            painter.translate(x_offset, y_offset)
            exporter.paint_page_for_printer(
                painter, self.canvas.document(), self.canvas.page_width, self.canvas.page_height,
                self.canvas.background_color(), background_image=self.canvas.background_image(),
                background_image_mode=self.canvas.background_image_mode(),
                border_width=self.canvas.border_width(), border_color=self.canvas.border_color(),
                border_style=self.canvas.border_style(), scale=scale,
            )
        finally:
            self.unsetCursor()
            painter.end()
        self.statusBar().showMessage("Sent to printer.", 4000)

    def _confirm_discard_unsaved(self):
        if not self.canvas.document().isModified():
            return True
        resp = QMessageBox.question(
            self, "Unsaved changes",
            "This page has unsaved changes. Save before continuing?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if resp == QMessageBox.StandardButton.Save:
            self.save_project()
            return not self.canvas.document().isModified()
        return resp == QMessageBox.StandardButton.Discard

    _DROPPABLE_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                path = url.toLocalFile()
                if path and (path.lower().endswith(".utw") or
                             path.lower().endswith(self._DROPPABLE_IMAGE_SUFFIXES)):
                    event.acceptProposedAction()
                    return
        event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return
        path = urls[0].toLocalFile()
        if not path:
            event.ignore()
            return
        if path.lower().endswith(".utw"):
            if self._confirm_discard_unsaved():
                self._load_project_from_path(path)
            event.acceptProposedAction()
            return
        if path.lower().endswith(self._DROPPABLE_IMAGE_SUFFIXES):
            img = QImage(path)
            if img.isNull():
                QMessageBox.warning(self, "Background Image", f"Couldn't read this image file:\n\n{path}")
                event.ignore()
                return
            self.canvas.set_background_image(_downscale_if_needed(img), self.canvas.background_image_mode())
            self.statusBar().showMessage(f"Set {Path(path).name} as the page background.", 4000)
            event.acceptProposedAction()
            return
        event.ignore()

    def closeEvent(self, event):
        if not self._confirm_discard_unsaved():
            event.ignore()
            return
        self._save_window_geometry()
        self._poll_timer.stop()
        self._autosave_timer.stop()
        if self.voice_recorder is not None:
            self.voice_recorder.stop()
            self.voice_recorder = None
        # Background model-load/transcription/export threads don't have a
        # natural cancellation point (they're inside a blocking library
        # call), so rather than risk Qt's fatal "thread destroyed while
        # still running" crash on exit, give them a moment to notice we're
        # closing and terminate them if they don't.
        for thread in (self._model_load_thread, self._transcribe_thread, self._export_thread):
            if thread is not None and thread.isRunning():
                thread.terminate()
                thread.wait(2000)
        # A clean exit (we got this far, meaning any unsaved changes were
        # already resolved above) doesn't need the crash-recovery copy —
        # leaving it behind would just make the next launch offer to
        # "recover" a page that was already saved or intentionally discarded.
        try:
            self._recovery_file_path().unlink(missing_ok=True)
        except OSError:
            pass
        event.accept()

    # ----------------------------------------------------------- export --
    def open_export_dialog(self):
        if self._export_thread is not None and self._export_thread.isRunning():
            QMessageBox.information(self, "Export", "An export is already in progress.")
            return

        dlg = ExportDialog(self.canvas.page_width, self.canvas.page_height, self)
        if not dlg.exec():
            return
        v = dlg.values()
        export_all = dlg.export_all_requested()
        fmt = v["format"]

        settings = QSettings(font_settings.ORG, font_settings.APP)
        last_dir = settings.value("export/lastDirectory", "", type=str)
        if export_all:
            path, _ = QFileDialog.getSaveFileName(
                self, "Export All Formats — choose a base name", f"{last_dir}/untitled.png" if last_dir else "untitled.png",
                "Any (*)"
            )
        else:
            default_name = f"{last_dir}/untitled.{fmt.lower()}" if last_dir else f"untitled.{fmt.lower()}"
            path, _ = QFileDialog.getSaveFileName(self, "Export", default_name, f"*.{fmt.lower()}")
        if not path:
            return
        settings.setValue("export/lastDirectory", str(Path(path).parent))
        settings.setValue("export/lastFormat", fmt)

        # Copy everything the export needs *here*, on the GUI thread, before
        # handing off to the background thread -- see ExportThread's
        # docstring. QImage.copy() and QTextDocument.clone() are both cheap
        # (implicit sharing / a single pass over the document respectively)
        # next to the actual work of rendering, so this adds negligible
        # time for a real guarantee against a data race with typing.
        doc_copy = self.canvas.document().clone()
        page_w, page_h = self.canvas.page_width, self.canvas.page_height
        bg = self.canvas.background_color() if v["include_background"] else None
        bg_image = self.canvas.background_image()
        bg_image_copy = bg_image.copy() if (v["include_background"] and bg_image is not None) else None
        bg_image_mode = self.canvas.background_image_mode()
        border_width = self.canvas.border_width() if v["include_background"] else 0
        border_color = self.canvas.border_color()
        border_style = self.canvas.border_style()
        self._last_export_paths = []

        def do_export():
            if export_all:
                self._last_export_paths = exporter.export_all_formats(
                    doc_copy, page_w, page_h, v["width"], v["height"], bg, path,
                    background_image=bg_image_copy, background_image_mode=bg_image_mode,
                    border_width=border_width, border_color=border_color, border_style=border_style,
                )
                return
            if fmt in ("PNG", "JPG"):
                exporter.export_raster(
                    doc_copy, page_w, page_h, v["width"], v["height"], bg, path, fmt,
                    background_image=bg_image_copy, background_image_mode=bg_image_mode,
                    border_width=border_width, border_color=border_color, border_style=border_style,
                )
            elif fmt == "SVG":
                exporter.export_svg(
                    doc_copy, page_w, page_h, bg, path,
                    background_image=bg_image_copy, background_image_mode=bg_image_mode,
                    border_width=border_width, border_color=border_color, border_style=border_style,
                )
            elif fmt == "PDF":
                exporter.export_pdf(
                    doc_copy, page_w, page_h, bg, path,
                    background_image=bg_image_copy, background_image_mode=bg_image_mode,
                    border_width=border_width, border_color=border_color, border_style=border_style,
                )
            elif fmt == "EPS":
                exporter.export_eps(
                    doc_copy, page_w, page_h, bg, path,
                    background_image=bg_image_copy, background_image_mode=bg_image_mode,
                    border_width=border_width, border_color=border_color, border_style=border_style,
                )
            self._last_export_paths = [path]

        self._export_thread = ExportThread(do_export, self)
        self._export_thread.succeeded.connect(lambda: self._on_export_succeeded(path))
        self._export_thread.failed.connect(self._on_export_failed)
        self.export_menu_action.setEnabled(False)
        self.export_toolbar_action.setEnabled(False)
        self.setCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage(f"Exporting to {path}…" if not export_all else "Exporting all formats…")
        self._export_thread.start()

    def _on_export_succeeded(self, path):
        self.export_menu_action.setEnabled(True)
        self.export_toolbar_action.setEnabled(True)
        self.unsetCursor()
        n = len(self._last_export_paths)
        if n > 1:
            self.statusBar().showMessage(f"Exported {n} files next to {path}", 6000)
        else:
            self.statusBar().showMessage(f"Exported to {path}", 5000)
        self._offer_reveal_in_folder(self._last_export_paths[0] if self._last_export_paths else path)

    def _offer_reveal_in_folder(self, path):
        from PyQt6.QtGui import QDesktopServices
        from PyQt6.QtCore import QUrl
        folder = str(Path(path).parent)
        box = QMessageBox(self)
        box.setWindowTitle("Export Complete")
        box.setText(f"Exported successfully.\n\nOpen the containing folder?")
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        if box.exec() == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _on_export_failed(self, message):
        self.export_menu_action.setEnabled(True)
        self.export_toolbar_action.setEnabled(True)
        self.unsetCursor()
        self.statusBar().clearMessage()
        QMessageBox.critical(self, "Export failed", message)

    def copy_as_image(self):
        # Not threaded (unlike open_export_dialog): a clipboard render is
        # always raster-only at a fixed, modest scale, with none of
        # export_svg()'s outline extraction, so it's not slow enough to
        # be worth the extra machinery.
        image = exporter.render_for_clipboard(
            self.canvas.document(), self.canvas.page_width, self.canvas.page_height,
            self.canvas.background_color(),
            background_image=self.canvas.background_image(),
            background_image_mode=self.canvas.background_image_mode(),
            border_width=self.canvas.border_width(), border_color=self.canvas.border_color(),
        )
        QApplication.clipboard().setImage(image)
        self.statusBar().showMessage("Copied page to clipboard as an image", 4000)

    def copy_as_svg(self):
        # Copies only the glyph outlines -- the current selection if there
        # is one, otherwise every letter on the page -- cropped to their
        # own bounding box, with no page background, border, or empty page
        # area around them (see exporter.render_svg_curves_bytes).
        #
        # The clipboard gets the same curves as SVG and as PDF, and nothing
        # raster (a design app offered a bitmap next to the vectors can
        # quietly take the bitmap -- that was the original pixelation bug).
        #
        # Why the PDF is also added under a second, Windows-specific name:
        # Windows matches clipboard formats by exact registered name, and
        # Affinity registers its own PDF clipboard data as "Portable Document
        # Format" -- not the MIME-style "application/pdf" that Qt uses by
        # default. With only the MIME names present, the likeliest reading of
        # "pastes nothing at all" is that Affinity saw nothing it recognised.
        # See exporter.make_curves_mime_data() for the full format list/order.
        # For a plain picture there's Copy as Image.
        cursor = self.canvas.textCursor()
        if cursor.hasSelection():
            char_range = (cursor.selectionStart(), cursor.selectionEnd())
            source_text = cursor.selection().toPlainText()
            what = "selection"
        else:
            char_range = None
            source_text = self.canvas.toPlainText()
            what = "page text"
        if not source_text.strip():
            self.statusBar().showMessage(
                "Nothing to copy — type or select some text first", 4000)
            return

        # Both renders walk every glyph, so — like file export — they run
        # off the GUI thread; clone the document (and read every other bit
        # of canvas state the render needs, as plain values) here on the
        # GUI thread first. See ExportThread's docstring for why the
        # do_export closure below must not reach back into self.canvas
        # once the thread has started.
        doc_copy = self.canvas.document().clone()
        page_w = self.canvas.page_width
        self._svg_clipboard_bytes = None
        self._pdf_clipboard_bytes = None

        def do_export():
            self._svg_clipboard_bytes = exporter.render_svg_curves_bytes(
                doc_copy, page_w, char_range=char_range,
            )
            self._pdf_clipboard_bytes = exporter.render_pdf_curves_bytes(
                doc_copy, page_w, char_range=char_range,
            )

        def on_succeeded():
            self.copy_svg_menu_act.setEnabled(True)
            self.unsetCursor()
            QApplication.clipboard().setMimeData(
                exporter.make_curves_mime_data(self._svg_clipboard_bytes, self._pdf_clipboard_bytes))
            self.statusBar().showMessage(
                f"Copied {what} as SVG curves (vector SVG + PDF on the clipboard)", 5000)

        def on_failed(message):
            self.copy_svg_menu_act.setEnabled(True)
            self.unsetCursor()
            self.statusBar().clearMessage()
            QMessageBox.critical(self, "Couldn't copy as curves", message)

        self._svg_copy_thread = ExportThread(do_export, self)
        self._svg_copy_thread.succeeded.connect(on_succeeded)
        self._svg_copy_thread.failed.connect(on_failed)
        self.copy_svg_menu_act.setEnabled(False)
        self.setCursor(Qt.CursorShape.WaitCursor)
        self.statusBar().showMessage("Preparing curves for the clipboard…")
        self._svg_copy_thread.start()


def install_exception_hook():
    """Catches any exception that would otherwise crash the app silently
    (or to a terminal window most users will never see) and shows a
    dialog instead. Logs the full traceback to stderr too, for anyone
    running from a terminal who wants the detail."""
    def handle_exception(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        import traceback
        traceback.print_exception(exc_type, exc_value, exc_traceback)
        try:
            QMessageBox.critical(
                None, "Unexpected error",
                f"Something went wrong:\n\n{exc_value}\n\n"
                f"The app will try to keep running — you may want to save your work "
                f"(File > Save Project) and restart if things seem off afterwards."
            )
        except Exception:
            pass  # if even the error dialog fails, don't let that crash us further
    sys.excepthook = handle_exception


def main():
    app = QApplication(sys.argv)
    app.setOrganizationName(font_settings.ORG)
    app.setApplicationName(font_settings.APP)
    app.setWindowIcon(QIcon(str(ICONS_DIR / "app-icon.svg")))
    theme.apply_theme(app)
    install_exception_hook()
    window = MainWindow()
    window.show()
    sys.exit(app.exec())



if __name__ == "__main__":
    main()
