"""
exporter.py — rendering / export logic for the Urdu Typewriter canvas.

Raster export (PNG/JPG) renders the page onto a QImage at a chosen pixel
resolution, independent of the on-screen canvas size — this is what the
export dialog's width/height steppers control.

Vector export (SVG/PDF/EPS) renders the page at its defined page size only.
"Resolution" doesn't apply to vector output: the page is redrawn from its
underlying paths/text every time it's opened or scaled, so it's crisp at
any zoom level or print size without needing a pixel dimension at all.

Background image and border painting (_paint_background_image/_paint_border
below) intentionally duplicate canvas.py's on-screen versions rather than
sharing a module: the two work in different coordinate contexts (a live
widget's viewport vs. a page-unit painter that may or may not be scaled
for raster export), and the small amount of duplication reads more clearly
here than a shared helper abstracted over both. Keep them in sync if you
change one — the "fit/fill/center/tile" behavior is meant to look
identical on screen and in every exported format.
"""

import base64
import datetime
import math
import sys

from PyQt6.QtCore import (
    Qt,
    QSize,
    QRect,
    QRectF,
    QSizeF,
    QMarginsF,
    QBuffer,
    QIODevice,
    QMimeData,
    QByteArray,
)
from PyQt6.QtGui import (
    QImage,
    QPainter,
    QPdfWriter,
    QPageSize,
    QColor,
    QPixmap,
    QPen,
    QPainterPath,
)
from PyQt6.QtSvg import QSvgGenerator

import font_gap
import text_outlines


def _layout_copy(document, page_width):
    """Clone the document and lock its wrap width to the page width, so
    exported text wraps exactly like it does on screen."""
    doc = document.clone()
    # The experimental gap between words in different fonts lives in each
    # block's *layout*, which clone() doesn't copy -- so re-derive it here, or
    # every export/copy/print would silently lack what the screen shows.
    # (No-op when the feature is Off.) See font_gap.py.
    font_gap.apply_document(doc)
    doc.setTextWidth(page_width)
    return doc


def _paint_background(painter, page_width, page_height, background_color):
    """background_color=None means "don't draw a background at all"
    (transparent for raster; simply omitted for vector)."""
    if background_color is None:
        return
    painter.fillRect(0, 0, int(page_width), int(page_height), background_color)


def _paint_background_image(painter, page_width, page_height, image, mode):
    """image: a QImage, or None to skip (the common case). See canvas.py's
    PageCanvas._paint_background_image() — same modes, same math, kept in
    sync deliberately (see the module docstring)."""
    if image is None or image.isNull():
        return
    iw, ih = image.width(), image.height()
    if iw <= 0 or ih <= 0:
        return
    rect = QRectF(0, 0, page_width, page_height)
    if mode == "tile":
        painter.drawTiledPixmap(rect, QPixmap.fromImage(image))
        return
    if mode == "center":
        x = (page_width - iw) / 2
        y = (page_height - ih) / 2
        painter.drawImage(QRectF(x, y, iw, ih), image)
        return
    scale = (max if mode == "fill" else min)(page_width / iw, page_height / ih)
    dw, dh = iw * scale, ih * scale
    target = QRectF((page_width - dw) / 2, (page_height - dh) / 2, dw, dh)
    if mode == "fill":
        painter.save()
        painter.setClipRect(rect)
        painter.drawImage(target, image)
        painter.restore()
    else:
        painter.drawImage(target, image)


def _paint_border(painter, page_width, page_height, width_px, color, style=None):
    """width_px=0 (or falsy) means "no border" -- the common case.
    style: a Qt.PenStyle (SolidLine/DashLine/DotLine/DashDotLine/...),
    defaulting to solid."""
    if not width_px or width_px <= 0:
        return
    painter.save()
    pen = QPen(QColor(color) if color else QColor("black"), width_px)
    pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
    pen.setStyle(style if style is not None else Qt.PenStyle.SolidLine)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    half = width_px / 2
    painter.drawRect(QRectF(half, half, page_width - width_px, page_height - width_px))
    painter.restore()


