"""
test_eps.py — headless checks for EPS export:

  * the file is well-formed Encapsulated PostScript: DSC header, an integer
    %%BoundingBox that encloses the %%HiResBoundingBox, 7-bit ASCII only,
    no line over the 255-character DSC limit, balanced gsave/grestore
  * every glyph outline is one closed, filled path (same count as the SVG
    export draws), in the run's colour
  * page background colour / border / dashes are present only when asked for
  * a background image is embedded as a decodable JPEG, in every fit mode
  * "Export All Formats" now writes an .eps too, and the Export dialog lists
    EPS as a (vector) format
  * if Ghostscript is installed (it's NOT needed to run the app -- the EPS is
    written directly in pure Python), the EPS is also rendered by it and
    compared with the app's own rendering of the same page. That's an
    independent PostScript interpreter, so it catches mistakes a hand-written
    checker would share with the writer. Skipped with a notice otherwise.

    python test_eps.py
"""
import base64
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtWidgets import QApplication, QFileDialog
from PyQt6.QtGui import (
    QColor, QFont, QImage, QPainter, QTextCharFormat, QTextCursor, QTextDocument, QTextOption,
)
from PyQt6.QtCore import QSettings, QStandardPaths, Qt

# Tests that build a MainWindow must never touch real preferences or the real
# autosave/recovery file (see the README's note on test isolation).
_settings_dir = tempfile.mkdtemp(prefix="utw_eps_settings_")
_RealSettings = QSettings
_RealSettings.setPath(_RealSettings.Format.IniFormat, _RealSettings.Scope.UserScope, _settings_dir)


def _isolated_settings(org, app_name=None, *_ignored):
    return _RealSettings(_RealSettings.Format.IniFormat, _RealSettings.Scope.UserScope, org, app_name)


import font_settings  # noqa: E402

font_settings.QSettings = _isolated_settings
app = QApplication(sys.argv)
QStandardPaths.setTestModeEnabled(True)
import main  # noqa: E402  (bundled fonts live in main.py)

main.QSettings = _isolated_settings
import exporter  # noqa: E402
import text_outlines  # noqa: E402
from dialogs import ExportDialog  # noqa: E402

main.load_bundled_fonts()

failures = []


def check(label, ok, detail=""):
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


W, H = 794, 1123


def make_doc(two_colours=True):
    doc = QTextDocument()
    opt = QTextOption()
    opt.setTextDirection(Qt.LayoutDirection.RightToLeft)
    opt.setAlignment(Qt.AlignmentFlag.AlignRight)
    doc.setDefaultTextOption(opt)
    doc.setDocumentMargin(40)
    doc.setTextWidth(W)
    c = QTextCursor(doc)

    def fmt(family, colour):
        f = QTextCharFormat()
        font = QFont(family)
        font.setPointSize(36)
        f.setFont(font)
        f.setForeground(QColor(colour))
        return f

    c.insertText("اردو ٹائپ رائٹر", fmt("Noto Nastaliq Urdu", "#1a2b3c"))
    c.insertBlock()
    c.insertText("پاکستان زندہ باد", fmt("Noto Nastaliq Urdu", "#b00020" if two_colours else "#1a2b3c"))
    return doc


doc = make_doc()
n_paths = sum(len(g["paths"]) for g in text_outlines.extract_groups(doc, "run"))

# ------------------------------------------------------------ structure ---
eps = exporter.render_eps_bytes(doc, W, H, QColor("#fffbe6"))
text = eps.decode("ascii")  # raises if anything non-ASCII slipped in
lines = text.split("\n")
check("pure 7-bit ASCII", True)
check("starts with the EPSF DSC magic line", lines[0] == "%!PS-Adobe-3.0 EPSF-3.0", lines[0])
check("ends with %%EOF", text.rstrip().endswith("%%EOF"))
check("no line exceeds the 255-character DSC limit", max(len(l) for l in lines) <= 255,
      str(max(len(l) for l in lines)))

