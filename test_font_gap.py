"""
test_font_gap.py — headless checks for the EXPERIMENTAL gap between words
written in different fonts (font_gap.py):

  planning     which space gets a gap (and which don't): only the last space
               between two words whose fonts differ; never inside a word; one
               font, or size/style-only changes, get none
  effect       the following word really moves by em x font size, the words
               before it don't, and Off puts everything back exactly
  typing       it appears the moment you type the second word -- no event loop,
               no flash of the un-gapped layout
  clean        the document itself is untouched: same text, same character
               formats, same revision, same undo history, modified flag
               untouched, nothing written to saved project files
  always right a randomised session of inserts / deletes / font changes /
               undo / redo never leaves the layout out of step with the text
  WYSIWYG      screen, SVG, PDF, EPS, PNG and print all see the same gaps
  safe         an internal error switches the feature off once, restores the
               page, and never raises into Qt; layout formats owned by someone
               else (e.g. an input method) are left alone
  UI           Tools > Gap Between Fonts: exclusive Off/Subtle/Normal/Wide,
               remembered between runs

    python test_font_gap.py
"""

import contextlib
import faulthandler
import io
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path

faulthandler.dump_traceback_later(240, exit=True)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtCore import QEvent, QSettings, QStandardPaths, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QKeyEvent,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextLayout,
)
from PyQt6.QtWidgets import QApplication, QMenu

_tmp = tempfile.mkdtemp(prefix="utw_fontgap_test_")
_Real = QSettings
_Real.setPath(_Real.Format.IniFormat, _Real.Scope.UserScope, _tmp)


def _isolated_settings(org, app=None, *_ignored):
    return _Real(_Real.Format.IniFormat, _Real.Scope.UserScope, org, app)


import font_settings  # noqa: E402

font_settings.QSettings = _isolated_settings

app = QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)

import main  # noqa: E402
import exporter  # noqa: E402
import font_gap  # noqa: E402
import project_io  # noqa: E402
import text_outlines  # noqa: E402
from canvas import PageCanvas  # noqa: E402

main.QSettings = _isolated_settings
main.load_bundled_fonts()

failures = []


def check(label, ok, detail=""):
    print(
        f"[{'OK' if ok else 'FAIL'}] {label}"
        + (f" — {detail}" if detail and not ok else "")
    )
    if not ok:
        failures.append(label)


NAST = "Noto Nastaliq Urdu"
SANS = "DejaVu Sans"
W1, W2, W3 = "کتاب", "قلم", "دوات"  # three Urdu words
PX36 = 36 * 96 / 72  # 48 px


def type_text(widget, text):
    """Real key events, one per character, carrying Unicode text -- the way
    typing arrives (QTest.keyClicks can't send non-Latin text)."""
    for ch in text:
        key = Qt.Key.Key_Space if ch == " " else Qt.Key.Key_unknown
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(
                widget, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, ch)
            )


def fmt(family, pt=36, style=None):
    f = QTextCharFormat()
    font = QFont(family)
    font.setPointSize(pt)
    if style:
        font.setStyleName(style)
    f.setFont(font)
    f.setForeground(QColor("black"))
    return f


def rtl_doc():
    from PyQt6.QtGui import QTextOption

    doc = QTextDocument()
    opt = QTextOption()
    opt.setTextDirection(Qt.LayoutDirection.RightToLeft)
    doc.setDefaultTextOption(opt)
    doc.setTextWidth(794)
    return doc


def line(*parts):
    """A one-paragraph document from (text, family[, pt]) parts."""
    doc = rtl_doc()
    c = QTextCursor(doc)
    for p in parts:
        c.insertText(p[0], fmt(p[1], p[2] if len(p) > 2 else 36))
    return doc


def gaps(doc, em=0.07):
    out = []
    b = doc.begin()
    while b.isValid():
        out.append(font_gap.plan_block(b, em))
        b = b.next()
    return out