def _render_raster_image(
    document,
    page_width,
    page_height,
    out_width,
    out_height,
    background_color,
    background_image,
    background_image_mode,
    border_width,
    border_color,
    border_style=None,
):
    doc = _layout_copy(document, page_width)

    image = QImage(out_width, out_height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(out_width / page_width, out_height / page_height)
    _paint_background(painter, page_width, page_height, background_color)
    _paint_background_image(
        painter, page_width, page_height, background_image, background_image_mode
    )
    doc.drawContents(painter)
    _paint_border(
        painter, page_width, page_height, border_width, border_color, border_style
    )
    painter.end()
    return image


def export_raster(
    document,
    page_width,
    page_height,
    out_width,
    out_height,
    background_color,
    file_path,
    image_format,
    jpg_quality=95,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
):
    """background_color: QColor to paint behind the text, or None for a
    transparent background (PNG only — JPG has no alpha channel)."""
    image = _render_raster_image(
        document,
        page_width,
        page_height,
        out_width,
        out_height,
        background_color,
        background_image,
        background_image_mode,
        border_width,
        border_color,
        border_style,
    )

    fmt = image_format.upper()
    if fmt in ("JPG", "JPEG"):
        if background_color is None:
            # JPG has no alpha channel — flatten onto white rather than
            # silently turning transparency into black.
            flat = QImage(image.size(), QImage.Format.Format_RGB32)
            flat.fill(Qt.GlobalColor.white)
            p2 = QPainter(flat)
            p2.drawImage(0, 0, image)
            p2.end()
            image = flat
        ok = image.save(file_path, "JPG", jpg_quality)
    else:
        ok = image.save(file_path, "PNG")
    if not ok:
        # QImage.save() fails silently (just returns False) rather than
        # raising — an invalid folder, a read-only location, or a full
        # disk would otherwise look identical to a successful export.
        raise OSError(
            f"Couldn't write the image file — check that the folder exists "
            f"and is writable, and that there's disk space free.\n\n{file_path}"
        )


def render_for_clipboard(
    document,
    page_width,
    page_height,
    background_color,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
    scale=2.0,
):
    """Same rendering pipeline as export_raster(), returned as a QImage
    instead of written to a file — main.py puts this straight on the
    system clipboard for "Copy as Image". Defaults to a 2x page-size
    render: sharp enough to paste into a chat app or document without
    needing to ask for a resolution first, the way a real export does."""
    return _render_raster_image(
        document,
        page_width,
        page_height,
        round(page_width * scale),
        round(page_height * scale),
        background_color,
        background_image,
        background_image_mode,
        border_width,
        border_color,
        border_style,
    )


def _paint_text_as_outlines(painter, doc):
    """Paint every glyph in `doc` as a filled vector path instead of a live
    text run, via text_outlines.extract_groups().

    Qt's SVG generator normally writes <text font-family="..."> elements,
    which only render correctly on a machine that also has that exact font
    installed. For a font like Noto Nastaliq Urdu that's an unreasonable
    thing to depend on, so instead we paint pre-extracted outlines
    directly. The SVG that results is self-contained: it looks identical
    anywhere, with no font installed.
    """
    for group in text_outlines.extract_groups(doc, granularity="run"):
        for path in group["paths"]:
            painter.fillPath(path, group["color"])


def render_svg_bytes(
    document,
    page_width,
    page_height,
    background_color,
    title="Urdu Typewriter",
    dpi=96,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
) -> bytes:
    """Same rendering as export_svg() below, but returned as bytes instead
    of written to a file -- shared by export_svg() (which just writes the
    result to file_path) and main.py's "Copy as SVG" (which puts it on
    the clipboard instead, via a QBuffer standing in for a real file)."""
    doc = _layout_copy(document, page_width)

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)

    generator = QSvgGenerator()
    generator.setOutputDevice(buffer)
    generator.setResolution(
        dpi
    )  # keeps the SVG's physical size sane (px-like) instead of Qt's default ~72dpi-derived mm size
    generator.setSize(QSize(int(page_width), int(page_height)))
    generator.setViewBox(QRect(0, 0, int(page_width), int(page_height)))
    generator.setTitle(title)
    generator.setDescription("Generated by Urdu Typewriter")

    painter = QPainter()
    if not painter.begin(generator):
        raise OSError("Couldn't generate the SVG.")
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    _paint_background(painter, page_width, page_height, background_color)
    _paint_background_image(
        painter, page_width, page_height, background_image, background_image_mode
    )
    _paint_text_as_outlines(painter, doc)
    _paint_border(
        painter, page_width, page_height, border_width, border_color, border_style
    )
    painter.end()

    return bytes(buffer.data())


