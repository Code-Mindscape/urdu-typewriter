"""
test_project_io.py — headless check of .utw project save/load error
handling: corrupted files, wrong types, future format versions, and
permission errors all raise ProjectLoadError with a message meant to be
shown directly to the user, rather than a raw Python traceback — and a
malformed individual field (wrong type on one run's font size, etc.)
degrades gracefully instead of failing the whole load partway through.

    python test_project_io.py
"""

import sys
import os
import json
import stat
import tempfile

import project_io

results = []


def check(name, cond, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append(cond)


def expect_load_error(name, path, message_fragment=None):
    try:
        project_io.load_project(path)
        check(name, False, "did not raise ProjectLoadError")
    except project_io.ProjectLoadError as e:
        ok = message_fragment is None or message_fragment.lower() in str(e).lower()
        check(name, ok, str(e) if not ok else "")
    except Exception as e:
        check(
            name, False, f"raised {type(e).__name__} instead of ProjectLoadError: {e}"
        )


tmp = tempfile.mkdtemp()


def write(name, content):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


# ----------------------------------------------------------------------
print("--- malformed files raise ProjectLoadError with a clear message ---")
expect_load_error("not valid JSON at all", write("bad1.utw", "{not json"), "corrupted")
expect_load_error(
    "valid JSON but not an object (a bare list)",
    write("bad2.utw", "[1, 2, 3]"),
    "doesn't look like",
)
expect_load_error(
    "valid JSON object but no version field", write("bad3.utw", "{}"), "newer version"
)
expect_load_error(
    "future format version",
    write("bad4.utw", json.dumps({"version": 999, "page": {}, "blocks": []})),
    "newer version",
)
expect_load_error(
    "page field wrong type",
    write("bad5.utw", json.dumps({"version": 1, "page": "nope", "blocks": []})),
    "corrupted",
)
expect_load_error(
    "blocks field wrong type",
    write("bad6.utw", json.dumps({"version": 1, "page": {}, "blocks": "nope"})),
    "corrupted",
)
expect_load_error(
    "a block with non-list runs",
    write(
        "bad7.utw", json.dumps({"version": 1, "page": {}, "blocks": [{"runs": "nope"}]})
    ),
    "corrupted",
)
expect_load_error(
    "nonexistent file", os.path.join(tmp, "does-not-exist.utw"), "no longer exists"
)

# Binary garbage that isn't valid UTF-8 text at all
binpath = os.path.join(tmp, "binary.utw")
with open(binpath, "wb") as fh:
    fh.write(bytes([0xFF, 0xFE, 0x00, 0x01, 0x80, 0x81]))
expect_load_error("binary garbage (not valid UTF-8)", binpath, "doesn't look like")

if hasattr(os, "chmod") and os.name != "nt" and os.geteuid() != 0:
    noperm = write("noperm.utw", json.dumps({"version": 1, "page": {}, "blocks": []}))
    os.chmod(noperm, 0)
    try:
        expect_load_error("permission denied", noperm, "permission denied")
    finally:
        os.chmod(
            noperm, stat.S_IRUSR | stat.S_IWUSR
        )  # restore so tempdir cleanup can remove it
else:
    print(
        "[SKIP] permission denied — running as root, which bypasses file permissions "
        "(PermissionError handling itself is still real code, just not exercisable here)"
    )

# ----------------------------------------------------------------------
print("\n--- a well-formed file loads and validates cleanly ---")
good_path = write(
    "good.utw",
    json.dumps(
        {
            "version": project_io.FORMAT_VERSION,
            "page": {
                "width": 800,
                "height": 1000,
                "margin": 40,
                "background_color": "#ffffff",
            },
            "pending": {
                "family": "Noto Nastaliq Urdu",
                "style": "Regular",
                "size": 24,
                "color": "#000000",
            },
            "blocks": [
                {
                    "alignment": "right",
                    "runs": [
                        {
                            "text": "سلام",
                            "family": "Noto Nastaliq Urdu",
                            "style": "Regular",
                            "size": 24,
                            "color": "#000000",
                        }
                    ],
                }
            ],
        }
    ),
)
try:
    loaded = project_io.load_project(good_path)
    check("well-formed file loads without raising", True)
    check("page data preserved", loaded["page"]["width"] == 800)
    check("blocks data preserved", loaded["blocks"][0]["runs"][0]["text"] == "سلام")
except Exception as e:
    check("well-formed file loads without raising", False, str(e))

# ----------------------------------------------------------------------
print("\n--- malformed individual fields degrade gracefully (apply_loaded_project) ---")
# These exercise apply_loaded_project directly against a headless
# PyQt6 canvas, since that's where per-field coercion happens (not
# load_project, which only validates overall shape).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402
from PyQt6.QtCore import Qt  # noqa: E402
from canvas import PageCanvas  # noqa: E402

app = QApplication(sys.argv)


def try_apply(name, loaded_dict):
    canvas = PageCanvas(800, 1000, default_family="Arial")
    try:
        project_io.apply_loaded_project(loaded_dict, canvas)
        check(name, True)
        return canvas
    except Exception as e:
        check(name, False, f"raised {type(e).__name__}: {e}")
        return None


try_apply(
    "non-numeric page width falls back instead of raising",
    {
        "page": {
            "width": "not-a-number",
            "height": 1000,
            "margin": 40,
            "background_color": "#fff",
        },
        "pending": {},
        "blocks": [],
    },
)

try_apply(
    "negative page width is clamped, not passed through raw",
    {
        "page": {
            "width": -500,
            "height": 1000,
            "margin": 40,
            "background_color": "#fff",
        },
        "pending": {},
        "blocks": [],
    },
)

canvas = try_apply(
    "non-numeric run size falls back instead of aborting the whole load",
    {
        "page": {
            "width": 800,
            "height": 1000,
            "margin": 40,
            "background_color": "#fff",
        },
        "pending": {},
        "blocks": [
            {
                "alignment": "right",
                "runs": [{"text": "test", "size": "huge", "color": "#000000"}],
            }
        ],
    },
)
if canvas is not None:
    check(
        "...and the text itself still made it in",
        canvas.toPlainText() == "test",
        repr(canvas.toPlainText()),
    )

canvas = try_apply(
    "a malformed run inside a block is skipped, not fatal to the rest",
    {
        "page": {
            "width": 800,
            "height": 1000,
            "margin": 40,
            "background_color": "#fff",
        },
        "pending": {},
        "blocks": [
            {
                "alignment": "right",
                "runs": ["not-a-dict-run", {"text": "ok text", "color": "#000000"}],
            }
        ],
    },
)
if canvas is not None:
    check(
        "...and the well-formed run next to it still landed",
        "ok text" in canvas.toPlainText(),
        repr(canvas.toPlainText()),
    )

canvas = try_apply(
    "invalid color string falls back instead of raising",
    {
        "page": {
            "width": 800,
            "height": 1000,
            "margin": 40,
            "background_color": "not-a-color",
        },
        "pending": {},
        "blocks": [],
    },
)

# ----------------------------------------------------------------------
print("\n--- background image, border, and per-run formatting (added in format v3) ---")

canvas = try_apply(
    "garbage background_image data falls back to 'no image' instead of raising",
    {
        "page": {
            "width": 800,
            "height": 1000,
            "margin": 40,
            "background_color": "#fff",
            "background_image": "not valid base64 png data!!",
        },
        "pending": {},
        "blocks": [],
    },
)
if canvas is not None:
    check(
        "...and background_image() is None, not a broken image",
        canvas.background_image() is None,
    )

canvas = try_apply(
    "an out-of-range background_image_mode falls back to 'fit'",
    {
        "page": {
            "width": 800,
            "height": 1000,
            "margin": 40,
            "background_color": "#fff",
            "background_image_mode": "explode",
        },
        "pending": {},
        "blocks": [],
    },
)
if canvas is not None:
    check("...", canvas.background_image_mode() == "fit")

canvas = try_apply(
    "a v2 file with none of the new fields at all still loads cleanly",
    {
        "page": {
            "width": 800,
            "height": 1000,
            "margin": 40,
            "background_color": "#fff",
        },
        "pending": {"family": "Arial", "style": "", "size": 16, "color": "#000000"},
        "blocks": [
            {
                "alignment": "right",
                "runs": [
                    {
                        "text": "old file, no new fields",
                        "family": "Arial",
                        "style": "",
                        "size": 16,
                        "color": "#000000",
                    }
                ],
            }
        ],
    },
)
if canvas is not None:
    check("...text came through", canvas.toPlainText() == "old file, no new fields")
    check("...border defaults to 0 (none)", canvas.border_width() == 0)
    check("...background image defaults to None", canvas.background_image() is None)
    check(
        "...border style defaults to solid",
        canvas.border_style() == Qt.PenStyle.SolidLine,
    )
    check("...vertical alignment defaults to top", canvas.vertical_align() == "top")
    check(
        "...word spacing defaults to 0 (field didn't exist yet)",
        canvas.document().begin().begin().fragment().charFormat().font().wordSpacing()
        == 0.0,
    )
    check(
        "...pending word spacing defaults to 0 too",
        canvas.pending_word_spacing() == 0.0,
    )

# Full round trip through a real canvas: save what a person actually set,
# reload it into a fresh canvas, and check every new property survived —
# broader than the field-by-field checks above, closer to what actually
# happens when someone saves and reopens a project.
from PyQt6.QtCore import Qt as _Qt

rt_canvas = PageCanvas(
    500, 500, "#eeeeee", 25, default_family="Arial", default_style="", default_size=20
)
from PyQt6.QtGui import QColor as _QColor, QImage as _QImage

bg_img = _QImage(40, 40, _QImage.Format.Format_RGB32)
bg_img.fill(_QColor("#336699"))
rt_canvas.set_background_image(bg_img, mode="tile")
rt_canvas.set_border(4, _QColor("#998877"), _Qt.PenStyle.DashLine)
rt_canvas.setPlainText("round trip me")
rt_canvas.selectAll()
rt_canvas.set_pending_underline(True)
rt_canvas.set_pending_highlight(_QColor("#00FF00"))
rt_canvas.set_pending_letter_spacing(133)
rt_cursor = rt_canvas.textCursor()
rt_cursor.select(rt_cursor.SelectionType.Document)
rt_canvas.setTextCursor(rt_cursor)
rt_canvas.set_word_spacing(37)
rt_canvas.apply_line_spacing(140)
rt_canvas.set_vertical_align("middle")

rt_path = write(
    "roundtrip.utw", ""
)  # create the path; save_project() will overwrite it
try:
    project_io.save_project(rt_path, rt_canvas)
    reloaded = project_io.load_project(rt_path)
    fresh = PageCanvas(
        10, 10, "white", 0, default_family="Arial", default_style="", default_size=10
    )
    project_io.apply_loaded_project(reloaded, fresh)

    check(
        "round trip: file was saved as format version 4",
        json.load(open(rt_path))["version"] == 4,
    )
    check(
        "round trip: text preserved",
        fresh.toPlainText() == "round trip me",
        repr(fresh.toPlainText()),
    )
    check(
        "round trip: background image preserved",
        fresh.background_image() is not None and not fresh.background_image().isNull(),
    )
    check(
        "round trip: background image mode preserved",
        fresh.background_image_mode() == "tile",
    )
    check(
        "round trip: border width+color preserved",
        fresh.border_width() == 4 and fresh.border_color().name().lower() == "#998877",
    )
    check(
        "round trip: border style preserved",
        fresh.border_style() == _Qt.PenStyle.DashLine,
    )
    check(
        "round trip: vertical alignment preserved", fresh.vertical_align() == "middle"
    )
    first_block = fresh.document().begin()
    first_frag = first_block.begin().fragment()
    first_fmt = first_frag.charFormat()
    check("round trip: underline preserved", first_fmt.fontUnderline() is True)
    check(
        "round trip: highlight color preserved",
        first_fmt.background().color().name().lower() == "#00ff00",
    )
    check(
        "round trip: letter spacing preserved",
        round(first_fmt.font().letterSpacing()) == 133,
    )
    check(
        "round trip: word spacing preserved",
        round(first_fmt.font().wordSpacing(), 2) == 37.0,
        str(first_fmt.font().wordSpacing()),
    )
    check(
        "round trip: line spacing preserved",
        round(first_block.blockFormat().lineHeight()) == 140,
    )

    # An empty page has no run to carry word spacing on, so it has to ride
    # along as "pending" instead (same mechanism family/size/color already
    # use) -- check that path separately.
    empty_canvas = PageCanvas(
        10, 10, "white", 0, default_family="Arial", default_style="", default_size=10
    )
    empty_canvas.set_word_spacing(64)
    empty_path = write("empty-word-spacing.utw", "")
    project_io.save_project(empty_path, empty_canvas)
    empty_reloaded = PageCanvas(
        10, 10, "white", 0, default_family="Arial", default_style="", default_size=10
    )
    project_io.apply_loaded_project(project_io.load_project(empty_path), empty_reloaded)
    check(
        "round trip: pending word spacing preserved on an empty page",
        empty_reloaded.pending_word_spacing() == 64.0,
        str(empty_reloaded.pending_word_spacing()),
    )
except Exception as e:
    check(
        "full round trip completes without raising", False, f"{type(e).__name__}: {e}"
    )

# ----------------------------------------------------------------------
print("\n--- undo stack starts clean (regression test) ---")
# setAlignment() and document().setDocumentMargin() during PageCanvas
# construction are real, undoable QTextDocument edits as far as Qt is
# concerned, even though no visible text changed -- found by testing
# main.py's Undo menu item, which was never becoming enabled because it
# was listening for a False->True transition on undoAvailable that had
# already happened before it could connect. See PageCanvas.__init__'s and
# apply_loaded_project()'s calls to clearUndoRedoStacks().
fresh_canvas = PageCanvas(
    400, 400, "#eeeeee", 30, default_family="Arial", default_style="", default_size=14
)
check(
    "a freshly constructed canvas has NO undo history available "
    "(setAlignment/setDocumentMargin during setup must not count as user edits)",
    fresh_canvas.document().isUndoAvailable() is False,
)
fresh_canvas.textCursor().insertText("x")
check(
    "...but a real edit after construction does become undoable",
    fresh_canvas.document().isUndoAvailable() is True,
)

loaded_canvas = PageCanvas(
    10, 10, "white", 0, default_family="Arial", default_style="", default_size=10
)
project_io.apply_loaded_project(project_io.load_project(rt_path), loaded_canvas)
check(
    "a canvas right after loading a project also has no undo history "
    "(loading a file isn't something Ctrl+Z should partially unwind)",
    loaded_canvas.document().isUndoAvailable() is False,
)

print()
if all(results):
    print(f"ALL {len(results)} PROJECT_IO ERROR-HANDLING CHECKS PASSED")
else:
    print(f"{results.count(False)} of {len(results)} CHECKS FAILED")
    sys.exit(1)
