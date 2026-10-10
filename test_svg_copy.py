"""
test_svg_copy.py — headless checks for "Copy as SVG (Curves)":

  * the SVG and PDF are each cropped to the glyphs' own bounding box, not
    the page
  * only the selected text is included when there's a selection
  * the curves are the same ones a full-page export draws
  * nothing to copy (empty / whitespace-only) raises instead of producing
    a blank file
  * the clipboard payload (exporter.make_curves_mime_data) carries the SVG
    and PDF under every name a receiving app might look for -- on Windows
    including PDF as "Portable Document Format", the name Affinity registers for PDF;
    with only the MIME-style names, nothing pasted into Affinity
  * the real clipboard flow (main.py's copy_as_svg) puts the vector formats
    on the clipboard, and no raster image alongside them
  * the same action is offered in the page's right-click menu

    python test_svg_copy.py
"""
import os
import re
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QColor, QImage, QPainter, QTextCursor
from PyQt6.QtCore import QByteArray, QRectF, QSettings, QSize, QStandardPaths
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtPdf import QPdfDocument

# Isolate settings so this can never read or modify your real preferences
# (main.py's MainWindow, used below for the end-to-end clipboard check,
# reads Preferences on construction).
_tmp_settings = tempfile.mkdtemp(prefix="utw_svgcopy_test_")
_Real = QSettings
_Real.setPath(_Real.Format.IniFormat, _Real.Scope.UserScope, _tmp_settings)


def _isolated_settings(org, app=None, *_ignored):
    return _Real(_Real.Format.IniFormat, _Real.Scope.UserScope, org, app)


import font_settings
font_settings.QSettings = _isolated_settings

app = QApplication(sys.argv)
# Isolates QStandardPaths.AppDataLocation too -- not just QSettings above --
# so MainWindow's autosave-recovery check can never find a real recovery
# file and block this test on a dialog with nothing to click.
QStandardPaths.setTestModeEnabled(True)
import main
main.QSettings = _isolated_settings

from canvas import PageCanvas
from main import load_bundled_fonts
import exporter
import text_outlines

failures = []


def check(label, ok, detail=""):
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def svg_frame(svg_bytes):
    """(width, height) of the SVG's viewBox."""
    m = re.search(rb'viewBox="([\d.\-]+) ([\d.\-]+) ([\d.\-]+) ([\d.\-]+)"', svg_bytes)
    assert m, "no viewBox in SVG"
    return float(m.group(3)), float(m.group(4))


