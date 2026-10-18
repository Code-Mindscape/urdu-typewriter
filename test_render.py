"""
test_render.py — headless sanity check for the shaping + export pipeline.

Run this after installing dependencies to confirm Urdu text is shaping
(joining) correctly on your machine and that all five export formats work,
without needing to open the GUI. Writes a few files into ./_smoketest/.

    python test_render.py
"""

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt

from canvas import PageCanvas
from main import load_bundled_fonts
import exporter

OUT_DIR = Path(__file__).parent / "_smoketest"
OUT_DIR.mkdir(exist_ok=True)

app = QApplication(sys.argv)

family = load_bundled_fonts()
assert (
    family
), "Bundled font failed to load — check fonts/NotoNastaliqUrdu-Regular.ttf exists."
print(f"Loaded bundled font as family: '{family}'")

canvas = PageCanvas(
    1080, 1080, QColor("#0b3d2e"), 60, default_family=family, default_size=64
)
canvas.set_pending_color(QColor("white"))
canvas.textCursor().insertText("زندگی ایک خوبصورت سفر ہے")
canvas.setTextCursor(canvas.textCursor())
canvas.selectAll()
canvas.apply_alignment(Qt.AlignmentFlag.AlignCenter)
doc = canvas.document()

jobs = [
    (
        "PNG (opaque)",
        lambda: exporter.export_raster(
            doc,
            canvas.page_width,
            canvas.page_height,
            1080,
            1080,
            canvas.background_color(),
            str(OUT_DIR / "sample.png"),
            "PNG",
        ),
    ),
    (
        "PNG (transparent, 2x)",
        lambda: exporter.export_raster(
            doc,
            canvas.page_width,
            canvas.page_height,
            2160,
            2160,
            None,
            str(OUT_DIR / "sample_transparent_2x.png"),
            "PNG",
        ),
    ),
    (
        "JPG",
        lambda: exporter.export_raster(
            doc,
            canvas.page_width,
            canvas.page_height,
            1080,
            1080,
            canvas.background_color(),
            str(OUT_DIR / "sample.jpg"),
            "JPG",
        ),
    ),
    (
        "SVG (outlined, portable)",
        lambda: exporter.export_svg(
            doc,
            canvas.page_width,
            canvas.page_height,
            canvas.background_color(),
            str(OUT_DIR / "sample.svg"),
        ),
    ),
    (
        "PDF (font embedded)",
        lambda: exporter.export_pdf(
            doc,
            canvas.page_width,
            canvas.page_height,
            canvas.background_color(),
            str(OUT_DIR / "sample.pdf"),
        ),
    ),
    (
        "EPS (outlined, portable)",
        lambda: exporter.export_eps(
            doc,
            canvas.page_width,
            canvas.page_height,
            canvas.background_color(),
            str(OUT_DIR / "sample.eps"),
        ),
    ),
]

for label, job in jobs:
    job()
    print(f"  ok  {label}")

print(f"\nAll exports succeeded. Open {OUT_DIR}/sample.png to confirm the")
print("Urdu letters look properly joined (not separated) on your system.")

# Each export backend (QImage.save / QSvgGenerator / QPdfWriter; EPS is plain
# file I/O) fails
# silently by default — no exception, no file, no error — which used to
# mean a bad path (or a full disk, or a read-only folder) looked exactly
# like a successful export to the person using the app. Confirm all
# three now raise instead of failing silently.
print(
    "\nChecking that a bad export path is reported as an error, not silently ignored:"
)
bad_dir = "/this_directory_should_not_exist_xyz/out"
failure_jobs = [
    (
        "raster",
        lambda: exporter.export_raster(
            doc,
            canvas.page_width,
            canvas.page_height,
            1080,
            1080,
            canvas.background_color(),
            f"{bad_dir}.png",
            "PNG",
        ),
    ),
    (
        "svg",
        lambda: exporter.export_svg(
            doc,
            canvas.page_width,
            canvas.page_height,
            canvas.background_color(),
            f"{bad_dir}.svg",
        ),
    ),
    (
        "pdf",
        lambda: exporter.export_pdf(
            doc,
            canvas.page_width,
            canvas.page_height,
            canvas.background_color(),
            f"{bad_dir}.pdf",
        ),
    ),
    (
        "eps",
        lambda: exporter.export_eps(
            doc,
            canvas.page_width,
            canvas.page_height,
            canvas.background_color(),
            f"{bad_dir}.eps",
        ),
    ),
]
for label, job in failure_jobs:
    try:
        job()
        raise AssertionError(
            f"{label} export to a nonexistent folder did not raise — silent failure!"
        )
    except OSError:
        print(f"  ok  {label} correctly raised OSError instead of failing silently")

# text_outlines.py's "run" granularity groups by same-formatting span
# instead of one glyphRuns() call per character, specifically so that a
# cursive, contextually-shaped script like Nastaliq doesn't show a
# hairline gap between two letters that should sit flush against each
# other (see extract_groups()'s docstring for why per-character
# extraction can round differently than the original whole-run shaping
# did). Confirm on the real Nastaliq text above that "run" mode still
# produces exactly the same visible glyphs as "character" mode -- same
# non-empty outline count, same overall bounding box -- just batched
# into far fewer groups (proof the merging is actually happening, not
# silently still one group per character).
print("\nChecking that SVG export's outline grouping doesn't drop or move any glyphs:")
import text_outlines

char_groups = text_outlines.extract_groups(doc, granularity="character")
run_groups = text_outlines.extract_groups(doc, granularity="run")
char_total = sum(len(g["paths"]) for g in char_groups)
run_total = sum(len(g["paths"]) for g in run_groups)
assert (
    char_total == run_total
), f"glyph count changed: character={char_total} run={run_total}"
assert len(run_groups) < len(char_groups), (
    f'"run" grouping isn\'t merging anything: {len(run_groups)} groups vs '
    f'{len(char_groups)} for "character" — expected far fewer'
)


def _union_bbox(groups):
    bbox = None
    for g in groups:
        bbox = g["bbox"] if bbox is None else bbox.united(g["bbox"])
    return bbox


char_bbox, run_bbox = _union_bbox(char_groups), _union_bbox(run_groups)
for attr in ("x", "y", "width", "height"):
    a, b = getattr(char_bbox, attr)(), getattr(run_bbox, attr)()
    assert abs(a - b) < 0.01, f"bounding box {attr} differs: character={a} run={b}"
print(
    f"  ok  same {char_total} glyphs, same overall bounding box, "
    f"{len(char_groups)} groups (character) -> {len(run_groups)} groups (run)"
)

# render_svg_bytes() (used by main.py's "Copy as SVG") shares export_svg()'s
# rendering but returns bytes instead of writing a file -- confirm it
# actually produces well-formed, non-trivial SVG content.
svg_bytes = exporter.render_svg_bytes(
    doc, canvas.page_width, canvas.page_height, canvas.background_color()
)
assert isinstance(svg_bytes, bytes) and len(svg_bytes) > 500
assert b"<svg" in svg_bytes[:300]
print(
    f"  ok  render_svg_bytes() produced {len(svg_bytes)} bytes of valid-looking SVG NOT OK"
)