bb = re.search(r"^%%BoundingBox: (-?\d+) (-?\d+) (\d+) (\d+)$", text, re.M)
hr = re.search(r"^%%HiResBoundingBox: ([\d.]+) ([\d.]+) ([\d.]+) ([\d.]+)$", text, re.M)
check("integer %%BoundingBox present", bb is not None)
check("%%HiResBoundingBox present", hr is not None)
if bb and hr:
    llx, lly, urx, ury = (int(v) for v in bb.groups())
    hllx, hlly, hurx, hury = (float(v) for v in hr.groups())
    check("bounding box starts at the origin", (llx, lly) == (0, 0))
    # 794 x 1123 px at 96 dpi = 595.5 x 842.25 pt, rounded UP to whole points
    check("hi-res box is the page size in points (px * 72/96)",
          abs(hurx - W * 0.75) < 0.01 and abs(hury - H * 0.75) < 0.01, f"{hurx} x {hury}")
    check("integer box encloses the hi-res box",
          urx >= hurx and ury >= hury and urx - hurx < 1 and ury - hury < 1, f"{urx}x{ury} vs {hurx}x{hury}")

check("gsave/grestore are balanced", len(re.findall(r"\bgsave\b", text)) == len(re.findall(r"\bgrestore\b", text)))
fills = len(re.findall(r"^(?:fill|eofill)$", text, re.M))
check("one filled path per glyph outline (same count the SVG export draws)",
      fills == n_paths and n_paths > 10, f"{fills} fills vs {n_paths} glyph paths")
check("every path is closed and begins with newpath",
      len(re.findall(r"^newpath$", text, re.M)) == fills)
check("both text colours are set", "0.102 0.1686 0.2353 setrgbcolor" in text
      and "0.6902 0 0.1255 setrgbcolor" in text)
check("no NaN/inf coordinates", not re.search(r"\b(nan|inf)\b", text, re.I))

# --------------------------------------------- background/border options ---
check("background colour is a vector rectangle", "1 0.9843 0.902 setrgbcolor\n0 0 596 843 rectfill" in text)
no_bg = exporter.render_eps_bytes(doc, W, H, None).decode()
check("background_color=None paints no background rectangle", "rectfill" not in no_bg)
check("no border unless asked for", "rectstroke" not in text)

bordered = exporter.render_eps_bytes(doc, W, H, QColor("white"), border_width=8,
                                     border_color=QColor("#335577"),
                                     border_style=Qt.PenStyle.DashLine).decode()
check("border is a stroked rectangle inset by half its width",
      "8 setlinewidth" in bordered and "4 4 786 1115 rectstroke" in bordered)
check("dashed border sets a dash pattern (4:2 of the pen width, like Qt)", "[32 16] 0 setdash" in bordered)
solid = exporter.render_eps_bytes(doc, W, H, QColor("white"), border_width=8,
                                  border_color=QColor("black")).decode()
check("solid border sets no dash pattern", "setdash" not in solid and "rectstroke" in solid)

translucent = exporter.render_eps_bytes(doc, W, H, QColor(255, 0, 0, 128)).decode()
check("a translucent colour is flattened onto white (EPS has no alpha)",
      "1 0.498 0.498 setrgbcolor" in translucent, translucent[translucent.index("rectfill") - 60:translucent.index("rectfill")])

empty = exporter.render_eps_bytes(QTextDocument(), W, H, QColor("white")).decode()
check("an empty page still produces a valid, glyph-free EPS",
      empty.startswith("%!PS-Adobe-3.0 EPSF-3.0") and "newpath" not in empty and "%%EOF" in empty)

# ------------------------------------------------------ background image ---
pic = QImage(300, 200, QImage.Format.Format_RGB32)
pic.fill(QColor("#e63946"))
p = QPainter(pic)
p.fillRect(150, 0, 150, 100, QColor("#2a9d8f"))
p.fillRect(0, 100, 150, 100, QColor("#264653"))
p.end()
for mode in ("fit", "fill", "center", "tile"):
    e = exporter.render_eps_bytes(doc, W, H, QColor("#ddeeff"), background_image=pic,
                                  background_image_mode=mode).decode("ascii")
    m = re.search(r"^image\n(.*?)~>\n", e, re.S | re.M)
    ok = m is not None
    jpeg = b""
    if ok:
        jpeg = base64.a85decode(m.group(1).replace("\n", "").encode("ascii"))
    check(f"background image ({mode}): embedded as ASCII85 + DCT (JPEG)",
          ok and jpeg[:2] == b"\xff\xd8" and jpeg[-2:] == b"\xff\xd9")
    if ok:
        decoded = QImage.fromData(jpeg)
        check(f"background image ({mode}): the embedded JPEG decodes at the declared size",
              (not decoded.isNull()) and f"/Width {decoded.width()}" in e and f"/Height {decoded.height()}" in e,
              f"{decoded.width()}x{decoded.height()}")