def rasterize(svg_bytes, scale=1.0):
    """Render at the SVG's viewBox size (one image pixel per viewBox unit,
    times `scale`). Not QSvgRenderer.defaultSize(): that converts the
    SVG's physical width/height at 90 dpi, so it comes out 0.9375x the
    viewBox for a 96-dpi file and would make every size comparison off."""
    renderer = QSvgRenderer(QByteArray(svg_bytes))
    vb = renderer.viewBoxF()
    w, h = max(1, round(vb.width() * scale)), max(1, round(vb.height() * scale))
    img = QImage(w, h, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    renderer.render(p, QRectF(0, 0, w, h))
    p.end()
    return img


def ink_bounds(img):
    """Bounding rect (x0, y0, x1, y1) of non-transparent pixels, or None."""
    x0, y0, x1, y1 = img.width(), img.height(), -1, -1
    for y in range(img.height()):
        for x in range(img.width()):
            if (img.pixel(x, y) >> 24) & 0xFF > 8:
                x0, y0 = min(x0, x), min(y0, y)
                x1, y1 = max(x1, x), max(y1, y)
    return None if x1 < 0 else (x0, y0, x1, y1)


def ink_area(img):
    total = 0
    for y in range(img.height()):
        for x in range(img.width()):
            total += (img.pixel(x, y) >> 24) & 0xFF
    return total


family = load_bundled_fonts()
canvas = PageCanvas(794, 1123, QColor("white"), 40, default_family=family, default_size=36)
LINE_1 = "زندگی ایک خوبصورت سفر ہے"
LINE_2 = "محبت سب سے بڑی دولت ہے"
canvas.textCursor().insertText(LINE_1 + "\n" + LINE_2)
doc = canvas.document()
page_w, page_h = canvas.page_width, canvas.page_height

# ---------------------------------------------------------------- whole text
full = exporter.render_svg_curves_bytes(doc, page_w)
fw, fh = svg_frame(full)
check("cropped SVG is far smaller than the page (height)", fh < page_h * 0.25,
      f"{fw}x{fh} vs page {page_w}x{page_h}")
check("cropped SVG is smaller than the page (width) — text doesn't span the margin",
      fw < page_w, f"{fw} vs {page_w}")
check("no page-sized elements: SVG frame isn't the page size",
      not (round(fw) == page_w and round(fh) == page_h))

img = rasterize(full, scale=1.0)
check("rasterized size matches the viewBox", (img.width(), img.height()) == (round(fw), round(fh)),
      f"{img.width()}x{img.height()} vs {fw}x{fh}")
bounds = ink_bounds(img)
check("SVG contains visible ink", bounds is not None)
if bounds:
    x0, y0, x1, y1 = bounds
    check("ink touches the left/top edges (tight crop, paths correctly shifted)",
          x0 <= 2 and y0 <= 2, f"ink starts at ({x0},{y0})")
    check("ink reaches the right/bottom edges (frame isn't oversized)",
          x1 >= img.width() - 3 and y1 >= img.height() - 3,
          f"ink ends at ({x1},{y1}) in {img.width()}x{img.height()}")

check("no <rect> background element in the curves SVG", b"<rect" not in full)

# --------------------------------------------- same curves as a full-page export
page_svg = exporter.render_svg_bytes(doc, page_w, page_h, None)
page_ink = ink_area(rasterize(page_svg))
crop_ink = ink_area(img)
ratio = crop_ink / page_ink if page_ink else 0
check("cropped SVG holds the same ink as a full-page export (same curves, just cropped)",
      0.97 <= ratio <= 1.03, f"ink ratio {ratio:.3f}")

# ------------------------------------------------------- selection: one block
block2 = doc.findBlockByNumber(1)
sel_start, sel_end = block2.position(), block2.position() + len(block2.text())
groups_sel = text_outlines.extract_groups(doc, "run", char_range=(sel_start, sel_end))
check("selecting only line 2 extracts only line 2's text",
      "".join(g["text"] for g in groups_sel) == LINE_2,
      repr("".join(g["text"] for g in groups_sel)))
line2_svg = exporter.render_svg_curves_bytes(doc, page_w, char_range=(sel_start, sel_end))
_, h2 = svg_frame(line2_svg)
check("a one-line selection is shorter than the two-line SVG", h2 < fh, f"{h2} vs {fh}")

# ------------------------------------------------ selection: partial line, mid-word
first_block = doc.findBlockByNumber(0)
a = first_block.position() + 5
b = first_block.position() + 12
partial = text_outlines.extract_groups(doc, "run", char_range=(a, b))
expected = first_block.text()[5:12]
check("a mid-line selection extracts exactly the selected characters",
      "".join(g["text"] for g in partial) == expected,
      f"{''.join(g['text'] for g in partial)!r} vs {expected!r}")

# ------------------------------------------- selection: spans both paragraphs
c = first_block.position() + 15
d = block2.position() + 6
span = text_outlines.extract_groups(doc, "run", char_range=(c, d))
expected_span = first_block.text()[15:] + block2.text()[:6]
check("a selection spanning two paragraphs extracts both partial lines",
      "".join(g["text"] for g in span) == expected_span,
      f"{''.join(g['text'] for g in span)!r} vs {expected_span!r}")

# ---------------------------------------------------- range None == full range
whole = text_outlines.extract_groups(doc, "run")
explicit = text_outlines.extract_groups(doc, "run", char_range=(0, doc.characterCount()))
check("char_range=None extracts the same groups as an all-covering range",
      [g["text"] for g in whole] == [g["text"] for g in explicit]
      and sum(len(g["paths"]) for g in whole) == sum(len(g["paths"]) for g in explicit))

# ------------------------------------------------------------- nothing to copy
canvas2 = PageCanvas(794, 1123, QColor("white"), 40, default_family=family, default_size=36)
try:
    exporter.render_svg_curves_bytes(canvas2.document(), 794)
    check("empty page raises ValueError", False, "no exception")
except ValueError:
    check("empty page raises ValueError", True)

canvas2.textCursor().insertText("   ")
try:
    exporter.render_svg_curves_bytes(canvas2.document(), 794)
    check("whitespace-only page raises ValueError", False, "no exception")
except ValueError:
    check("whitespace-only page raises ValueError", True)

try:
    exporter.render_svg_curves_bytes(doc, page_w, char_range=(0, 0))
    check("empty selection range raises ValueError", False, "no exception")
except ValueError:
    check("empty selection range raises ValueError", True)

# ============================================================ PDF curves ===
# Same coverage as the SVG checks above, for exporter.render_pdf_curves_bytes.
# QPdfDocument.load() only reliably picked up a temp file in testing (not a
# QBuffer/QIODevice directly), which is fine — that's a test-harness detail,
# not something the app itself needs, since it only ever *writes* PDF bytes
# for the clipboard, never reads them back.

def rasterize_pdf(pdf_bytes, scale=4.0):
    """Render page 0 at `scale` image pixels per PDF point (PDF points are
    72/inch; the generator uses 96 dpi, so scale=4 keeps this comparable in
    resolution to the SVG rasterize() above's 1 px/viewBox-unit)."""
    path = Path(tempfile.mkstemp(suffix=".pdf")[1])
    path.write_bytes(pdf_bytes)
    doc = QPdfDocument(None)
    doc.load(str(path))
    deadline = time.time() + 5
    while doc.status() == QPdfDocument.Status.Loading and time.time() < deadline:
        app.processEvents(); time.sleep(0.01)
    assert doc.status() == QPdfDocument.Status.Ready, f"PDF failed to load: {doc.error()}"
    pt = doc.pagePointSize(0)
    img = doc.render(0, QSize(max(1, round(pt.width() * scale)), max(1, round(pt.height() * scale))))
    path.unlink(missing_ok=True)
    return img, pt


pdf_full = exporter.render_pdf_curves_bytes(doc, page_w)
pdf_img, pdf_pt = rasterize_pdf(pdf_full)
pdf_w_px = pdf_pt.width() * 96.0 / 72.0   # back to the same px space the SVG checks use
pdf_h_px = pdf_pt.height() * 96.0 / 72.0
check("PDF page is cropped, not full-page sized", pdf_h_px < page_h * 0.25 and pdf_w_px < page_w,
      f"{pdf_w_px:.0f}x{pdf_h_px:.0f} vs page {page_w}x{page_h}")
check("PDF crop matches the SVG crop (same content, same bbox math)",
      abs(pdf_w_px - fw) < 1.5 and abs(pdf_h_px - fh) < 1.5,
      f"PDF {pdf_w_px:.1f}x{pdf_h_px:.1f} vs SVG {fw}x{fh}")
pdf_bounds = ink_bounds(pdf_img)
check("PDF contains visible ink", pdf_bounds is not None)
if pdf_bounds:
    x0, y0, x1, y1 = pdf_bounds
    check("PDF ink touches the top/left edges (tight crop)", x0 <= 3 and y0 <= 3,
          f"ink starts at ({x0},{y0})")
    check("PDF ink reaches the bottom/right edges (frame isn't oversized)",
          x1 >= pdf_img.width() - 4 and y1 >= pdf_img.height() - 4,
          f"ink ends at ({x1},{y1}) in {pdf_img.width()}x{pdf_img.height()}")
pdf_sel = exporter.render_pdf_curves_bytes(doc, page_w, char_range=(sel_start, sel_end))
_, sel_pt = rasterize_pdf(pdf_sel)
check("a one-line PDF selection is shorter than the two-line PDF",
      sel_pt.height() < pdf_pt.height(), f"{sel_pt.height()} vs {pdf_pt.height()}")

canvas3 = PageCanvas(794, 1123, QColor("white"), 40, default_family=family, default_size=36)
try:
    exporter.render_pdf_curves_bytes(canvas3.document(), 794)
    check("empty page raises ValueError (PDF)", False, "no exception")
except ValueError:
    check("empty page raises ValueError (PDF)", True)

try:
    exporter.render_pdf_curves_bytes(doc, page_w, char_range=(0, 0))
    check("empty selection range raises ValueError (PDF)", False, "no exception")
except ValueError:
    check("empty selection range raises ValueError (PDF)", True)

# ============================================ clipboard payload / names ===
# Windows matches clipboard formats by exact registered name, so the PDF has
# to be present as "Portable Document Format" (what Affinity registers), not
# only as "application/pdf". Qt registers an arbitrary Windows format name
# from application/x-qt-windows-mime;value="<name>". `platform` is injectable
# so this is checkable from any OS.
_WIN_PDF_KEY = 'application/x-qt-windows-mime;value="Portable Document Format"'
_fake_svg = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'
_fake_pdf = b"%PDF-1.4\n%fake\n"

m_win = exporter.make_curves_mime_data(_fake_svg, _fake_pdf, platform="win32")
check("windows payload: formats are exactly [PDF-by-Windows-name, SVG, PDF], in that order",
      m_win.formats() == [_WIN_PDF_KEY, "image/svg+xml", "application/pdf"], str(m_win.formats()))
check("windows payload: the Windows-named PDF is byte-identical to the MIME-named PDF",
      bytes(m_win.data(_WIN_PDF_KEY)) == bytes(m_win.data("application/pdf")) == _fake_pdf)
check("windows payload: SVG bytes preserved", bytes(m_win.data("image/svg+xml")) == _fake_svg)
check("windows payload: no raster image", not m_win.hasImage())
check("the registered Windows name is exactly 'Portable Document Format'",
      exporter.WINDOWS_PDF_CLIPBOARD_NAME == "Portable Document Format")

for plat in ("linux", "darwin"):
    m_other = exporter.make_curves_mime_data(_fake_svg, _fake_pdf, platform=plat)
    check(f"{plat} payload: just SVG + PDF (no Windows-only format)",
          m_other.formats() == ["image/svg+xml", "application/pdf"], str(m_other.formats()))

# ================================================== full clipboard flow ===
# Through the real MainWindow.copy_as_svg(), not the exporter functions
# directly: confirms the clipboard actually ends up with both curve
# formats and nothing else (no raster image competing with them — see
# copy_as_svg's docstring for why that matters).
from PyQt6.QtGui import QTextCursor as _QTextCursor
from PyQt6.QtWidgets import QApplication as _QApplication

win = main.MainWindow()
win.resize(1000, 800)
win.show()
win.canvas.textCursor().insertText(LINE_1 + "\n" + LINE_2)

win.copy_as_svg()
deadline = time.time() + 15
while win._svg_copy_thread.isRunning() and time.time() < deadline:
    app.processEvents(); time.sleep(0.02)
win._svg_copy_thread.wait(2000)
app.processEvents()

mime = _QApplication.clipboard().mimeData()
check("clipboard flow: not still running", not win._svg_copy_thread.isRunning())
check("clipboard has SVG data", mime is not None and mime.hasFormat("image/svg+xml"))
check("clipboard has PDF data", mime is not None and mime.hasFormat("application/pdf"))
check("clipboard has NO competing raster image (the original pixelation bug)",
      mime is not None and not mime.hasImage())
if mime is not None:
    clip_svg = bytes(mime.data("image/svg+xml"))
    clip_pdf = bytes(mime.data("application/pdf"))
    check("clipboard SVG bytes are non-trivial", len(clip_svg) > 200, str(len(clip_svg)))
    check("clipboard PDF bytes are non-trivial and start with %PDF",
          len(clip_pdf) > 200 and clip_pdf[:5] == b"%PDF-", str(clip_pdf[:5]))
check("clipboard formats on this platform match the payload builder's list",
      mime is not None and mime.formats() == exporter.make_curves_mime_data(
          b"x", b"y").formats(), str(mime.formats() if mime is not None else None))
check("menu label says 'Copy as SVG'", win.copy_svg_menu_act.text().startswith("Copy as SVG"),
      win.copy_svg_menu_act.text())
check("the same action is registered for the page's right-click menu",
      win.copy_svg_menu_act in win.canvas._extra_context_actions)

# selection-only through the real flow too
cur = win.canvas.textCursor()
b2 = win.canvas.document().findBlockByNumber(1)
cur.setPosition(b2.position())
cur.setPosition(b2.position() + len(b2.text()), _QTextCursor.MoveMode.KeepAnchor)
win.canvas.setTextCursor(cur)
win.copy_as_svg()
deadline = time.time() + 15
while win._svg_copy_thread.isRunning() and time.time() < deadline:
    app.processEvents(); time.sleep(0.02)
win._svg_copy_thread.wait(2000)
app.processEvents()
mime2 = _QApplication.clipboard().mimeData()
check("clipboard flow: status message says 'selection'", "selection" in win.statusBar().currentMessage(),
      win.statusBar().currentMessage())
if mime2 is not None and mime2.hasFormat("image/svg+xml"):
    check("selection copy produces a smaller SVG than the full-page copy",
          len(bytes(mime2.data("image/svg+xml"))) < len(clip_svg) if mime is not None else True)

win.canvas.document().setModified(False)
win.close()

print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {failures}")
    sys.exit(1)
print("ALL SVG COPY CHECKS PASSED")
