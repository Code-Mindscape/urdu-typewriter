"""
canvas.py — the typewriter "page".

Correct Urdu shaping here comes entirely from *not* fighting Qt: we feed it
plain, ordinary Urdu Unicode text (logical order, unshaped) and a real Urdu
font, set right-to-left layout direction, and let Qt's own text engine
(HarfBuzz, under the hood) join the letters into their correct contextual
forms. Pre-reshaping the text ourselves (e.g. with a library like
arabic_reshaper) would be actively wrong here — Qt would then try to shape
already-shaped presentation-form characters a second time.

Formatting ("pending format" below) is tracked explicitly rather than left
to Qt's default behavior. Qt's default is: whatever format is under the
cursor becomes the format for the next typed character — which means
clicking back into the canvas after picking a color from the toolbar
would otherwise drop that color the moment the cursor lands somewhere
else. We instead treat the toolbar's last-chosen family/style/size/color
as the one source of truth for "what happens next," and re-assert it every
time the cursor moves without a selection.
"""

import contextlib

from PyQt6.QtWidgets import QTextEdit, QGraphicsItem
from PyQt6.QtGui import (
    QColor,
    QFont,
    QTextCharFormat,
    QTextCursor,
    QTextBlockFormat,
    QPainter,
    QPixmap,
    QBrush,
    QPen,
)
from PyQt6.QtCore import Qt, QRectF, QEventLoop

import roman_urdu