def positions(doc):
    """{stripped run text: (left, right)} of every outlined run."""
    return {
        g["text"].strip(): (g["bbox"].left(), g["bbox"].right())
        for g in text_outlines.extract_groups(doc, "run")
    }


def sep(pos):
    """How far apart the two words are: first word's left edge minus second
    word's right edge (it's a right-to-left line, so the first word is on the
    right). Which word physically moves when the gap grows depends on where
    the paragraph is anchored (alignment), but this separation does not."""
    return pos[W1][0] - pos[W2][1]


def width_of(pos, word):
    return pos[word][1] - pos[word][0]


def block_formats(doc):
    """Everything about every character format in the document, to prove the
    feature never touches them: (position, length, family, pt, letter-spacing
    type+value, word-spacing, colour) per fragment."""
    out = []
    b = doc.begin()
    while b.isValid():
        it = b.begin()
        while not it.atEnd():
            fr = it.fragment()
            f = fr.charFormat()
            out.append(
                (
                    fr.position(),
                    fr.length(),
                    f.font().family(),
                    f.font().pointSizeF(),
                    int(f.fontLetterSpacingType().value),
                    f.fontLetterSpacing(),
                    f.fontWordSpacing(),
                    f.foreground().color().name(),
                )
            )
            it += 1
        b = b.next()
    return out


def in_sync(doc, em):
    """True if no block's layout needs changing to match its text."""
    ok = True
    b = doc.begin()
    while b.isValid():
        if font_gap.apply_block(b, em):
            ok = False
        b = b.next()
    return ok


def n_gap_ranges(doc):
    n = 0
    b = doc.begin()
    while b.isValid():
        n += sum(
            1 for r in b.layout().formats() if r.format.hasProperty(font_gap._MARK)
        )
        b = b.next()
    return n


# ============================================================== planning ===
check(
    "level table: off=0 and the three sizes ascend",
    font_gap.LEVELS["off"]
    == 0
    < font_gap.LEVELS["subtle"]
    < font_gap.LEVELS["normal"]
    < font_gap.LEVELS["wide"],
)
check("default level is 'normal'", font_gap.DEFAULT_LEVEL == "normal")

d = line((W1 + " ", NAST), (W2, SANS))
p = gaps(d)[0]
check("two words in different fonts: exactly one gap", len(p) == 1, str(p))
check(
    "...on the space between them (index 4, after the 4-letter word)",
    p and p[0][0] == 4,
    str(p),
)
check(
    "...sized em x the font size in px (0.07 x 48)",
    p and abs(p[0][1] - 0.07 * PX36) < 1e-6,
    str(p),
)

d = line((W1 + " " + W2, NAST))
check("one font: no gap", gaps(d)[0] == [])

d = line((W1 + " ", NAST), (W2 + " ", SANS), (W3, NAST))
check(
    "fonts A, B, A: a gap at each of the two joins (spaces at 4 and 8)",
    [g[0] for g in gaps(d)[0]] == [4, 8],
    str(gaps(d)),
)

d = line((W1 + "   ", NAST), (W2, SANS))
check(
    "several spaces between: the gap goes on the LAST one (index 6)",
    [g[0] for g in gaps(d)[0]] == [6],
    str(gaps(d)),
)

d = line((W1 + "\t", NAST), (W2, SANS))
check("only a tab between: nothing to carry the gap, so none", gaps(d)[0] == [])

d = line((W1[:2], NAST), (W1[2:] + " " + W2, SANS))
check(
    "a font change in the MIDDLE of a word: no gap (it must never split a word)",
    gaps(d)[0] == [],
    str(gaps(d)),
)

d = line((W1 + " ", NAST, 36), (W2, NAST, 20))
check("same family, different size: no gap", gaps(d)[0] == [])