check("without a background image there is no raster in the file",
      "DCTDecode" not in exporter.render_eps_bytes(doc, W, H, QColor("white")).decode())

# ------------------------------------------------- file export + all-formats
tmp = tempfile.mkdtemp(prefix="utw_eps_test_")
out = os.path.join(tmp, "page.eps")
exporter.export_eps(doc, W, H, QColor("#fffbe6"), out)
check("export_eps writes a file", os.path.getsize(out) > 5000)
try:
    exporter.export_eps(doc, W, H, QColor("white"), os.path.join(tmp, "no-such-dir", "x.eps"))
    check("an unwritable path raises a readable OSError", False, "no exception")
except OSError as exc:
    check("an unwritable path raises a readable OSError", "EPS" in str(exc))

written = exporter.export_all_formats(doc, W, H, W, H, QColor("white"), os.path.join(tmp, "all.png"))
exts = sorted(Path(w).suffix for w in written)
check("Export All Formats now writes png, jpg, svg, pdf and eps",
      exts == [".eps", ".jpg", ".pdf", ".png", ".svg"], str(exts))

# ----------------------------------------------------------------- dialog ---
dlg = ExportDialog(W, H)
check("Export dialog lists EPS", "EPS" in ExportDialog.FORMATS and "EPS" in dlg.format_radios)
dlg.format_radios["EPS"].setChecked(True)
app.processEvents()
check("choosing EPS counts as a vector format (hides the pixel-size controls)",
      dlg._current_format() == "EPS" and dlg.vector_info.isVisibleTo(dlg) and not dlg.raster_widget.isVisibleTo(dlg))
check("...and the info text names it", "EPS" in dlg.vector_info.text())
check("the dialog reports EPS in its values", dlg.values()["format"] == "EPS")
check("...and explains what 'background off' means for a format with no transparency",
      "transparency" in dlg.bg_note.text() and dlg.bg_checkbox.isEnabled())
dlg.format_radios["PNG"].setChecked(True)
app.processEvents()
check("...the note goes away again for other formats", dlg.bg_note.text() == "")

# ------------------------------- the real Export button, worker thread and all
# open_export_dialog() normally blocks on two modal dialogs; stand-ins answer
# them so the real code after them (clone the page, hand it to ExportThread,
# write the file, report back) runs exactly as it does for a person.
class _FakeExportDialog:
    chosen_format = "EPS"
    all_formats = False

    def __init__(self, *a, **k):
        pass

    def exec(self):
        return True

    def values(self):
        return {"format": self.chosen_format, "width": W, "height": H, "include_background": True}

    def export_all_requested(self):
        return self.all_formats


win = main.MainWindow()
win.show()
cur = win.canvas.textCursor()
cur.insertText("اردو ٹائپ رائٹر", win.canvas._make_format())
app.processEvents()
real_dialog, real_save = main.ExportDialog, QFileDialog.getSaveFileName
win._offer_reveal_in_folder = lambda path: None  # a modal "open folder?" box
main.ExportDialog = _FakeExportDialog


def run_export(path, **flags):
    for k, v in flags.items():
        setattr(_FakeExportDialog, k, v)
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (path, ""))
    win.open_export_dialog()
    win._export_thread.wait(30000)
    app.processEvents()