def render_svg_curves_bytes(
    document, page_width, char_range=None, title="Urdu Typewriter", dpi=96
) -> bytes:
    """Curves-only SVG for main.py's "Copy as SVG (Curves)": just the
    glyph outlines, nothing else.

    render_svg_bytes() above is a whole-page export -- the SVG is always
    the full page size, with the page background and border painted in,
    so a few words near one corner arrive in a design app as a big mostly
    empty rectangle. Pasting into Affinity/Illustrator/Inkscape wants the
    opposite: only the letterforms, as editable curves, sitting in a
    canvas exactly as big as they are. So this version:

    - restricts itself to `char_range` -- an absolute (start, end) pair
      like QTextCursor.selectionStart()/selectionEnd() -- when given, so a
      selection copies only the selected text; None copies every glyph;
    - sizes the SVG (both its size and viewBox) to the union of those
      glyphs' bounding boxes and shifts every path to sit inside that
      frame, rather than the full page; and
    - skips the page background, background image and border entirely.

    The outlines themselves are extracted and filled exactly the way
    render_svg_bytes() does it (same text_outlines.extract_groups() call
    at "run" granularity, same fillPath per glyph), so the curves match a
    file export point for point -- only the frame around them differs.

    Raises ValueError if there's nothing to draw (an empty page, or a
    selection containing only spaces and line breaks), so the caller can
    say so instead of putting a blank SVG on the clipboard.
    """
    doc = _layout_copy(document, page_width)
    groups = text_outlines.extract_groups(doc, granularity="run", char_range=char_range)
    if not groups:
        raise ValueError("There's no text to copy as curves.")

    bbox = groups[0]["bbox"]
    for group in groups[1:]:
        bbox = bbox.united(group["bbox"])

    # Whole-pixel frame, with the viewBox identical to the size so nothing
    # gets rescaled or letterboxed when the SVG is placed; rounding up
    # leaves at most a pixel of empty room on the right/bottom edge.
    width = max(1, math.ceil(bbox.width()))
    height = max(1, math.ceil(bbox.height()))

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)

    generator = QSvgGenerator()
    generator.setOutputDevice(buffer)
    generator.setResolution(dpi)
    generator.setSize(QSize(width, height))
    generator.setViewBox(QRect(0, 0, width, height))
    generator.setTitle(title)
    generator.setDescription("Generated by Urdu Typewriter")

    painter = QPainter()
    if not painter.begin(generator):
        raise OSError("Couldn't generate the SVG.")
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    # Shift the paths themselves (rather than the painter) into the
    # cropped frame, so the SVG holds plain absolute coordinates on every
    # curve instead of a transform attribute per shape -- cleaner to edit
    # once it's pasted into a design app.
    dx, dy = -bbox.x(), -bbox.y()
    for group in groups:
        for path in group["paths"]:
            painter.fillPath(path.translated(dx, dy), group["color"])
    painter.end()

    return bytes(buffer.data())


