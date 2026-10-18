"""
font_gap.py — EXPERIMENTAL: a small, automatic gap between neighbouring words
that are written in different fonts on the same line.

Why: a word's space character takes the width of whichever font it was typed
in, and Nastaliq's space is very narrow (about 0.13 em, versus 0.32 em in an
ordinary Latin/Arabic text font) while its letters overhang their advance
width. Put a word in a different font right after it and the two can touch.
This adds a little extra room exactly at those joins, and nowhere else.

How it works (and why it's built this way):

  * The gap is extra absolute letter-spacing on the ONE space character
    between the two words (the last space, if there are several). Spacing a
    space is safe for cursive Arabic-script shaping -- unlike spacing a
    letter, it never splits or un-joins a word.

  * It is applied as a *layout* format (QTextLayout.setFormats on each
    block's own layout) -- the same mechanism Qt itself uses for IME
    underlines -- and NOT as a character format in the document. So the
    document's text, its character formats, its undo/redo history, its
    "modified" flag, saved project files, and the word/letter-spacing
    controls are all completely untouched: the gap is derived on the fly from
    "which font is each word in", never stored. Nothing to save, nothing to
    undo, nothing that can go stale in a file.

  * FontGapManager keeps it current on the live page. QTextDocument emits
    contentsChange *before* it re-lays-out the changed text, so the manager
    updates just the affected blocks first and the layout is computed once,
    already correct (no flash of the un-gapped version).

  * Exports, copies and printing all work from a clone of the document, and
    layout formats are not cloned -- so exporter._layout_copy() calls
    apply_document() on its clone. That is why SVG/PDF/EPS/PNG, Copy as SVG
    and Print show exactly the same gaps as the screen.

Sizes are fractions of the larger neighbouring font's size (see LEVELS), so
the gap scales with the text.

TO ROLL THIS BACK: either choose Tools > Gap Between Fonts > Off (instant,
reversible, nothing is left behind), or delete this file and the four small
hooks that mention font_gap: the import + manager + Tools-menu block in
main.py, the apply_document() call in exporter._layout_copy(), and
test_font_gap.py. Nothing else depends on it.
"""

import bisect
import sys
import traceback

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QFont, QTextCharFormat, QTextFormat, QTextLayout

# Fraction of the (larger neighbouring) font size added at a font change.
# "normal" is the default: for a 36 pt (48 px) run that's about 3.4 px --
# enough to separate the words, small enough that it doesn't read as an
# extra space.
LEVELS = {"off": 0.0, "subtle": 0.04, "normal": 0.07, "wide": 0.12}
LEVEL_LABELS = {"off": "Off", "subtle": "Subtle", "normal": "Normal", "wide": "Wide"}
DEFAULT_LEVEL = "normal"

_POINT_TO_PX = 96.0 / 72.0
_CARRIERS = " \u00a0"  # characters that can carry the gap: space and no-break space

# Marks a layout FormatRange as ours, so the manager only ever replaces its own
# ranges and leaves any other layout formats (e.g. an input method's) alone.
_MARK = int(QTextFormat.Property.UserProperty) + 71

# The level exports/copies/printing should use. Read from worker threads
# (a plain str/dict lookup, so no locking needed); written on the GUI thread.
_active_level = DEFAULT_LEVEL


def active_level() -> str:
    return _active_level


def active_em() -> float:
    return LEVELS[_active_level]


def set_active_level(level: str) -> str:
    global _active_level
    _active_level = level if level in LEVELS else DEFAULT_LEVEL
    return _active_level


def _font_px(font: QFont) -> float:
    px = font.pixelSize()
    if px > 0:
        return float(px)
    pt = font.pointSizeF()
    return pt * _POINT_TO_PX if pt > 0 else 16.0


def plan_block(block, em: float):
    """The gaps one block should have: a list of (index_in_block, gap_px),
    one per join between two differently-fonted words. Pure function of the
    block's text and character formats."""
    if em <= 0:
        return []
    text = block.text()
    n = len(text)
    if n < 3:  # needs word, space, word
        return []

    base = block.position()
    spans = []  # (start, family, px) per fragment, in order
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid() and frag.length() > 0:
            font = frag.charFormat().font()
            spans.append((frag.position() - base, font.family(), _font_px(font)))
        it += 1
    # The overwhelmingly common case -- the whole line in one font -- exits here.
    if len({family for _s, family, _p in spans}) < 2:
        return []
    starts = [s for s, _f, _p in spans]

    def span_at(i):
        return spans[bisect.bisect_right(starts, i) - 1]

    gaps = []
    prev = None  # (index of previous word's last char, its family, its px)
    i = 0
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < n and not text[j].isspace():
            j += 1
        _s, first_family, first_px = span_at(i)
        _s, last_family, last_px = span_at(j - 1)
        if prev is not None and prev[1] != first_family:
            k = i - 1  # the last real space between the two words
            while k > prev[0] and text[k] not in _CARRIERS:
                k -= 1
            if k > prev[0]:
                gaps.append((k, em * max(prev[2], first_px)))
        prev = (j - 1, last_family, last_px)
        i = j
    return gaps