d = line((W1 + " ", NAST, 36), (W2, SANS, 20))
check(
    "different sizes + families: the larger font sets the gap (48 px, not 26.7)",
    abs(gaps(d)[0][0][1] - 0.07 * PX36) < 1e-6,
    str(gaps(d)),
)

d = line((" " + W1 + " ", NAST), (W2 + " ", SANS))
check(
    "leading/trailing spaces don't make extra gaps",
    [g[0] for g in gaps(d)[0]] == [5],
    str(gaps(d)),
)

d = line((W1 + " ", NAST), (W2, SANS))
c = QTextCursor(d)
c.movePosition(QTextCursor.MoveOperation.End)
c.insertBlock()
c.insertText(W2 + " ", fmt(SANS))
c.insertText(W3, fmt(NAST))
g = gaps(d)
check(
    "each paragraph is planned on its own",
    len(g) == 2 and len(g[0]) == 1 and len(g[1]) == 1,
    str(g),
)
check("no gaps at all when the level is off", all(not x for x in gaps(d, 0.0)))

d = line(("a", NAST), (" ", NAST), ("b", SANS))
check(
    "single-letter words still get their gap",
    [x[0] for x in gaps(d)[0]] == [1],
    str(gaps(d)),
)


# ================================================================ effect ===
def two_font_canvas(level="normal"):
    c = PageCanvas(794, 1123, QColor("white"), 40, default_family=NAST, default_size=36)
    mgr = font_gap.FontGapManager(c.document(), level, parent=c)
    c.show()
    app.processEvents()
    return c, mgr


c, mgr = two_font_canvas("off")
type_text(c, W1 + " ")
c.set_pending_family(SANS)
type_text(c, W2)
app.processEvents()
doc = c.document()
base = positions(doc)
check("two runs found on the page", set(base) == {W1, W2}, str(list(base)))
check("level off: no gap ranges anywhere", n_gap_ranges(doc) == 0)

rev0, undo0, mod0, fmts0 = (
    doc.revision(),
    doc.availableUndoSteps(),
    doc.isModified(),
    block_formats(doc),
)
for level, em in (("subtle", 0.04), ("normal", 0.07), ("wide", 0.12), ("off", 0.0)):
    mgr.set_level(level)
    app.processEvents()
    now = positions(doc)
    grew = sep(now) - sep(base)
    want = em * PX36
    check(
        f"{level}: the two words end up em x 48 px further apart ({want:.2f})",
        abs(grew - want) < 0.1,
        f"{grew:.3f}",
    )
    # (0.05 px tolerance: Qt lays text out in 1/64-pixel steps, so a word's measured
    # width can wobble by one step when the line around it shifts.)
    check(
        f"{level}: neither word changes shape (each keeps its own width)",
        all(abs(width_of(now, w) - width_of(base, w)) < 0.05 for w in (W1, W2)),
    )
    check(
        f"{level}: exactly {0 if em == 0 else 1} gap range(s) in the layout",
        n_gap_ranges(doc) == (0 if em == 0 else 1),
    )
check(
    "back at Off, every position is exactly as the baseline",
    all(abs(positions(doc)[k][i] - base[k][i]) < 0.01 for k in base for i in (0, 1)),
)

# ================================================== the document is clean ===
mgr.set_level("normal")
app.processEvents()
check(
    "document revision unchanged by turning the feature on/off",
    doc.revision() == rev0,
    f"{doc.revision()} vs {rev0}",
)
check("undo history unchanged (no extra steps)", doc.availableUndoSteps() == undo0)
check("modified flag unchanged", doc.isModified() == mod0)
check(
    "every character format identical (letter/word spacing included)",
    block_formats(doc) == fmts0,
)
check("plain text identical", doc.toPlainText() == W1 + " " + W2)

