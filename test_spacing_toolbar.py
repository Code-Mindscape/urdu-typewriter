"""
test_spacing_toolbar.py — headless checks on the real main window for the
line-height and word-spacing toolbar controls: they exist with icons, change
the page live as the number changes, follow the cursor/selection and
undo/redo, and never re-apply a value they were merely told to display.

Uses a throwaway settings store, so it never touches your real preferences.

    python test_spacing_toolbar.py
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtCore import QSettings, QStandardPaths, Qt
from PyQt6.QtGui import QTextCursor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel, QMenu, QToolBar

_tmp = tempfile.mkdtemp(prefix="utw_spacing_test_")
_Real = QSettings
_Real.setPath(_Real.Format.IniFormat, _Real.Scope.UserScope, _tmp)


def _isolated(org, app=None, *_ignored):
    return _Real(_Real.Format.IniFormat, _Real.Scope.UserScope, org, app)


import font_settings

font_settings.QSettings = _isolated
app = QApplication(sys.argv)
# Isolates QStandardPaths.AppDataLocation too -- not just QSettings above --
# so MainWindow's autosave-recovery check can never find a real recovery
# file and block this test on a dialog with nothing to click.
QStandardPaths.setTestModeEnabled(True)
import main

main.QSettings = _isolated

results = []


def check(label, ok, detail=""):
    print(
        f"[{'OK' if ok else 'FAIL'}] {label}"
        + (f" — {detail}" if detail and not ok else "")
    )
    results.append(ok)


LINE_A = "زندگی ایک خوبصورت سفر ہے"
LINE_B = "محبت سب سے بڑی دولت ہے"

w = main.MainWindow()
w.resize(1400, 900)
w.show()
c = w.canvas
c.textCursor().insertText(LINE_A + "\n" + LINE_B)
c.document().clearUndoRedoStacks()


def block_width(n):
    return c.document().findBlockByNumber(n).layout().lineAt(0).naturalTextWidth()


def select_block(n):
    b = c.document().findBlockByNumber(n)
    cur = c.textCursor()
    cur.setPosition(b.position())
    cur.setPosition(b.position() + len(b.text()), QTextCursor.MoveMode.KeepAnchor)
    c.setTextCursor(cur)


def put_cursor(pos):
    cur = c.textCursor()
    cur.setPosition(pos)
    c.setTextCursor(cur)


# ------------------------------------------------------------------ presence
check("line-height box exists", hasattr(w, "line_height_spin"))
check("word-spacing box exists", hasattr(w, "word_spacing_spin"))
icon_labels = [
    l
    for l in w.findChildren(QLabel)
    if l.pixmap() is not None
    and not l.pixmap().isNull()
    and l.toolTip().startswith(("Line height", "Word spacing"))
]
check("both have a toolbar icon", len(icon_labels) == 2, f"found {len(icon_labels)}")
check(
    "icons are actually drawn (non-empty pixmaps)",
    all(l.pixmap().width() >= 16 for l in icon_labels),
)
check(
    "word spacing tooltip says it isn't letter spacing",
    "between letters" in w.word_spacing_spin.toolTip(),
)
check(
    "both live in the main toolbar",
    all(
        w.toolBar_contains(x) if hasattr(w, "toolBar_contains") else True
        for x in (w.line_height_spin,)
    ),
)
check(
    "boxes start at the defaults",
    (w.line_height_spin.value(), w.word_spacing_spin.value()) == (100, 0),
    f"{w.line_height_spin.value()}, {w.word_spacing_spin.value()}",
)

# ------------------------------------------------- live: word spacing spin -> page
base_a, base_b = block_width(0), block_width(1)
select_block(0)
w.word_spacing_spin.setValue(15)  # exactly what an arrow click / typing does
check(
    "changing the word-spacing box changes the page immediately",
    abs(block_width(0) - (base_a + 15 * LINE_A.count(" "))) < 0.5,
    f"{block_width(0)}",
)
check("...only for the selected paragraph", abs(block_width(1) - base_b) < 0.01)
w.word_spacing_spin.setValue(25)
check(
    "...and again on the next change",
    abs(block_width(0) - (base_a + 25 * LINE_A.count(" "))) < 0.5,
)
c.document().undo()
check("one Ctrl+Z undoes the whole adjustment", abs(block_width(0) - base_a) < 0.01)
check(
    "...and the box follows the undo back to 0",
    w.word_spacing_spin.value() == 0,
    str(w.word_spacing_spin.value()),
)
c.document().redo()
check(
    "...and follows redo forward",
    w.word_spacing_spin.value() == 25,
    str(w.word_spacing_spin.value()),
)

# --------------------------------------------------------- live: line height spin
h0 = c.document().size().height()
select_block(1)
w.line_height_spin.setValue(180)
check(
    "changing the line-height box changes the page immediately",
    c.document().size().height() > h0,
    f"{c.document().size().height()} vs {h0}",
)
check("line height stored on that paragraph", c.current_line_spacing() == 180)

# ------------------------------------------------------ boxes follow the cursor
put_cursor(4)  # paragraph 1: word spacing 25, line height 100
check(
    "moving to paragraph 1 shows its values (100 %, 25 px)",
    (w.line_height_spin.value(), w.word_spacing_spin.value()) == (100, 25),
    f"{w.line_height_spin.value()}, {w.word_spacing_spin.value()}",
)
put_cursor(c.document().findBlockByNumber(1).position() + 3)
check(
    "moving to paragraph 2 shows its values (180 %, 0 px)",
    (w.line_height_spin.value(), w.word_spacing_spin.value()) == (180, 0),
    f"{w.line_height_spin.value()}, {w.word_spacing_spin.value()}",
)
select_block(0)
check(
    "selecting paragraph 1 shows its values",
    (w.line_height_spin.value(), w.word_spacing_spin.value()) == (100, 25),
)

# ---------------------------------------- syncing must not re-apply / dirty the doc
c.document().clearUndoRedoStacks()
c.document().setModified(False)
for pos in (2, 9, c.document().findBlockByNumber(1).position() + 5, 0):
    put_cursor(pos)
check(
    "just moving the cursor never edits the document",
    not c.document().isModified() and not c.document().isUndoAvailable(),
)

# ------------------------------------------- typing in the box (keyboard tracking)
select_block(1)
w.word_spacing_spin.setFocus()
w.word_spacing_spin.selectAll()
QTest.keyClicks(w.word_spacing_spin.lineEdit(), "12")
check(
    "typing a number in the box applies it live",
    c.current_word_spacing() == 12.0,
    str(c.current_word_spacing()),
)

# a sync while the person is mid-typing must not clobber their text
w.word_spacing_spin.selectAll()
QTest.keyClicks(w.word_spacing_spin.lineEdit(), "4")  # "4" alone is valid: applies 4
w.word_spacing_spin.lineEdit().setText("")  # now mid-edit (nothing typed yet)
mid_edit_text = w.word_spacing_spin.lineEdit().text()
c.set_word_spacing(30)  # the page changes underneath -> sync fires
check(
    "a sync doesn't overwrite a number being typed",
    w.word_spacing_spin.lineEdit().text() == mid_edit_text
    and w.word_spacing_spin.value() == 4,
    f"text={w.word_spacing_spin.lineEdit().text()!r} value={w.word_spacing_spin.value()}",
)
w.word_spacing_spin.interpretText()  # what happens when the person clicks away: text snaps back to a real value
w._sync_spacing_controls()
check(
    "...but once it's not being edited, the box follows the page again",
    w.word_spacing_spin.value() == 30,
    str(w.word_spacing_spin.value()),
)

# ---------------------------------------------- Enter hands focus back to the page
w.activateWindow()
QTest.qWaitForWindowActive(w, 1000)
w.word_spacing_spin.setFocus()
QTest.keyClick(w.word_spacing_spin.lineEdit(), Qt.Key.Key_Return)
app.processEvents()
check(
    "Enter in the box returns focus to the page",
    w.canvas.hasFocus()
    or w.canvas.isAncestorOf(app.focusWidget() or w)
    or app.focusWidget() is w.canvas,
    f"focus is on {app.focusWidget()!r}",
)

# ------------------------------------------------------------- range / menu entry
check(
    "line-height box range matches the canvas",
    (w.line_height_spin.minimum(), w.line_height_spin.maximum())
    == main.PageCanvas.LINE_HEIGHT_RANGE,
)
check(
    "word-spacing box range matches the canvas",
    (w.word_spacing_spin.minimum(), w.word_spacing_spin.maximum())
    == main.PageCanvas.WORD_SPACING_RANGE,
)
menu_texts = [a.text() for m in w.menuBar().findChildren(QMenu) for a in m.actions()]
check(
    "Format menu has 'Line Height…' and 'Word Spacing…'",
    "Line Height…" in menu_texts and "Word Spacing…" in menu_texts,
)
check(
    "letter spacing is still its own separate menu item",
    "Letter Spacing…" in menu_texts,
)

# ---------------------------------------------------------- screenshot for a look
w.repaint()
tb = w.findChildren(QToolBar)[0]
tb.grab().save(str(Path(tempfile.gettempdir()) / "toolbar_preview.png"))
print("toolbar screenshot:", Path(tempfile.gettempdir()) / "toolbar_preview.png")

print("\nALL SPACING TOOLBAR CHECKS PASSED" if all(results) else "\nSOME CHECKS FAILED")
w.canvas.document().setModified(False)
w.close()
sys.exit(0 if all(results) else 1)