def render_pdf_curves_bytes(document, page_width, char_range=None, dpi=96) -> bytes:
    """PDF twin of render_svg_curves_bytes() above: same selection-aware,
    cropped-to-the-glyphs curves, same outline extraction and fillPath
    calls, just written through QPdfWriter into an in-memory PDF instead
    of QSvgGenerator into an in-memory SVG.

    Exists because "Copy as SVG (Curves)" puts *both* an SVG and a PDF of
    the curves on the clipboard (see main.py's copy_as_svg): Affinity in
    particular does not read arbitrary external SVG text pasted onto its
    clipboard as editable curves at all (per Affinity's own forum, it
    rasterizes it, if it accepts it as anything); a PDF on the clipboard
    is the interop path several vector apps, Affinity included, are
    independently reported to actually read as real vector data. Offering
    both costs nothing -- a receiving app takes whichever format it
    understands, and both are genuine vector data, so there's no "wrong
    one wins" risk the way there was with a raster fallback.

    Raises ValueError under the same conditions as render_svg_curves_bytes
    (nothing to draw).
    """
    doc = _layout_copy(document, page_width)
    groups = text_outlines.extract_groups(doc, granularity="run", char_range=char_range)
    if not groups:
        raise ValueError("There's no text to copy as curves.")

    bbox = groups[0]["bbox"]
    for group in groups[1:]:
        bbox = bbox.united(group["bbox"])

    width_pt = bbox.width() * 72.0 / dpi
    height_pt = bbox.height() * 72.0 / dpi

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)

    writer = QPdfWriter(buffer)
    writer.setResolution(dpi)
    writer.setPageSize(
        QPageSize(
            QSizeF(max(0.01, width_pt), max(0.01, height_pt)), QPageSize.Unit.Point
        )
    )
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))

    painter = QPainter()
    if not painter.begin(writer):
        raise OSError("Couldn't generate the PDF.")
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    dx, dy = -bbox.x(), -bbox.y()
    for group in groups:
        for path in group["paths"]:
            painter.fillPath(path.translated(dx, dy), group["color"])
    painter.end()

    return bytes(buffer.data())


# The clipboard format name Affinity Designer registers for PDF data on
# *Windows* (and, per Inkscape's issue tracker, the convention among Windows
# vector editors -- some programs will not accept "application/pdf" at all).
# Windows clipboard formats are matched by exact registered name, and the
# MIME-style "application/pdf" is a different name, so an app that only looks
# for this one doesn't see a PDF placed under the other. That mismatch is the
# most likely reason "Copy as Curves" pasted nothing at all into Affinity
# (SVG + "application/pdf" were the only formats on the clipboard); it could
# not be tested against Affinity itself from the sandbox this was built in.
# Qt registers an arbitrary Windows clipboard format name when the MIME type
# is spelled application/x-qt-windows-mime;value="<name>" (documented under
# QWindowsMimeConverter::registerMimeType).
WINDOWS_PDF_CLIPBOARD_NAME = "Portable Document Format"


def make_curves_mime_data(svg_bytes, pdf_bytes, platform=None) -> QMimeData:
    """The clipboard payload for "Copy as SVG (Curves)": the same cropped
    curves as real vector data in every format a receiving app might look
    for, and deliberately nothing raster (a design app offered a bitmap next
    to the vectors can quietly take the bitmap -- the original pixelation
    bug).

    Order matters: Windows reports clipboard formats to the receiving app in
    the order they were added, and apps tend to take the first one they
    understand.

      1. (Windows only) PDF under its Windows registered name, "Portable
         Document Format" -- what Affinity and other Windows vector apps
         actually look for (see WINDOWS_PDF_CLIPBOARD_NAME).
      2. image/svg+xml -- SVG for apps that read it (Inkscape, browsers,
         most web/design tools; also macOS's SVG pasteboard type).
      3. application/pdf -- the same PDF under its MIME name, which is the
         one macOS (com.adobe.pdf) and Linux apps look for.

    `platform` is injectable (default sys.platform) so the Windows-specific
    entry can be tested from any OS.
    """
    platform = sys.platform if platform is None else platform
    mime = QMimeData()
    if platform.startswith("win"):
        mime.setData(
            f'application/x-qt-windows-mime;value="{WINDOWS_PDF_CLIPBOARD_NAME}"',
            QByteArray(pdf_bytes),
        )
    mime.setData("image/svg+xml", QByteArray(svg_bytes))
    mime.setData("application/pdf", QByteArray(pdf_bytes))
    return mime