# ===================================================== typing: immediate ===
c, mgr = two_font_canvas("normal")
type_text(c, W1 + " ")
check(
    "typing the first word + space: no gap yet (one font so far)",
    n_gap_ranges(c.document()) == 0,
)
c.set_pending_family(SANS)
type_text(c, W2[0])
# deliberately NO processEvents: the gap must already be in the layout the
# moment the character exists, before Qt lays the line out
check(
    "the instant the second word's first letter is typed, the gap exists",
    n_gap_ranges(c.document()) == 1,
)
type_text(c, W2[1:])
check("finishing the word keeps exactly one gap", n_gap_ranges(c.document()) == 1)
check("...and the layout is in sync with the text", in_sync(c.document(), 0.07))

# delete the space: words touch, so no join remains
cur = c.textCursor()
cur.setPosition(4)
cur.setPosition(5, QTextCursor.MoveMode.KeepAnchor)
cur.removeSelectedText()
check(
    "delete the space between them: the gap goes away", n_gap_ranges(c.document()) == 0
)
c.document().undo()
check(
    "undo that deletion: the gap comes back by itself", n_gap_ranges(c.document()) == 1
)
c.document().redo()
check("redo it: gone again", n_gap_ranges(c.document()) == 0)
c.document().undo()

# put the second word in the first word's font -> no join
before_change = block_formats(c.document())
cur = c.textCursor()
cur.select(QTextCursor.SelectionType.Document)
c.setTextCursor(cur)
c.set_pending_family(NAST)
check("make everything one font: no gap", n_gap_ranges(c.document()) == 0)
# (the app applies a font change as two document edits -- merge, then set the
# pending format -- so it can take more than one undo to get back; what must
# hold is that the gap is back exactly when the formats are)
n = 0
while (
    block_formats(c.document()) != before_change
    and c.document().isUndoAvailable()
    and n < 6
):
    c.document().undo()
    n += 1
check(
    "undo the font change until the fonts are back: the gap is back with them",
    block_formats(c.document()) == before_change and n_gap_ranges(c.document()) == 1,
    f"{n} undos",
)

# Roman-Urdu / dictation style insertion through the app's own paths
c2, m2 = two_font_canvas("normal")
c2.insert_dictated_text(W1 + " ")
c2.set_pending_family(SANS)
c2.insert_dictated_text(W2)
check(
    "text inserted by voice dictation gets its gap too",
    n_gap_ranges(c2.document()) == 1,
)

# ============================================================ undo clean ===
c, mgr = two_font_canvas("normal")
doc = c.document()
type_text(c, W1 + " ")
c.set_pending_family(SANS)
type_text(c, W2)
steps_after_typing = doc.availableUndoSteps()
n = 0
while doc.isUndoAvailable() and n < 50:
    doc.undo()
    n += 1
check("undoing everything empties the page", doc.toPlainText() == "")
check(
    "...with no extra 'gap-only' undo steps in between (steps == undo calls)",
    n == steps_after_typing,
    f"{n} undos vs {steps_after_typing} steps",
)
check("...and no gap left behind", n_gap_ranges(doc) == 0)
n = 0
while doc.isRedoAvailable() and n < 50:
    doc.redo()
    n += 1
check("redo everything restores the text", doc.toPlainText() == W1 + " " + W2)
check("...and the gap with it", n_gap_ranges(doc) == 1)


# =========================================================== saved files ===
def project_json(level):
    c, m = two_font_canvas(level)
    type_text(c, W1 + " ")
    c.set_pending_family(SANS)
    type_text(c, W2)
    path = os.path.join(_tmp, f"p_{level}.utw")
    project_io.save_project(path, c)
    return path, c


p_off, _ = project_json("off")
p_on, c_on = project_json("wide")
a = json.load(open(p_off, encoding="utf-8"))
b = json.load(open(p_on, encoding="utf-8"))
check("saved project is byte-for-byte what it would be with the feature Off", a == b)
check(
    "no gap/letter-spacing trace anywhere in the saved text",
    "AbsoluteSpacing" not in open(p_on, encoding="utf-8").read(),
)