def _make_range(index: int, gap_px: float):
    fmt = QTextCharFormat()
    fmt.setFontLetterSpacingType(QFont.SpacingType.AbsoluteSpacing)
    fmt.setFontLetterSpacing(gap_px)
    fmt.setProperty(_MARK, True)
    r = QTextLayout.FormatRange()
    r.start = index
    r.length = 1
    r.format = fmt
    return r


def apply_block(block, em: float) -> bool:
    """Make this block's layout carry exactly the gaps plan_block() wants.
    Returns True if anything changed (so callers know whether a relayout is
    needed). Never touches the document itself."""
    layout = block.layout()
    if layout is None:
        return False
    current = layout.formats()
    have = [
        (r.start, round(r.format.fontLetterSpacing(), 2))
        for r in current
        if r.format.hasProperty(_MARK)
    ]
    # em <= 0 means "remove": don't even call the planner, so cleanup after a
    # planner failure can't hit the same failure again.
    desired = plan_block(block, em) if em > 0 else []
    want = [(k, round(px, 2)) for k, px in desired]
    if have == want:
        return False
    others = [r for r in current if not r.format.hasProperty(_MARK)]
    layout.setFormats(others + [_make_range(k, px) for k, px in desired])
    return True


def apply_document(doc, em=None, relayout=False) -> int:
    """Apply (or, with em=0, remove) the gaps on every block of `doc`.
    em=None means the currently active level. Returns how many blocks
    changed. relayout=True also tells an already-laid-out document to redo
    its layout (not needed for a fresh clone, which is laid out afterwards)."""
    if em is None:
        em = active_em()
    changed = 0
    block = doc.begin()
    while block.isValid():
        if apply_block(block, em):
            changed += 1
        block = block.next()
    if changed and relayout:
        doc.markContentsDirty(0, max(doc.characterCount(), 1))
    return changed


class FontGapManager(QObject):
    """Keeps a live QTextDocument's gaps current as it is edited.

    Cheap by construction: per edit it only re-examines the blocks the edit
    touched, and a block whose words all share one font exits after one pass
    over its fragments. Any unexpected error switches the feature off (once,
    with a message) rather than raising from inside Qt's signal machinery,
    which would otherwise repeat on every keystroke."""

    failed = pyqtSignal(str)

    def __init__(self, document, level=DEFAULT_LEVEL, parent=None):
        super().__init__(parent)
        self._doc = document
        self._broken = False
        self._level = level if level in LEVELS else DEFAULT_LEVEL
        set_active_level(self._level)
        document.contentsChange.connect(self._on_contents_change)
        self.refresh_all()

    def level(self) -> str:
        return self._level

    def set_level(self, level: str):
        self._level = level if level in LEVELS else DEFAULT_LEVEL
        set_active_level(self._level)
        self._broken = False
        self.refresh_all()

    def refresh_all(self):
        try:
            apply_document(self._doc, LEVELS[self._level], relayout=True)
        except Exception:
            self._fail()

    def _on_contents_change(self, position, chars_removed, chars_added):
        if self._broken:
            return
        try:
            doc = self._doc
            em = LEVELS[self._level]
            last = max(doc.characterCount() - 1, 0)
            start = min(max(position, 0), last)
            end = position + chars_added
            block = doc.findBlock(start)
            while block.isValid() and block.position() <= end:
                apply_block(block, em)
                block = block.next()
        except Exception:
            self._fail()

    def _fail(self):
        self._broken = True
        set_active_level("off")
        detail = traceback.format_exc()
        try:  # leave the page looking exactly like it would without the feature
            apply_document(self._doc, 0.0, relayout=True)
        except Exception:
            pass
        print("font_gap: disabled after an error:\n" + detail, file=sys.stderr)
        self.failed.emit(
            detail.strip().splitlines()[-1] if detail.strip() else "unknown error"
        )