def export_svg(
    document,
    page_width,
    page_height,
    background_color,
    file_path,
    title="Urdu Typewriter",
    dpi=96,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
):
    data = render_svg_bytes(
        document,
        page_width,
        page_height,
        background_color,
        title=title,
        dpi=dpi,
        background_image=background_image,
        background_image_mode=background_image_mode,
        border_width=border_width,
        border_color=border_color,
        border_style=border_style,
    )
    try:
        with open(file_path, "wb") as f:
            f.write(data)
    except OSError:
        # Same failure modes export_raster() guards against (bad folder,
        # read-only location, full disk) -- give the same clear message
        # rather than whatever raw OSError text the open()/write() above
        # would otherwise surface.
        raise OSError(
            f"Couldn't write the SVG file — check that the folder exists "
            f"and is writable.\n\n{file_path}"
        )


def export_pdf(
    document,
    page_width,
    page_height,
    background_color,
    file_path,
    dpi=96,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
):
    doc = _layout_copy(document, page_width)

    writer = QPdfWriter(file_path)
    writer.setResolution(dpi)
    width_pt = page_width * 72.0 / dpi
    height_pt = page_height * 72.0 / dpi
    writer.setPageSize(QPageSize(QSizeF(width_pt, height_pt), QPageSize.Unit.Point))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0))

    painter = QPainter()
    if not painter.begin(writer):
        raise OSError(
            f"Couldn't write the PDF file — check that the folder exists "
            f"and is writable.\n\n{file_path}"
        )
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    # QPdfWriter's painter operates in device pixels at `dpi`, so page_width/
    # page_height px map straight onto the page with no extra scaling needed.
    _paint_background(painter, page_width, page_height, background_color)
    _paint_background_image(
        painter, page_width, page_height, background_image, background_image_mode
    )
    doc.drawContents(painter)
    _paint_border(
        painter, page_width, page_height, border_width, border_color, border_style
    )
    painter.end()


# ------------------------------------------------------------------- EPS ---
# Encapsulated PostScript, written directly (no Ghostscript or any other
# dependency needed on the machine running the app). Level 2 PostScript with
# plain operators only -- no procedure definitions or shorthand -- so even a
# simple EPS importer that parses rather than interprets can read it.
#
# Everything text-related is vector: each glyph outline from
# text_outlines.extract_groups() becomes one closed path filled with the run's
# colour, exactly like the SVG export. The page background colour is a vector
# rectangle, the border a vector stroke. Only a background *image* is raster
# (a JPEG embedded with the DCTDecode filter), because that's what it is.
#
# PostScript's origin is bottom-left with y pointing up and its unit is the
# point; the page here is top-left, y-down, in pixels at `dpi`. So after the
# background is painted, one transform (move to the top edge, scale by
# 72/dpi, flip y) lets every following coordinate be written in the page's
# own pixel space with no per-point arithmetic.

# Qt pen dash patterns, in multiples of the pen width (see QPen docs); kept
# here so the EPS border dashes like the on-screen and SVG ones do.
_EPS_DASH_PATTERNS = {
    Qt.PenStyle.DashLine: (4, 2),
    Qt.PenStyle.DotLine: (1, 2),
    Qt.PenStyle.DashDotLine: (4, 2, 1, 2),
    Qt.PenStyle.DashDotDotLine: (4, 2, 1, 2, 1, 2),
}