c3, m3 = two_font_canvas("normal")
project_io.apply_loaded_project(project_io.load_project(p_on), c3)
app.processEvents()
check("a loaded project shows the gap on the page", n_gap_ranges(c3.document()) == 1)
check("...loading leaves nothing to undo", not c3.document().isUndoAvailable())

# ====================================================== always in sync ===
rng = random.Random(20241004)
c, mgr = two_font_canvas("normal")
doc = c.document()
WORDS = [W1, W2, W3, "ہے", "اور", "سفر", "hello", "x"]
FAMS = [NAST, SANS, "Noto Sans CJK JP"]
errors = []
sync_ok = True
text_ok = True
steps = 0
buf = io.StringIO()
with contextlib.redirect_stderr(buf):
    for step in range(450):
        op = rng.choice(
            [
                "insert",
                "insert",
                "insert",
                "delete",
                "fmt",
                "undo",
                "redo",
                "block",
                "spaces",
            ]
        )
        cur = QTextCursor(doc)
        total = max(doc.characterCount() - 1, 0)
        pos = rng.randint(0, total)
        if op == "insert":
            cur.setPosition(pos)
            cur.insertText(
                rng.choice(WORDS) + rng.choice(["", " ", " ", "  "]),
                fmt(rng.choice(FAMS), rng.choice([24, 36, 36])),
            )
        elif op == "spaces":
            cur.setPosition(pos)
            cur.insertText(" " * rng.randint(1, 3), fmt(rng.choice(FAMS)))
        elif op == "delete" and total > 0:
            a = rng.randint(0, total)
            cur.setPosition(a)
            cur.setPosition(
                min(total, a + rng.randint(1, 8)), QTextCursor.MoveMode.KeepAnchor
            )
            cur.removeSelectedText()
        elif op == "fmt" and total > 0:
            a = rng.randint(0, total)
            cur.setPosition(a)
            cur.setPosition(
                min(total, a + rng.randint(1, 12)), QTextCursor.MoveMode.KeepAnchor
            )
            f = QTextCharFormat()
            font = QFont(rng.choice(FAMS))
            f.setFont(font)
            cur.mergeCharFormat(f)
        elif op == "undo" and doc.isUndoAvailable():
            doc.undo()
        elif op == "redo" and doc.isRedoAvailable():
            doc.redo()
        elif op == "block":
            cur.setPosition(pos)
            cur.insertBlock()
        if mgr._broken:
            errors.append(f"manager broke at step {step} ({op})")
            break
        if not in_sync(doc, 0.07):
            sync_ok = False
            errors.append(f"out of sync after step {step} ({op})")
            break
        steps += 1
check(
    "450-step random session (insert/delete/font change/undo/redo/new paragraph): manager never broke",
    not mgr._broken and not any("broke" in e for e in errors),
    "; ".join(errors),
)
check(
    "...and after EVERY step the layout matched the text (never stale, never missing)",
    sync_ok,
    "; ".join(errors),
)
check("...ran all the steps", steps == 450, str(steps))
total_seen = 0
rng2 = random.Random(7)
c_v, m_v = two_font_canvas("normal")
for _ in range(60):
    cur = QTextCursor(c_v.document())
    cur.movePosition(QTextCursor.MoveOperation.End)
    cur.insertText(rng2.choice(WORDS) + " ", fmt(rng2.choice(FAMS)))
    total_seen = max(total_seen, n_gap_ranges(c_v.document()))
check(
    "(sanity) a plain sequence of mixed-font words does accumulate many gaps",
    total_seen > 10,
    str(total_seen),
)