class PageCanvas(QTextEdit):
    def __init__(
        self,
        width_px=1080,
        height_px=1080,
        background=None,
        margin_px=40,
        default_family="Noto Nastaliq Urdu",
        default_style="Regular",
        default_size=36,
        parent=None,
    ):
        super().__init__(parent)
        self.page_width = width_px
        self.page_height = height_px
        self.roman_urdu_mode = False
        # Set for the duration of the standard right-click menu's exec()
        # call, below — see contextMenuEvent()'s docstring for why
        # MainWindow checks this before zooming.
        self.context_menu_open = False
        # App-level actions (owned by MainWindow, e.g. "Copy as SVG (Curves)")
        # that the right-click menu should offer under Qt's standard
        # Cut/Copy/Paste entries. PageCanvas doesn't know about MainWindow's
        # actions, so MainWindow hands them over via set_extra_context_actions().
        self._extra_context_actions = []

        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.document().setDocumentMargin(margin_px)
        self.setFixedSize(width_px, height_px)
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._background = QColor(background) if background else QColor("white")
        # None of these three paint anything by themselves -- _refresh_background()
        # (below) is the single place that decides how they combine, so there's
        # one source of truth for "what does the page background actually look
        # like right now" instead of that logic being spread across callers.
        self._background_image = None  # QImage or None
        self._background_image_mode = "fit"  # "fit" | "fill" | "center" | "tile"
        self._border_width = 0
        self._border_color = QColor("black")
        # autoFillBackground normally has Qt paint the palette's Base color in
        # behind everything else before paintEvent() even runs -- fine for a
        # solid color, but it would silently paint over a background image
        # drawn in our own paintEvent() below. Painting the solid-color case
        # ourselves too (in _refresh_background) keeps one consistent path
        # for both instead of a solid color and an image being handled two
        # completely different ways.
        self.setAutoFillBackground(False)
        self._refresh_background()

        self._pending_family = default_family
        self._pending_style = default_style
        self._pending_size = default_size
        self._pending_color = QColor("black")
        self._pending_underline = False
        self._pending_strikeout = False
        self._pending_highlight = None  # QColor or None ("no highlight")
        self._pending_letter_spacing = 100  # percent; 100 = normal
        # Extra space added to every word gap, in page pixels (0 = the
        # font's own space; negative = tighter). Deliberately separate from
        # letter spacing above: it only moves whole words apart, never the
        # letters inside them. See set_word_spacing().
        self._pending_word_spacing = 0.0
        # (key, document revision) of the most recent live spacing edit, so a
        # run of small adjustments (arrow clicks, a drag) folds into a single
        # undo step instead of dozens -- see _live_edit().
        self._last_spacing_edit = None
        self._pending_vertical = QTextCharFormat.VerticalAlignment.AlignNormal
        self._apply_pending(to_selection=False)

        self._vertical_align = (
            "top"  # "top" | "middle" | "bottom" -- see set_vertical_align()
        )
        self._border_style = Qt.PenStyle.SolidLine
        self._in_vertical_align = (
            False  # reentrancy guard -- see _apply_vertical_align()
        )

        self.cursorPositionChanged.connect(self._on_cursor_moved)
        self.selectionChanged.connect(self._sync_pending_word_spacing)
        self.textChanged.connect(self._apply_vertical_align)

        # setAlignment() and document().setDocumentMargin() above are both
        # real, undoable document edits as far as QTextDocument is
        # concerned, even though nothing visible changed (there's no text
        # yet). Left alone, that means isUndoAvailable() is already True
        # the moment this constructor returns -- before the user has typed
        # a single character -- which would make an Undo menu item enabled
        # from app startup, and any code watching undoAvailable/
        # redoAvailable for "the user has made an edit" (main.py's Undo/
        # Redo menu state, for one) would never see the False -> True
        # transition it's listening for, since it already happened here.
        self.document().clearUndoRedoStacks()

    # -- background, image, and border -----------------------------------
    def _refresh_background(self):
        self.update()

    def set_background(self, color: QColor):
        self._background = QColor(color)
        self._refresh_background()

    def background_color(self) -> QColor:
        return QColor(self._background)

    def set_background_image(self, image, mode=None):
        """image: a QImage, or None to remove the background image and go
        back to a plain solid-color background."""
        self._background_image = image
        if mode is not None:
            self._background_image_mode = mode
        self.update()

    def background_image(self):
        return self._background_image

    def set_background_image_mode(self, mode: str):
        """mode: "fit" (whole image visible, letterboxed if needed), "fill"
        (image covers the page, cropped if needed), "center" (no scaling),
        or "tile" (repeated at natural size)."""
        self._background_image_mode = mode
        self.update()

    def background_image_mode(self) -> str:
        return self._background_image_mode

    def set_border(
        self, width_px: int, color: QColor = None, style: Qt.PenStyle = None
    ):
        self._border_width = max(0, int(width_px))
        if color is not None:
            self._border_color = QColor(color)
        if style is not None:
            self._border_style = style
        self.update()

    def border_width(self) -> int:
        return self._border_width

    def border_color(self) -> QColor:
        return QColor(self._border_color)

    def border_style(self) -> Qt.PenStyle:
        return self._border_style

    def _paint_background_image(self, painter, rect: QRectF):
        img = self._background_image
        iw, ih = img.width(), img.height()
        if iw <= 0 or ih <= 0:
            return
        mode = self._background_image_mode
        if mode == "tile":
            painter.drawTiledPixmap(rect, QPixmap.fromImage(img))
            return
        rw, rh = rect.width(), rect.height()
        if mode == "center":
            x = rect.x() + (rw - iw) / 2
            y = rect.y() + (rh - ih) / 2
            painter.drawImage(QRectF(x, y, iw, ih), img)
            return
        # "fit" (contain: whole image visible) or "fill" (cover: page
        # fully covered, image cropped) -- same math either way, just
        # min() vs max() for which axis determines the scale.
        scale = (max if mode == "fill" else min)(rw / iw, rh / ih)
        dw, dh = iw * scale, ih * scale
        target = QRectF(rect.x() + (rw - dw) / 2, rect.y() + (rh - dh) / 2, dw, dh)
        if mode == "fill":
            painter.save()
            painter.setClipRect(rect)
            painter.drawImage(target, img)
            painter.restore()
        else:
            painter.drawImage(target, img)

    def paintEvent(self, event):
        # autoFillBackground is off (see __init__) specifically so this can
        # paint a background image beneath the text without Qt's automatic
        # palette-based fill wiping it out first.
        rect = self.viewport().rect()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(rect, self._background)
        if self._background_image is not None and not self._background_image.isNull():
            self._paint_background_image(painter, QRectF(rect))
        painter.end()

        super().paintEvent(event)  # the actual text, cursor, and selection

        if self._border_width > 0:
            painter = QPainter(self.viewport())
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(self._border_color, self._border_width)
            pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
            pen.setStyle(self._border_style)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            half = self._border_width / 2
            painter.drawRect(
                QRectF(
                    half,
                    half,
                    rect.width() - self._border_width,
                    rect.height() - self._border_width,
                )
            )
            painter.end()

    # -- page geometry ----------------------------------------------------
    def resize_page(self, width_px, height_px):
        self.page_width = width_px
        self.page_height = height_px
        self.setFixedSize(width_px, height_px)
        self._apply_vertical_align()

    def set_margin(self, margin_px):
        self.document().setDocumentMargin(margin_px)
        self._apply_vertical_align()

    def set_vertical_align(self, mode: str):
        """mode: "top" (default), "middle", or "bottom" -- pushes the text
        block down within the page when its content doesn't fill the
        page's height, the way a design tool vertically aligns a text box.
        Recomputed automatically as content changes (wired to textChanged
        in __init__) since how much push is needed depends on how tall
        the current text actually lays out to."""
        self._vertical_align = mode
        self._apply_vertical_align()

    def vertical_align(self) -> str:
        return self._vertical_align

    def _apply_vertical_align(self):
        if self._in_vertical_align:
            return
        self._in_vertical_align = True
        try:
            root_frame = self.document().rootFrame()
            fmt = root_frame.frameFormat()
            base_margin = self.document().documentMargin()
            # Reset to the base top margin *before* measuring, so the height
            # measured below reflects the content alone -- not a stale offset
            # from a previous "middle"/"bottom" computation compounding on
            # top of itself on every subsequent keystroke.
            if fmt.topMargin() != base_margin:
                fmt.setTopMargin(base_margin)
                root_frame.setFrameFormat(fmt)
            if self._vertical_align != "top":
                content_height = self.document().size().height()
                extra = max(0.0, self.page_height - content_height)
                additional = extra / 2 if self._vertical_align == "middle" else extra
                if additional > 0:
                    fmt.setTopMargin(base_margin + additional)
                    root_frame.setFrameFormat(fmt)
        finally:
            self._in_vertical_align = False

    # -- pending format (the "sticky formatting" fix) ------------------------
    def _build_pending_font(self) -> QFont:
        f = QFont(self._pending_family)
        if self._pending_style:
            f.setStyleName(self._pending_style)
        f.setPointSize(self._pending_size)
        f.setLetterSpacing(
            QFont.SpacingType.PercentageSpacing, self._pending_letter_spacing
        )
        f.setWordSpacing(self._pending_word_spacing)
        return f

    def _make_format(self) -> QTextCharFormat:
        fmt = QTextCharFormat()
        fmt.setFont(self._build_pending_font())
        fmt.setForeground(self._pending_color)
        fmt.setFontUnderline(self._pending_underline)
        fmt.setFontStrikeOut(self._pending_strikeout)
        fmt.setVerticalAlignment(self._pending_vertical)
        if self._pending_highlight is not None:
            fmt.setBackground(QBrush(self._pending_highlight))
        else:
            fmt.clearBackground()
        return fmt

    def _apply_pending(self, to_selection: bool):
        fmt = self._make_format()
        cursor = self.textCursor()
        if to_selection and cursor.hasSelection():
            cursor.mergeCharFormat(fmt)
            self.setTextCursor(cursor)
        self.setCurrentCharFormat(fmt)

    def _on_cursor_moved(self):
        # Word spacing follows the text under the cursor (see
        # _sync_pending_word_spacing) rather than being purely "sticky" like
        # the other pending values -- it has to, because applying any other
        # formatting to a selection re-stamps the whole pending format onto
        # it, and a stale pending word spacing would silently reset the
        # selection's own. Do this first so the format asserted below has it.
        self._sync_pending_word_spacing()
        if not self.textCursor().hasSelection():
            self.setCurrentCharFormat(self._make_format())

    def set_pending_family(self, family: str):
        self._pending_family = family
        self._apply_pending(to_selection=True)

    def set_pending_style(self, style: str):
        self._pending_style = style
        self._apply_pending(to_selection=True)

    def set_pending_size(self, size: int):
        self._pending_size = size
        self._apply_pending(to_selection=True)

    def set_pending_color(self, color: QColor):
        self._pending_color = QColor(color)
        self._apply_pending(to_selection=True)

    def set_pending_underline(self, on: bool):
        self._pending_underline = bool(on)
        self._apply_pending(to_selection=True)

    def set_pending_strikeout(self, on: bool):
        self._pending_strikeout = bool(on)
        self._apply_pending(to_selection=True)

    def set_pending_highlight(self, color):
        """color: a QColor, or None to remove any highlight from here on."""
        self._pending_highlight = QColor(color) if color is not None else None
        self._apply_pending(to_selection=True)

    def set_pending_letter_spacing(self, percent: int):
        self._pending_letter_spacing = percent
        self._apply_pending(to_selection=True)

    def set_pending_superscript(self, on: bool):
        # Superscript and subscript are mutually exclusive -- turning one on
        # always means the other is off, never both raised and lowered.
        self._pending_vertical = (
            QTextCharFormat.VerticalAlignment.AlignSuperScript
            if on
            else QTextCharFormat.VerticalAlignment.AlignNormal
        )
        self._apply_pending(to_selection=True)

    def set_pending_subscript(self, on: bool):
        self._pending_vertical = (
            QTextCharFormat.VerticalAlignment.AlignSubScript
            if on
            else QTextCharFormat.VerticalAlignment.AlignNormal
        )
        self._apply_pending(to_selection=True)

    def clear_formatting(self):
        """Resets the selection (or, with none, what's typed next) back to
        the current pending family/style/size/color with no underline,
        strikethrough, highlight, letter spacing, word spacing, or
        superscript/subscript -- the usual "Clear Formatting" a word
        processor offers."""
        self._pending_underline = False
        self._pending_strikeout = False
        self._pending_highlight = None
        self._pending_letter_spacing = 100
        self._pending_word_spacing = 0.0
        self._pending_vertical = QTextCharFormat.VerticalAlignment.AlignNormal
        cursor = self.textCursor()
        if cursor.hasSelection():
            cursor.mergeCharFormat(self._make_format())
            self.setTextCursor(cursor)
        self.setCurrentCharFormat(self._make_format())

    def pending_family(self):
        return self._pending_family

    def pending_style(self):
        return self._pending_style

    def pending_size(self):
        return self._pending_size

    def pending_color(self) -> QColor:
        return QColor(self._pending_color)

    def pending_underline(self) -> bool:
        return self._pending_underline

    def pending_strikeout(self) -> bool:
        return self._pending_strikeout

    def pending_highlight(self):
        return (
            QColor(self._pending_highlight)
            if self._pending_highlight is not None
            else None
        )

    def pending_letter_spacing(self) -> int:
        return self._pending_letter_spacing

    def pending_superscript(self) -> bool:
        return (
            self._pending_vertical == QTextCharFormat.VerticalAlignment.AlignSuperScript
        )

    def pending_subscript(self) -> bool:
        return (
            self._pending_vertical == QTextCharFormat.VerticalAlignment.AlignSubScript
        )

    def apply_alignment(self, alignment: Qt.AlignmentFlag):
        self.setAlignment(alignment)

    # -- line height and word spacing (the live toolbar controls) -----------
    # Line height is a percentage of the font's normal line (100 = single);
    # word spacing is extra pixels per word gap (0 = normal). Both are
    # applied the moment the value changes, so dragging or clicking through
    # values shows the result on the page as it happens.
    LINE_HEIGHT_RANGE = (25, 500)
    WORD_SPACING_RANGE = (-100, 1000)

    @contextlib.contextmanager
    def _live_edit(self, cursor, key):
        """One undo step per *adjustment session*, not per tick of a spin
        box. Consecutive edits from the same control on the same target,
        with nothing else changed in between (the document revision is
        exactly what our last edit left it at), are joined into the
        previous undo step; anything else -- typing, another format, a
        different selection -- starts a fresh one, so this can never
        swallow an unrelated edit."""
        doc = self.document()
        before = doc.revision()
        if self._last_spacing_edit == (key, before):
            cursor.joinPreviousEditBlock()
        else:
            cursor.beginEditBlock()
        try:
            yield
        finally:
            cursor.endEditBlock()
            if doc.revision() != before:  # only if this really made an undo step
                self._last_spacing_edit = (key, doc.revision())

    def apply_line_spacing(self, percent: int):
        """Proportional line height (100 = single, 150 = 1.5x, ...), applied
        to the current paragraph or every paragraph a selection spans --
        same scope as apply_alignment() above, and for the same reason:
        line spacing is a paragraph-level property in Qt's text model
        (QTextBlockFormat), not a per-character one, so "pending for the
        next character typed" (like family/size/color above) doesn't
        apply to it the same way."""
        lo, hi = self.LINE_HEIGHT_RANGE
        percent = max(lo, min(hi, int(percent)))
        cursor = self.textCursor()
        if cursor.hasSelection():
            key = ("line", cursor.selectionStart(), cursor.selectionEnd())
        else:
            key = ("line", cursor.block().position())
        bf = QTextBlockFormat()
        bf.setLineHeight(
            float(percent), QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        )
        with self._live_edit(cursor, key):
            cursor.mergeBlockFormat(bf)
        self.setTextCursor(cursor)

    def current_line_spacing(self) -> int:
        cursor = self.textCursor()
        block = (
            self.document().findBlock(cursor.selectionStart())
            if cursor.hasSelection()
            else cursor.block()
        )
        bf = block.blockFormat()
        height = bf.lineHeight()
        if (
            height <= 0
            or bf.lineHeightType()
            != QTextBlockFormat.LineHeightTypes.ProportionalHeight.value
        ):
            return 100
        return int(round(height))

    def set_word_spacing(self, px):
        """Sets the extra space between words, in pixels (0 = normal,
        negative = tighter), and applies it *immediately*: to the selection
        if there is one, otherwise to the whole current paragraph -- a
        change you can't see would look like the control was broken. It's
        also what gets typed next. Only word gaps change; the letters
        inside each word are never touched (that's letter spacing, a
        separate setting).

        Applies just the word-spacing property, not the whole pending
        format, so a selection's own font, size and color are left alone."""
        lo, hi = self.WORD_SPACING_RANGE
        px = float(max(lo, min(hi, px)))
        self._pending_word_spacing = px

        cursor = self.textCursor()
        has_selection = cursor.hasSelection()
        if has_selection:
            start, end = cursor.selectionStart(), cursor.selectionEnd()
        else:
            block = cursor.block()
            start = block.position()
            end = start + block.length() - 1  # length() includes the paragraph break
        if end > start:
            target = QTextCursor(self.document())
            target.setPosition(start)
            target.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setFontWordSpacing(px)
            with self._live_edit(target, ("word", start, end)):
                target.mergeCharFormat(fmt)
        if not has_selection:
            # Setting the format on a selection would replace all of its
            # formatting, so only do this for the plain-cursor case.
            self.setCurrentCharFormat(self._make_format())

    def pending_word_spacing(self) -> float:
        return self._pending_word_spacing

    def _word_spacing_at_cursor(self):
        """Word spacing (px) of the text under the cursor -- the first
        selected character if there's a selection, otherwise the character
        beside the cursor -- or None when there's no character to read it
        from (an empty paragraph), in which case callers fall back to the
        pending value."""
        cursor = self.textCursor()
        doc = self.document()
        if cursor.hasSelection():
            pos, end = cursor.selectionStart(), cursor.selectionEnd()
            # Skip a leading paragraph break so a selection that starts at
            # the end of a line reads the first real character after it.
            while pos < end:
                block = doc.findBlock(pos)
                if pos < block.position() + block.length() - 1:
                    break
                pos = block.position() + block.length()
            if pos >= end:
                return None
            char_pos = pos
        else:
            block = cursor.block()
            if block.length() <= 1:
                return None  # empty paragraph
            pos = cursor.position()
            # Just before the cursor; at the very start of a paragraph
            # there is no "before", so take the first character instead.
            char_pos = pos if pos == block.position() else pos - 1
        # A fresh cursor, not the widget's: the widget's may carry an
        # explicit "next typed character" format that isn't the text's.
        probe = QTextCursor(doc)
        probe.setPosition(char_pos + 1)
        return float(probe.charFormat().fontWordSpacing())

    def current_word_spacing(self) -> float:
        """What a word-spacing control should show right now: the spacing
        of the text under the cursor/selection, or the pending value if
        there's nothing there to read."""
        found = self._word_spacing_at_cursor()
        return self._pending_word_spacing if found is None else found

    def _sync_pending_word_spacing(self):
        found = self._word_spacing_at_cursor()
        if found is not None:
            self._pending_word_spacing = found

    # -- stats --------------------------------------------------------------
    def word_count(self) -> int:
        return len(self.toPlainText().split())

    def character_count(self) -> int:
        return len(self.toPlainText())

    # -- Roman Urdu auto-conversion ---------------------------------------
    _TRIGGER_KEYS = (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter)
    _TRIGGER_CHARS = {".", ",", "!", "?", ":", ";"}

    def keyPressEvent(self, event):
        if self.roman_urdu_mode and (
            event.key() in self._TRIGGER_KEYS or event.text() in self._TRIGGER_CHARS
        ):
            self._convert_word_before_cursor()
        super().keyPressEvent(event)

    def focusOutEvent(self, event):
        # Catches a trailing Roman word left unconverted because the user
        # clicked away (to the toolbar, say) instead of pressing space/enter.
        if self.roman_urdu_mode:
            self._convert_word_before_cursor()
        super().focusOutEvent(event)

    def set_extra_context_actions(self, actions):
        """Actions to append (after a separator) to the standard right-click
        menu. The same QAction objects can also live in a main-window menu;
        Qt keeps both in sync (enabled state, text, shortcut)."""
        self._extra_context_actions = list(actions)

    def contextMenuEvent(self, event):
        # menu.exec() blocks in its own nested event loop until the menu
        # closes, but a Ctrl+scroll (or a Zoom Tool click) can still reach
        # MainWindow's event filter and zoom the page while that menu is
        # still open on screen — the menu itself is a separate top-level
        # popup, not part of the page, so it doesn't move or resize when
        # the page does, and the two visibly drift apart. context_menu_open
        # is set for exactly the span this menu is up, and MainWindow
        # checks it before zooming from either path (including
        # ZoomableGraphicsView.wheelEvent for wheel events landing on the
        # view itself, not just this widget), so a scroll or a zoom-tool
        # click while this menu is open does nothing instead of zooming
        # the page out from under it.
        #
        # Separately: this widget is embedded in the page's QGraphicsScene
        # via a QGraphicsProxyWidget, and Qt's proxy-widget machinery
        # automatically embeds any popup a proxied widget opens into that
        # same scene too, as its own child proxy (see QGraphicsProxyWidget's
        # class docs: "takes care of automatically embedding popup children
        # of embedded widgets"). That means this menu would otherwise be
        # painted through the *page's* current zoom transform right along
        # with the page itself, so it'd visibly grow or shrink with every
        # zoom change — confirmed directly (not just from the docs): once
        # shown, menu.graphicsProxyWidget() is a real, distinct proxy, not
        # None. ItemIgnoresTransformations, set on exactly that proxy, is
        # the standard Qt flag for "this graphics item ignores the view's
        # transform" — it keeps the menu anchored where it was opened
        # without letting it scale, so it's always a normal, constant,
        # readable size regardless of the page's zoom level, the same as
        # every other app's right-click menu.
        #
        # Getting a chance to set that flag is why this uses popup() (which
        # returns immediately, once the menu — and by then its auto-created
        # proxy — actually exists) plus a local event loop tied to
        # aboutToHide, rather than the simpler menu.exec(), which shows and
        # blocks in one call with no opening to act in between.
        menu = self.createStandardContextMenu(event.pos())
        if self._extra_context_actions:
            menu.addSeparator()
            for action in self._extra_context_actions:
                menu.addAction(action)
            menu.setToolTipsVisible(True)  # so the action's tooltip explains itself
        self.context_menu_open = True
        try:
            menu.popup(event.globalPos())
            proxy = menu.graphicsProxyWidget()
            if proxy is not None:
                proxy.setFlag(
                    QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations, True
                )
            loop = QEventLoop()
            menu.aboutToHide.connect(loop.quit)
            loop.exec()
        finally:
            self.context_menu_open = False

    def _convert_word_before_cursor(self):
        cursor = self.textCursor()
        if cursor.hasSelection():
            return
        block_text = cursor.block().text()
        pos_in_block = cursor.positionInBlock()
        start = pos_in_block
        while (
            start > 0
            and block_text[start - 1].isascii()
            and block_text[start - 1].isalpha()
        ):
            start -= 1
        if start == pos_in_block:
            return  # nothing Latin immediately before the cursor
        word = block_text[start:pos_in_block]

        word_cursor = self.textCursor()
        word_cursor.setPosition(cursor.block().position() + start)
        word_cursor.setPosition(cursor.position(), QTextCursor.MoveMode.KeepAnchor)
        converted = roman_urdu.transliterate_word(word)
        if converted == word:
            return  # nothing recognizable to convert; leave the Latin text alone
        word_cursor.insertText(converted, self._make_format())
        self.setTextCursor(word_cursor)

    def convert_selection_to_urdu(self):
        """Manual fallback for text already typed/pasted in Roman Urdu —
        the toolbar toggle only catches new words as you type them."""
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        text = cursor.selectedText()
        # QTextCursor uses U+2029 as its paragraph separator in selectedText()
        text = text.replace("\u2029", "\n")
        converted = roman_urdu.transliterate(text)
        cursor.insertText(converted, self._make_format())
        return True

    # -- Voice to Urdu (live dictation) ------------------------------------
    def insert_dictated_text(self, text: str):
        """Inserts one live-transcribed chunk from Voice to Urdu at the
        cursor, in the current pending format — same idea as typing, just
        arriving as a whole recognized phrase instead of one keystroke at
        a time. Adds a separating space first if needed so consecutive
        chunks don't run into each other mid-word, without doubling up
        spaces or adding a leading one at the very start of the page."""
        text = text.strip()
        if not text:
            return
        self.setFocus()
        cursor = self.textCursor()
        if cursor.position() > 0:
            probe = QTextCursor(cursor)
            probe.movePosition(
                QTextCursor.MoveOperation.PreviousCharacter,
                QTextCursor.MoveMode.KeepAnchor,
            )
            preceding = probe.selectedText()
            if preceding and not preceding.isspace():
                cursor.insertText(" ", self._make_format())
        cursor.insertText(text, self._make_format())
        self.setTextCursor(cursor)