def _eps_num(value) -> str:
    """Compact PostScript number: up to 3 decimals, no trailing zeros, no
    '-0'."""
    text = f"{float(value):.3f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def _eps_rgb(color) -> str:
    """'r g b setrgbcolor' for a QColor. EPS has no transparency, so a
    translucent colour is flattened onto white, which is what it would look
    like on the (white) page anyway."""
    c = QColor(color)
    a = c.alphaF()
    channels = [a * ch + (1.0 - a) for ch in (c.redF(), c.greenF(), c.blueF())]
    return (
        " ".join(f"{v:.4f}".rstrip("0").rstrip(".") or "0" for v in channels)
        + " setrgbcolor"
    )


def _eps_path_ops(path):
    """PostScript path-construction operators for one QPainterPath, in its
    own coordinate space. QPainterPath only ever holds move/line/cubic
    elements (a quadratic TrueType curve is stored as an equivalent cubic),
    which map one-to-one onto moveto/lineto/curveto. Each subpath is closed
    explicitly: fill would close it implicitly anyway, but an importer such
    as Affinity or Illustrator then shows the outlines as closed shapes
    rather than open ones with a fill."""
    ops = []
    n = path.elementCount()
    i = 0
    open_subpath = False
    move_to = QPainterPath.ElementType.MoveToElement
    line_to = QPainterPath.ElementType.LineToElement
    curve_to = QPainterPath.ElementType.CurveToElement
    while i < n:
        e = path.elementAt(i)
        if e.type == move_to:
            if open_subpath:
                ops.append("closepath")
            ops.append(f"{_eps_num(e.x)} {_eps_num(e.y)} moveto")
            open_subpath = True
            i += 1
        elif e.type == line_to:
            ops.append(f"{_eps_num(e.x)} {_eps_num(e.y)} lineto")
            i += 1
        elif e.type == curve_to and i + 2 < n:
            c2 = path.elementAt(i + 1)
            end = path.elementAt(i + 2)
            ops.append(
                f"{_eps_num(e.x)} {_eps_num(e.y)} {_eps_num(c2.x)} {_eps_num(c2.y)} "
                f"{_eps_num(end.x)} {_eps_num(end.y)} curveto"
            )
            i += 3
        else:
            i += 1  # stray data element -- nothing sensible to emit
    if open_subpath:
        ops.append("closepath")
    return ops


def _eps_ascii(text, fallback="Untitled") -> str:
    """DSC header comments are 7-bit ASCII, single line."""
    clean = " ".join(str(text).split()).encode("ascii", "replace").decode("ascii")
    return clean or fallback


def _eps_background_image_lines(
    page_width,
    page_height,
    width_pt,
    height_pt,
    background_color,
    background_image,
    mode,
    max_edge=3000,
):
    """PostScript lines placing the page's background -- colour plus image,
    composed by the same _paint_background()/_paint_background_image() the
    other exports use, so fit/fill/center/tile look identical -- as a single
    full-page JPEG. Rendered at up to 2x (so it stays crisp when scaled up a
    little) but capped to `max_edge` pixels on the long side to keep the file
    a sensible size."""
    scale = min(2.0, max_edge / max(page_width, page_height))
    w = max(1, round(page_width * scale))
    h = max(1, round(page_height * scale))
    flat = QImage(w, h, QImage.Format.Format_RGB32)
    flat.fill(Qt.GlobalColor.white)  # EPS can't be transparent
    painter = QPainter(flat)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(w / page_width, h / page_height)
    _paint_background(painter, page_width, page_height, background_color)
    _paint_background_image(painter, page_width, page_height, background_image, mode)
    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not flat.save(buf, "JPG", 92):
        raise OSError("Couldn't encode the background image for the EPS.")
    # ASCII85 (Level 2), wrapped well under the 255-character DSC line limit.
    encoded = base64.a85encode(bytes(buf.data()), wrapcol=76).decode("ascii")
    return [
        "gsave",
        f"{_eps_num(width_pt)} {_eps_num(height_pt)} scale",
        "/DeviceRGB setcolorspace",
        "<<",
        "/ImageType 1",
        f"/Width {w}",
        f"/Height {h}",
        "/BitsPerComponent 8",
        "/Decode [0 1 0 1 0 1]",
        f"/ImageMatrix [{w} 0 0 -{h} 0 {h}]",
        "/DataSource currentfile /ASCII85Decode filter /DCTDecode filter",
        ">>",
        "image",
        encoded + "~>",
        "grestore",
    ]