# ================================================== WYSIWYG / exports ===
c, mgr = two_font_canvas("normal")
type_text(c, W1 + " ")
c.set_pending_family(SANS)
type_text(c, W2)
app.processEvents()
doc = c.document()
doc.setTextWidth(794)
live = positions(doc)
clone_on = positions(exporter._layout_copy(doc, 794))
check(
    "the export/print clone shows the same gap the screen does",
    all(abs(live[k][i] - clone_on[k][i]) < 0.05 for k in live for i in (0, 1)),
    f"{live} vs {clone_on}",
)
raw_clone = doc.clone()
check(
    "(a plain clone has no layout formats -- which is why _layout_copy re-applies them)",
    n_gap_ranges(raw_clone) == 0,
)

mgr.set_level("off")
clone_off = positions(exporter._layout_copy(doc, 794))
check(
    "with the feature Off, exports have no gap either (words 0.07 x 48 px closer than with it on)",
    abs((sep(clone_on) - sep(clone_off)) - 0.07 * PX36) < 0.1,
    f"{sep(clone_on)} vs {sep(clone_off)}",
)
mgr.set_level("normal")

from PyQt6.QtGui import QImage  # noqa: E402


def _without_timestamp(eps_bytes):
    return b"\n".join(
        l for l in eps_bytes.split(b"\n") if not l.startswith(b"%%CreationDate")
    )


def outputs():
    d = doc.clone()
    img = exporter._render_raster_image(
        d, 794, 1123, 794, 1123, QColor("white"), None, "fit", 0, None
    )
    return {
        "SVG": exporter.render_svg_bytes(d, 794, 1123, QColor("white")),
        "PDF (curves)": exporter.render_pdf_curves_bytes(d, 794),
        "SVG (curves)": exporter.render_svg_curves_bytes(d, 794),
        "EPS": _without_timestamp(
            exporter.render_eps_bytes(d, 794, 1123, QColor("white"))
        ),
        "PNG pixels": bytes(img.constBits().asarray(img.sizeInBytes())),
    }


font_gap.set_active_level("normal")
with_gap = outputs()
mgr.set_level("off")
without = outputs()
mgr.set_level("normal")
for name in with_gap:
    check(
        f"{name} output differs with the gap vs without it (the exporter really applies it)",
        with_gap[name] != without[name],
    )

# =================================================== safe / containment ===
c, mgr = two_font_canvas("normal")
type_text(c, W1 + " ")
c.set_pending_family(SANS)
type_text(c, W2)
failed_msgs = []
mgr.failed.connect(failed_msgs.append)
real_plan = font_gap.plan_block


def boom(*a, **k):
    raise RuntimeError("simulated failure")


font_gap.plan_block = boom
buf = io.StringIO()
raised = None
try:
    with contextlib.redirect_stderr(buf):
        type_text(c, "x")
        type_text(c, "y")
except Exception as exc:  # pragma: no cover
    raised = exc
finally:
    font_gap.plan_block = real_plan
check(
    "an internal error never propagates out of the signal handler",
    raised is None,
    str(raised),
)
check(
    "...it reports once (not once per keystroke)",
    len(failed_msgs) == 1,
    str(failed_msgs),
)
check("...the traceback went to stderr", "simulated failure" in buf.getvalue())
check(
    "...the feature switched itself off for exports too",
    font_gap.active_level() == "off",
)
check(
    "...and the page was restored to look exactly as without the feature",
    n_gap_ranges(c.document()) == 0,
)
check(
    "...typing carries on normally afterwards",
    (type_text(c, "z") or True) and c.toPlainText().endswith("xyz"),
)
mgr.set_level("normal")
check(
    "choosing a level again re-enables it",
    not mgr._broken
    and font_gap.active_level() == "normal"
    and n_gap_ranges(c.document()) == 1,
)

