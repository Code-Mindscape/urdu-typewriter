"""
test_spacing.py — headless checks for the line-height and word-spacing
controls' canvas behavior:

  * changes apply to the text immediately (live), to the selection or the
    current paragraph, and only touch what they should
  * word spacing moves words apart without touching letter spacing, and
    leaves font/size/color alone
  * a run of adjustments is one undo step; unrelated edits are never
    swallowed into it
  * the value shown follows the text under the cursor
  * new text typed afterwards keeps the spacing

    python test_spacing.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from canvas import PageCanvas
from main import load_bundled_fonts

app = QApplication(sys.argv)
results = []


def check(label, ok, detail=""):
    print(
        f"[{'OK' if ok else 'FAIL'}] {label}"
        + (f" — {detail}" if detail and not ok else "")
    )
    results.append(ok)


family = load_bundled_fonts()
LINE_A = "زندگی ایک خوبصورت سفر ہے"
LINE_B = "محبت سب سے بڑی دولت ہے"


def make(text=LINE_A + "\n" + LINE_B):
    c = PageCanvas(
        794, 1123, QColor("white"), 40, default_family=family, default_size=36
    )
    c.show()
    c.textCursor().insertText(text)
    c.document().clearUndoRedoStacks()
    return c


def block_width(c, n):
    return c.document().findBlockByNumber(n).layout().lineAt(0).naturalTextWidth()


def ws_of(c, pos):
    """word spacing stored on the character at absolute position `pos`."""
    cur = QTextCursor(c.document())
    cur.setPosition(pos + 1)
    return cur.charFormat().fontWordSpacing()


def select_block(c, n):
    b = c.document().findBlockByNumber(n)
    cur = c.textCursor()
    cur.setPosition(b.position())
    cur.setPosition(b.position() + len(b.text()), QTextCursor.MoveMode.KeepAnchor)
    c.setTextCursor(cur)


def put_cursor(c, pos):
    cur = c.textCursor()
    cur.setPosition(pos)
    c.setTextCursor(cur)


# ===================================================================== word spacing
c = make()
base_a, base_b = block_width(c, 0), block_width(c, 1)
gaps_a = LINE_A.count(" ")

select_block(c, 0)
c.set_word_spacing(20)
check(
    "selection: line A widens by (gaps x spacing) immediately",
    abs(block_width(c, 0) - (base_a + 20 * gaps_a)) < 0.5,
    f"{block_width(c, 0)} vs {base_a + 20 * gaps_a}",
)
check("selection: line B untouched", abs(block_width(c, 1) - base_b) < 0.01)
check("stored on the characters", ws_of(c, 3) == 20.0, str(ws_of(c, 3)))

# letters inside words must not move: letter spacing / glyph runs unchanged
fmt = QTextCursor(c.document())
fmt.setPosition(3)
check(
    "letter spacing is untouched (still 100%)",
    c.pending_letter_spacing() == 100
    and c.document()
    .findBlockByNumber(0)
    .begin()
    .fragment()
    .charFormat()
    .font()
    .letterSpacing()
    == 100.0,
)

# no selection -> the whole current paragraph
c = make()
put_cursor(c, c.document().findBlockByNumber(1).position() + 4)
c.set_word_spacing(
    10
)  # small enough that the paragraph doesn't wrap onto a second line
check(
    "no selection: current paragraph widens",
    abs(block_width(c, 1) - (base_b + 10 * LINE_B.count(" "))) < 0.5,
    f"{block_width(c, 1)} vs {base_b + 10 * LINE_B.count(' ')}",
)
check("no selection: other paragraph untouched", abs(block_width(c, 0) - base_a) < 0.01)

# negative tightens
c = make()
select_block(c, 0)
c.set_word_spacing(-5)
check(
    "negative spacing tightens",
    block_width(c, 0) < base_a,
    f"{block_width(c, 0)} vs {base_a}",
)

# range clamp
c = make()
select_block(c, 0)
c.set_word_spacing(99999)
check(
    "value is clamped to the maximum",
    c.pending_word_spacing() == PageCanvas.WORD_SPACING_RANGE[1],
)
c.set_word_spacing(-99999)
check(
    "value is clamped to the minimum",
    c.pending_word_spacing() == PageCanvas.WORD_SPACING_RANGE[0],
)

# leaves font/size/color alone (only the one property is applied)
c = make()
cur = c.textCursor()
cur.setPosition(0)
cur.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
from PyQt6.QtGui import QTextCharFormat

mark = QTextCharFormat()
mark.setForeground(QColor("#ff0000"))
f = QFont(family)
f.setPointSize(50)
mark.setFont(f)
cur.mergeCharFormat(mark)
select_block(c, 0)
c.set_word_spacing(12)
probe = QTextCursor(c.document())
probe.setPosition(3)
pf = probe.charFormat()
check(
    "word spacing keeps the selection's own color",
    pf.foreground().color().name() == "#ff0000",
    pf.foreground().color().name(),
)
check(
    "word spacing keeps the selection's own size",
    pf.font().pointSize() == 50,
    str(pf.font().pointSize()),
)
probe2 = QTextCursor(c.document())
probe2.setPosition(len(LINE_A) - 1)
check(
    "...and the rest keeps the default size",
    probe2.charFormat().font().pointSize() == 36,
)

# ================================================================== undo merging
c = make()
select_block(c, 0)
for v in (5, 10, 15, 20, 25):
    c.set_word_spacing(v)
check("five adjustments applied", ws_of(c, 3) == 25.0)
c.document().undo()
check(
    "ONE undo reverts the whole run of adjustments",
    ws_of(c, 3) == 0.0 and abs(block_width(c, 0) - base_a) < 0.01,
    f"ws={ws_of(c, 3)}",
)
c.document().redo()
check("redo brings the final value back", ws_of(c, 3) == 25.0)

# an unrelated edit in between must NOT be swallowed into the spacing undo step
c = make()
select_block(c, 0)
c.set_word_spacing(10)
c.set_word_spacing(20)
end = QTextCursor(c.document())
end.movePosition(QTextCursor.MoveOperation.End)
c.setTextCursor(end)
c.textCursor().insertText("X")
select_block(c, 0)
c.set_word_spacing(30)
c.document().undo()
check(
    "undo after a later spacing change only reverts that change (back to 20)",
    ws_of(c, 3) == 20.0,
    str(ws_of(c, 3)),
)
c.document().undo()  # the typed X
check(
    "next undo removes the typed text, not spacing",
    "X" not in c.toPlainText() and ws_of(c, 3) == 20.0,
    f"{c.toPlainText()[-4:]!r}, ws={ws_of(c, 3)}",
)
c.document().undo()
check("next undo reverts the earlier spacing run", ws_of(c, 3) == 0.0, str(ws_of(c, 3)))

# a different target starts a new undo step
c = make()
select_block(c, 0)
c.set_word_spacing(10)
select_block(c, 1)
c.set_word_spacing(10)
c.document().undo()
check(
    "changing a different paragraph is its own undo step",
    ws_of(c, 3) == 10.0
    and ws_of(c, c.document().findBlockByNumber(1).position() + 3) == 0.0,
)

# a no-op change must not glue itself onto an earlier, unrelated undo step
c = make()
c.textCursor().insertText("!")  # an unrelated edit: top of the undo stack
select_block(c, 0)
c.set_word_spacing(0)  # no actual change
c.set_word_spacing(15)  # real change
c.document().undo()
check(
    "a no-op adjustment doesn't merge later changes into an unrelated undo step",
    ws_of(c, 3) == 0.0 and "!" in c.toPlainText(),
    f"ws={ws_of(c, 3)}, text ends {c.toPlainText()[-3:]!r}",
)

# ============================================================ typing keeps spacing
c = make()
put_cursor(c, len(LINE_A))  # end of paragraph 1
c.set_word_spacing(25)
QTest.keyClicks(
    c, " abc"
)  # QTest can only type ASCII; the property doesn't depend on script
end_a = c.document().findBlockByNumber(0)
new_ws = ws_of(c, end_a.position() + len(end_a.text()) - 2)
check(
    "text typed after changing spacing gets the same spacing",
    new_ws == 25.0,
    str(new_ws),
)

# spacing set on an empty page applies to what's typed next
c = make("")
c.set_word_spacing(18)
QTest.keyClicks(c, "ab cd")
check(
    "spacing chosen before typing applies to the typed text",
    ws_of(c, 3) == 18.0,
    str(ws_of(c, 3)),
)

# pressing Enter keeps the spacing on the next paragraph
QTest.keyClick(c, Qt.Key.Key_Return)
QTest.keyClicks(c, "ef gh")
b1 = c.document().findBlockByNumber(1)
check(
    "a new paragraph typed after Enter keeps the spacing",
    ws_of(c, b1.position() + 3) == 18.0,
    str(ws_of(c, b1.position() + 3)),
)

# ===================================================== value follows the cursor
c = make()
select_block(c, 0)
c.set_word_spacing(40)
put_cursor(c, c.document().findBlockByNumber(1).position() + 3)
check(
    "moving into unspaced text shows 0",
    c.current_word_spacing() == 0.0,
    str(c.current_word_spacing()),
)
put_cursor(c, 5)
check(
    "moving back into spaced text shows 40",
    c.current_word_spacing() == 40.0,
    str(c.current_word_spacing()),
)
select_block(c, 0)
check("selecting spaced text shows 40", c.current_word_spacing() == 40.0)
# right-to-left selection: cursor sits at the START of the selection
cur = c.textCursor()
b0 = c.document().findBlockByNumber(0)
cur.setPosition(b0.position() + len(b0.text()))
cur.setPosition(b0.position(), QTextCursor.MoveMode.KeepAnchor)
c.setTextCursor(cur)
check(
    "a backwards selection shows the selected text's spacing",
    c.current_word_spacing() == 40.0,
    str(c.current_word_spacing()),
)

# ...and re-stamping other formatting must not flatten a selection's spacing
c = make()
select_block(c, 0)
c.set_word_spacing(40)
select_block(c, 1)
c.set_word_spacing(0)
select_block(c, 0)  # back to the spaced paragraph
c.set_pending_color(QColor("#00aa00"))  # any other format re-stamps the pending format
check(
    "changing color on spaced text keeps its word spacing",
    ws_of(c, 3) == 40.0,
    str(ws_of(c, 3)),
)

# clear formatting resets it
c.clear_formatting()
check(
    "Clear Formatting resets word spacing",
    ws_of(c, 3) == 0.0 and c.pending_word_spacing() == 0.0,
)

# ============================================================== line height
c = make()
h0 = c.document().size().height()
select_block(c, 0)
c.apply_line_spacing(200)
check(
    "line height applies immediately to the selected paragraph",
    c.document().size().height() > h0,
    f"{c.document().size().height()} vs {h0}",
)
check("current_line_spacing reports it", c.current_line_spacing() == 200)
put_cursor(c, c.document().findBlockByNumber(1).position() + 2)
check("other paragraph still 100", c.current_line_spacing() == 100)

c = make()
put_cursor(c, 4)
c.apply_line_spacing(150)
check(
    "no selection: applies to the cursor's paragraph", c.current_line_spacing() == 150
)
b1 = c.document().findBlockByNumber(1)
check("...and not to the next one", b1.blockFormat().lineHeight() in (0.0, 100.0))

c.apply_line_spacing(10)
check(
    "line height is clamped to the minimum",
    c.current_line_spacing() == PageCanvas.LINE_HEIGHT_RANGE[0],
)
c.apply_line_spacing(9000)
check(
    "line height is clamped to the maximum",
    c.current_line_spacing() == PageCanvas.LINE_HEIGHT_RANGE[1],
)

c = make()
select_block(c, 0)
for v in (110, 120, 130, 140):
    c.apply_line_spacing(v)
c.document().undo()
check(
    "one undo reverts a run of line-height adjustments",
    c.current_line_spacing() == 100,
    str(c.current_line_spacing()),
)

c = make()
c.selectAll()
c.apply_line_spacing(180)
check(
    "select-all applies to every paragraph",
    all(
        round(c.document().findBlockByNumber(i).blockFormat().lineHeight()) == 180
        for i in range(2)
    ),
)

print("\nALL SPACING CHECKS PASSED" if all(results) else "\nSOME CHECKS FAILED")
sys.exit(0 if all(results) else 1)