def render_eps_bytes(
    document,
    page_width,
    page_height,
    background_color,
    title="Urdu Typewriter",
    dpi=96,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
) -> bytes:
    """The whole page as an EPS file's bytes: same inputs and same content
    as render_svg_bytes() (page background, optional background image,
    every glyph as a filled outline, optional border), so an EPS export
    matches the SVG and PDF ones. background_color=None leaves the
    background unpainted (white, since EPS has no transparency)."""
    doc = _layout_copy(document, page_width)
    groups = text_outlines.extract_groups(doc, granularity="run")

    k = 72.0 / dpi  # page px -> PostScript points
    width_pt = page_width * k
    height_pt = page_height * k
    bb_w = max(1, math.ceil(width_pt))
    bb_h = max(1, math.ceil(height_pt))

    out = [
        "%!PS-Adobe-3.0 EPSF-3.0",
        "%%Creator: Urdu Typewriter",
        f"%%Title: {_eps_ascii(title)}",
        f"%%CreationDate: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"%%BoundingBox: 0 0 {bb_w} {bb_h}",
        f"%%HiResBoundingBox: 0 0 {_eps_num(width_pt)} {_eps_num(height_pt)}",
        "%%LanguageLevel: 2",
        "%%DocumentData: Clean7Bit",
        "%%Pages: 1",
        "%%EndComments",
        "%%BeginProlog",
        "%%EndProlog",
        "%%Page: 1 1",
        "gsave",
    ]

    # -- background (native PostScript space, so it can reach the whole
    # integer bounding box -- the page is rarely a whole number of points,
    # and a sliver of unpainted edge would show on a coloured page).
    has_image = background_image is not None and not background_image.isNull()
    if has_image:
        if background_color is not None:
            out.append(_eps_rgb(background_color))
            out.append(f"0 0 {bb_w} {bb_h} rectfill")
        out += _eps_background_image_lines(
            page_width,
            page_height,
            width_pt,
            height_pt,
            background_color,
            background_image,
            background_image_mode,
        )
    elif background_color is not None:
        out.append(_eps_rgb(background_color))
        out.append(f"0 0 {bb_w} {bb_h} rectfill")

    # -- from here on: page pixels, origin top-left, y down.
    out.append(f"0 {_eps_num(height_pt)} translate {_eps_num(k)} {_eps_num(-k)} scale")

    # -- text: one filled, closed path per glyph outline.
    for group in groups:
        out.append(_eps_rgb(group["color"]))
        for path in group["paths"]:
            ops = _eps_path_ops(path)
            if not ops:
                continue
            out.append("newpath")
            out.extend(ops)
            # Respect the path's own fill rule, like the SVG export does.
            out.append(
                "eofill" if path.fillRule() == Qt.FillRule.OddEvenFill else "fill"
            )

    # -- border: a stroked rectangle inset by half its width, as on screen.
    if border_width and border_width > 0:
        w = float(border_width)
        out.append("gsave")
        out.append(_eps_rgb(border_color if border_color else QColor("black")))
        out.append(f"{_eps_num(w)} setlinewidth 0 setlinejoin 2 setlinecap")
        pattern = _EPS_DASH_PATTERNS.get(border_style)
        if pattern:
            out.append("[" + " ".join(_eps_num(p * w) for p in pattern) + "] 0 setdash")
        out.append(
            f"{_eps_num(w / 2)} {_eps_num(w / 2)} "
            f"{_eps_num(page_width - w)} {_eps_num(page_height - w)} rectstroke"
        )
        out.append("grestore")

    out += ["grestore", "showpage", "%%Trailer", "%%EOF", ""]
    return "\n".join(out).encode("ascii")