# layout formats somebody else owns are left alone
c, mgr = two_font_canvas("normal")
type_text(c, W1 + " ")
c.set_pending_family(SANS)
type_text(c, W2)
blk = c.document().firstBlock()
foreign = QTextLayout.FormatRange()
foreign.start, foreign.length = 0, 2
ff = QTextCharFormat()
ff.setFontUnderline(True)
foreign.format = ff
blk.layout().setFormats(list(blk.layout().formats()) + [foreign])
type_text(c, "ab")  # an edit re-applies ours
kept = [r for r in blk.layout().formats() if not r.format.hasProperty(font_gap._MARK)]
check(
    "another component's layout format survives our updates",
    len(kept) == 1 and kept[0].format.fontUnderline(),
)
check("...and ours is still there", n_gap_ranges(c.document()) == 1)

# speed: a big page, every word a different font from its neighbour
big = rtl_doc()
bc = QTextCursor(big)
for i in range(2500):
    bc.insertText(W1 + " ", fmt(NAST if i % 2 == 0 else SANS))
    if i % 50 == 49:
        bc.insertBlock()
t0 = time.time()
changed = font_gap.apply_document(big, 0.07)
t_all = time.time() - t0
t0 = time.time()
blk = big.findBlockByNumber(10)
font_gap.apply_block(blk, 0.07)
t_one = time.time() - t0
check(
    f"whole 2,500-word page planned+applied in {t_all*1000:.0f} ms (< 1.5 s)",
    t_all < 1.5,
    f"{t_all:.2f}s",
)
check(
    f"...re-checking the block being edited takes {t_one*1000:.1f} ms (< 50 ms, i.e. typing stays instant)",
    t_one < 0.05,
    f"{t_one:.3f}s",
)
check(
    "...and the big page really got its gaps",
    changed >= 50 and n_gap_ranges(big) > 2000,
    f"{changed} blocks, {n_gap_ranges(big)} gaps",
)

# =============================================================== the UI ===
win = main.MainWindow()
win.show()
app.processEvents()
tools = next(m for m in win.menuBar().findChildren(QMenu) if m.title() == "&Tools")
gap_menu = next(
    (a.menu() for a in tools.actions() if a.menu() and "Gap Between Fonts" in a.text()),
    None,
)
check("Tools menu has 'Gap Between Fonts (Experimental)'", gap_menu is not None)
acts = {a.text(): a for a in gap_menu.actions()} if gap_menu else {}
check(
    "...with Off / Subtle / Normal / Wide",
    list(acts) == ["Off", "Subtle", "Normal", "Wide"],
    str(list(acts)),
)
check(
    "...all checkable, and exactly one checked",
    all(a.isCheckable() for a in acts.values())
    and sum(a.isChecked() for a in acts.values()) == 1,
)
check(
    "default is Normal", acts["Normal"].isChecked() and win.font_gap.level() == "normal"
)

acts["Wide"].trigger()
check(
    "choosing Wide switches the manager",
    win.font_gap.level() == "wide" and font_gap.active_level() == "wide",
)
check(
    "...and moves the check mark",
    acts["Wide"].isChecked() and not acts["Normal"].isChecked(),
)
check(
    "...and says so in the status bar",
    "wide" in win.statusBar().currentMessage().lower(),
)
acts["Off"].trigger()
check(
    "choosing Off switches it off",
    win.font_gap.level() == "off" and font_gap.active_level() == "off",
)
saved = _isolated_settings(font_settings.ORG, font_settings.APP).value(
    "experimental/fontGapLevel", "", type=str
)
check("the choice is remembered", saved == "off", saved)
win.canvas.document().setModified(False)
win.close()

win2 = main.MainWindow()
check(
    "a new window starts with the remembered level",
    win2.font_gap.level() == "off" and win2._font_gap_actions["off"].isChecked(),
)
win2._on_font_gap_failed("boom")
check(
    "a reported failure ticks Off in the menu and says so",
    win2._font_gap_actions["off"].isChecked()
    and "turned off" in win2.statusBar().currentMessage(),
)
win2._set_font_gap_level("normal")
win2.canvas.document().setModified(False)
win2.close()

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {failures}")
    sys.exit(1)
print("ALL FONT GAP CHECKS PASSED")