e2e_dir = tempfile.mkdtemp(prefix="utw_eps_e2e_")
single = os.path.join(e2e_dir, "page.eps")
try:
    run_export(single, chosen_format="EPS", all_formats=False)
    data = open(single, "rb").read() if os.path.exists(single) else b""
    check("Export button -> EPS: the file is written by the background thread",
          data.startswith(b"%!PS-Adobe-3.0 EPSF-3.0") and b"%%EOF" in data[-20:])
    check("...with real glyph outlines in it", data.count(b"curveto") > 50, f"{data.count(b'curveto')} curveto")
    check("...and the status bar reports it", "Exported to" in win.statusBar().currentMessage(),
          win.statusBar().currentMessage())
    check("...and the Export button is usable again afterwards", win.export_menu_action.isEnabled())

    base = os.path.join(e2e_dir, "everything.png")
    run_export(base, chosen_format="PNG", all_formats=True)
    got = sorted(p.suffix for p in Path(e2e_dir).glob("everything.*"))
    check("Export All Formats through the real button writes all five files",
          got == [".eps", ".jpg", ".pdf", ".png", ".svg"], str(got))
finally:
    main.ExportDialog = real_dialog
    QFileDialog.getSaveFileName = real_save
    win.canvas.document().setModified(False)
    win.close()
shutil.rmtree(e2e_dir, ignore_errors=True)

# --------------------------------------- independent check with Ghostscript
gs = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
if not gs:
    print("[SKIP] Ghostscript not installed -- skipping the independent render comparison "
          "(not needed to run the app, only to run this extra check)")
else:
    def gs_render(path, png):
        r = subprocess.run([gs, "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-dEPSCrop", "-sDEVICE=png16m",
                            "-r96", "-dTextAlphaBits=4", "-dGraphicsAlphaBits=4", f"-sOutputFile={png}", path],
                           capture_output=True, text=True)
        return r

    def mean_abs_diff(a, b):
        # average per-channel difference over the overlapping area, 0..255
        w, h = min(a.width(), b.width()), min(a.height(), b.height())
        total = 0
        for y in range(0, h, 2):  # every other row is plenty and 2x faster
            for x in range(0, w):
                ca, cb = QColor(a.pixel(x, y)), QColor(b.pixel(x, y))
                total += abs(ca.red() - cb.red()) + abs(ca.green() - cb.green()) + abs(ca.blue() - cb.blue())
        return total / (3 * w * ((h + 1) // 2))

    png = os.path.join(tmp, "gs.png")
    r = gs_render(out, png)
    check("Ghostscript accepts the EPS with no errors", r.returncode == 0 and not r.stderr.strip(), r.stderr[:200])
    gs_img = QImage(png)
    ref = exporter._render_raster_image(doc, W, H, W, H, QColor("#fffbe6"), None, "fit", 0, None)
    check("Ghostscript's render is the page size", (gs_img.width(), gs_img.height()) == (W, H),
          f"{gs_img.width()}x{gs_img.height()}")
    d = mean_abs_diff(gs_img, ref.convertToFormat(QImage.Format.Format_RGB32))
    check("Ghostscript's render matches the app's own render of the same page (mean |diff| < 3/255)",
          d < 3, f"{d:.2f}")

    # a coloured page + dashed border + background image all at once
    full = os.path.join(tmp, "full.eps")
    exporter.export_eps(doc, W, H, QColor("#ddeeff"), full, background_image=pic, background_image_mode="fit",
                        border_width=6, border_color=QColor("#335577"), border_style=Qt.PenStyle.DashLine)
    png2 = os.path.join(tmp, "gs_full.png")
    r2 = gs_render(full, png2)
    ref2 = exporter._render_raster_image(doc, W, H, W, H, QColor("#ddeeff"), pic, "fit", 6, QColor("#335577"),
                                         Qt.PenStyle.DashLine)
    d2 = mean_abs_diff(QImage(png2), ref2.convertToFormat(QImage.Format.Format_RGB32))
    check("background image + dashed border + text together: Ghostscript renders without errors",
          r2.returncode == 0 and not r2.stderr.strip(), r2.stderr[:200])
    check("...and matches the app's own render (mean |diff| < 4/255)", d2 < 4, f"{d2:.2f}")

shutil.rmtree(tmp, ignore_errors=True)
print()
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {failures}")
    sys.exit(1)
print("ALL EPS CHECKS PASSED")