def export_eps(
    document,
    page_width,
    page_height,
    background_color,
    file_path,
    title="Urdu Typewriter",
    dpi=96,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
):
    data = render_eps_bytes(
        document,
        page_width,
        page_height,
        background_color,
        title=title,
        dpi=dpi,
        background_image=background_image,
        background_image_mode=background_image_mode,
        border_width=border_width,
        border_color=border_color,
        border_style=border_style,
    )
    try:
        with open(file_path, "wb") as f:
            f.write(data)
    except OSError:
        raise OSError(
            f"Couldn't write the EPS file — check that the folder exists "
            f"and is writable.\n\n{file_path}"
        )


def paint_page_for_printer(
    painter,
    document,
    page_width,
    page_height,
    background_color,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
    scale=1.0,
):
    """Paints the page onto an already-open painter (a QPrinter's, from
    main.py's Print handler) at `scale` device pixels per page pixel --
    so a printed page is rendered by exactly the same code as every file
    export, rather than a fourth, separately-maintained painting path."""
    doc = _layout_copy(document, page_width)
    painter.save()
    painter.scale(scale, scale)
    _paint_background(painter, page_width, page_height, background_color)
    _paint_background_image(
        painter, page_width, page_height, background_image, background_image_mode
    )
    doc.drawContents(painter)
    _paint_border(
        painter, page_width, page_height, border_width, border_color, border_style
    )
    painter.restore()


def export_all_formats(
    document,
    page_width,
    page_height,
    out_width,
    out_height,
    background_color,
    base_path,
    background_image=None,
    background_image_mode="fit",
    border_width=0,
    border_color=None,
    border_style=None,
    jpg_quality=95,
    formats=("PNG", "JPG", "SVG", "PDF", "EPS"),
):
    """Exports the same page to every format in `formats` in one call, each
    named from `base_path` with its own extension swapped in (a base_path
    of ".../design.png" produces design.png, design.jpg, design.svg,
    design.pdf, design.eps alongside each other). Returns the list of paths actually
    written, in order — main.py's "Export All Formats" uses this so a
    batch export is one call instead of one hand-written per-format loop
    each caller would otherwise have to keep in sync with the single-format
    functions above.
    """
    import os

    stem = os.path.splitext(base_path)[0]
    written = []
    for fmt in formats:
        path = f"{stem}.{fmt.lower()}"
        if fmt in ("PNG", "JPG", "JPEG"):
            export_raster(
                document,
                page_width,
                page_height,
                out_width,
                out_height,
                background_color,
                path,
                fmt,
                jpg_quality=jpg_quality,
                background_image=background_image,
                background_image_mode=background_image_mode,
                border_width=border_width,
                border_color=border_color,
                border_style=border_style,
            )
        elif fmt == "SVG":
            export_svg(
                document,
                page_width,
                page_height,
                background_color,
                path,
                background_image=background_image,
                background_image_mode=background_image_mode,
                border_width=border_width,
                border_color=border_color,
                border_style=border_style,
            )
        elif fmt == "PDF":
            export_pdf(
                document,
                page_width,
                page_height,
                background_color,
                path,
                background_image=background_image,
                background_image_mode=background_image_mode,
                border_width=border_width,
                border_color=border_color,
                border_style=border_style,
            )
        elif fmt == "EPS":
            export_eps(
                document,
                page_width,
                page_height,
                background_color,
                path,
                background_image=background_image,
                background_image_mode=background_image_mode,
                border_width=border_width,
                border_color=border_color,
                border_style=border_style,
            )
        else:
            continue
        written.append(path)
    return written
