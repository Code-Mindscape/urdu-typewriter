"""
project_io.py — save/load for the app's own project format (.utw).

This is the "always be able to edit what you made" file, the same idea as
an Illustrator .ai file: a JSON document that captures the page settings
and every run of text with its exact family/style/size/color, so reopening
it reconstructs the document exactly rather than reflowing it as plain
text.

Deliberately does NOT use QTextDocument.toHtml()/setHtml() for this: HTML's
font-weight model only has a bold/not-bold flag, so round-tripping through
it silently drops a font's specific *style name* (e.g. "Medium" or
"SemiBold" collapse to nothing) — exactly the distinction the app exists to
preserve. Instead we walk the document's blocks and fragments directly and
rebuild it the same way on load, using explicit QTextCharFormat objects.

A background image is embedded directly in the file (base64-encoded PNG),
not saved as a path reference — a path would silently break the moment the
image file is moved, renamed, or the .utw is shared with anyone else,
which runs against the entire point of this format being a single
self-contained "everything needed to reconstruct this exactly" file.
"""

import base64
import json

from PyQt6.QtCore import Qt, QBuffer, QIODevice
from PyQt6.QtGui import QTextCursor, QTextCharFormat, QTextBlockFormat, QFont, QColor, QBrush, QImage

FORMAT_VERSION = 4
EXTENSION = "utw"
FILE_FILTER = "Urdu Typewriter Project (*.utw)"


class ProjectLoadError(Exception):
    """Raised for problems specific to reading a .utw file, with a message
    written for the person using the app rather than a raw exception
    string — main.py shows this text directly in the error dialog."""

_ALIGN_TO_STR = {
    Qt.AlignmentFlag.AlignRight: "right",
    Qt.AlignmentFlag.AlignHCenter: "center",
    Qt.AlignmentFlag.AlignCenter: "center",
    Qt.AlignmentFlag.AlignLeft: "left",
}
_STR_TO_ALIGN = {
    "right": Qt.AlignmentFlag.AlignRight,
    "center": Qt.AlignmentFlag.AlignCenter,
    "left": Qt.AlignmentFlag.AlignLeft,
}
_BORDER_STYLE_TO_STR = {
    Qt.PenStyle.SolidLine: "solid",
    Qt.PenStyle.DashLine: "dash",
    Qt.PenStyle.DotLine: "dot",
    Qt.PenStyle.DashDotLine: "dashdot",
}
_STR_TO_BORDER_STYLE = {v: k for k, v in _BORDER_STYLE_TO_STR.items()}


def _safe_int(value, default):
    """A hand-edited or corrupted .utw file can have a field of the wrong
    type (a string where a number was expected, etc.) — this keeps one
    bad field from failing the whole load partway through, after the
    canvas has already been cleared to start rebuilding it."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_color(value, default):
    color = QColor(value) if isinstance(value, str) else QColor()
    if color.isValid():
        return color
    return QColor(default) if default is not None else None


def _encode_image(image):
    """QImage -> base64 PNG string, or None if there's no image to encode."""
    if image is None or image.isNull():
        return None
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return base64.b64encode(bytes(buf.data())).decode("ascii")


def _decode_image(value):
    """The inverse of _encode_image(), tolerant of a missing, malformed,
    or non-image value the same way _safe_color()/_safe_int() are —
    returns None rather than raising, so a corrupted image field can't
    fail an otherwise-fine load."""
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        return None
    image = QImage()
    if not image.loadFromData(raw, "PNG"):
        return None
    return image


def _safe_str(value, default):
    return value if isinstance(value, str) else default


def _safe_float(value, default):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return default


def _serialize_blocks(document):
    blocks = []
    block = document.begin()
    while block.isValid():
        align = _ALIGN_TO_STR.get(block.blockFormat().alignment(), "right")
        line_height = block.blockFormat().lineHeight()
        runs = []
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.length() > 0:
                fmt = frag.charFormat()
                font = fmt.font()
                bg = fmt.background()
                highlight = bg.color().name() if bg.style() != Qt.BrushStyle.NoBrush else None
                if font.letterSpacingType() == QFont.SpacingType.PercentageSpacing:
                    letter_spacing = round(font.letterSpacing())
                else:
                    letter_spacing = 100
                runs.append({
                    "text": frag.text(),
                    "family": font.family(),
                    "style": font.styleName(),
                    "size": font.pointSize() if font.pointSize() > 0 else 12,
                    "color": fmt.foreground().color().name(),
                    "underline": fmt.fontUnderline(),
                    "strikeout": fmt.fontStrikeOut(),
                    "highlight": highlight,
                    "letter_spacing": letter_spacing,
                    "word_spacing": round(font.wordSpacing(), 2),
                })
            it += 1
        blocks.append({
            "alignment": align,
            "line_spacing": round(line_height) if line_height > 0 else 100,
            "runs": runs,
        })
        block = block.next()
    return blocks


def _deserialize_blocks(document, blocks_data):
    document.clear()
    cursor = QTextCursor(document)
    for bi, block_data in enumerate(blocks_data):
        if not isinstance(block_data, dict):
            continue  # skip a malformed block rather than fail the whole load
        if bi > 0:
            cursor.insertBlock()
        bf = cursor.blockFormat()
        bf.setAlignment(_STR_TO_ALIGN.get(block_data.get("alignment"), Qt.AlignmentFlag.AlignRight))
        line_spacing = _safe_int(block_data.get("line_spacing"), 100)
        bf.setLineHeight(float(line_spacing if line_spacing > 0 else 100),
                          QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.setBlockFormat(bf)
        for run in block_data.get("runs", []):
            if not isinstance(run, dict):
                continue  # same — skip rather than abort
            fmt = QTextCharFormat()
            font = QFont(_safe_str(run.get("family"), "Noto Nastaliq Urdu"))
            style = run.get("style")
            if isinstance(style, str) and style:
                font.setStyleName(style)
            font.setPointSize(max(1, _safe_int(run.get("size"), 12)))
            letter_spacing = _safe_int(run.get("letter_spacing"), 100)
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing,
                                   letter_spacing if letter_spacing > 0 else 100)
            font.setWordSpacing(_safe_float(run.get("word_spacing"), 0.0))
            fmt.setFont(font)
            fmt.setForeground(_safe_color(run.get("color"), "#000000"))
            fmt.setFontUnderline(bool(run.get("underline", False)))
            fmt.setFontStrikeOut(bool(run.get("strikeout", False)))
            highlight = _safe_color(run.get("highlight"), None)
            if highlight is not None:
                fmt.setBackground(QBrush(highlight))
            cursor.insertText(_safe_str(run.get("text"), ""), fmt)


def save_project(path, canvas):
    data = {
        "version": FORMAT_VERSION,
        "page": {
            "width": canvas.page_width,
            "height": canvas.page_height,
            "margin": canvas.document().documentMargin(),
            "background_color": canvas.background_color().name(),
            "background_image": _encode_image(canvas.background_image()),
            "background_image_mode": canvas.background_image_mode(),
            "border_width": canvas.border_width(),
            "border_color": canvas.border_color().name(),
            "border_style": _BORDER_STYLE_TO_STR.get(canvas.border_style(), "solid"),
            "vertical_align": canvas.vertical_align(),
        },
        "pending": {
            "family": canvas.pending_family(),
            "style": canvas.pending_style(),
            "size": canvas.pending_size(),
            "color": canvas.pending_color().name(),
            "word_spacing": round(canvas.pending_word_spacing(), 2),
        },
        "blocks": _serialize_blocks(canvas.document()),
    }
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except PermissionError as e:
        raise ProjectLoadError(
            f"Couldn't save here — permission denied.\n\n{path}"
        ) from e
    except OSError as e:
        raise ProjectLoadError(
            f"Couldn't save the project (disk full, or an invalid location?).\n\n{path}\n\n{e}"
        ) from e


def load_project(path):
    """Returns a dict with keys: page, pending, blocks. Caller applies this
    to a canvas + toolbar — project_io stays UI-agnostic. Raises
    ProjectLoadError, with a message meant to be shown to the person
    directly, for anything wrong with the file itself; validates the
    overall shape before returning so a caller can trust the result enough
    to start applying it to a live canvas without needing to re-check
    types at every step."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError as e:
        raise ProjectLoadError(f"That file no longer exists:\n\n{path}") from e
    except PermissionError as e:
        raise ProjectLoadError(f"Couldn't open this file — permission denied.\n\n{path}") from e
    except UnicodeDecodeError as e:
        raise ProjectLoadError(
            f"This doesn't look like a Urdu Typewriter project file "
            f"(not valid text).\n\n{path}"
        ) from e
    except json.JSONDecodeError as e:
        raise ProjectLoadError(
            f"This project file appears to be corrupted or incomplete and "
            f"couldn't be read.\n\n{path}\n\nDetails: {e}"
        ) from e

    if not isinstance(data, dict):
        raise ProjectLoadError(
            f"This doesn't look like a Urdu Typewriter project file.\n\n{path}"
        )

    version = data.get("version")
    if not isinstance(version, int) or version > FORMAT_VERSION:
        raise ProjectLoadError(
            f"This project was saved by a newer version of Urdu Typewriter "
            f"(format version {version!r}) and can't be opened here "
            f"(this copy supports up to version {FORMAT_VERSION}).\n\n"
            f"Update the app, or open it with the version that saved it."
        )

    page = data.get("page", {})
    blocks = data.get("blocks", [])
    if not isinstance(page, dict) or not isinstance(blocks, list):
        raise ProjectLoadError(
            f"This project file's contents don't look right — it may be "
            f"corrupted.\n\n{path}"
        )
    for block in blocks:
        if not isinstance(block, dict) or not isinstance(block.get("runs", []), list):
            raise ProjectLoadError(
                f"This project file's text content doesn't look right — it "
                f"may be corrupted.\n\n{path}"
            )

    return {
        "page": page,
        "pending": data.get("pending", {}) if isinstance(data.get("pending"), dict) else {},
        "blocks": blocks,
    }


def apply_loaded_project(loaded, canvas):
    # Compute every value up front, with safe fallbacks for anything of
    # the wrong type, before mutating the canvas at all — so a single bad
    # field can't leave the page resized/recolored with the old text still
    # in place (or worse, cleared) while the rest of the load never ran.
    page = loaded["page"]
    width = max(10, _safe_int(page.get("width"), 1080))
    height = max(10, _safe_int(page.get("height"), 1080))
    margin = max(0, _safe_int(page.get("margin"), 40))
    background = _safe_color(page.get("background_color"), "#ffffff")
    background_image = _decode_image(page.get("background_image"))
    background_image_mode = _safe_str(page.get("background_image_mode"), "fit")
    if background_image_mode not in ("fit", "fill", "center", "tile"):
        background_image_mode = "fit"
    border_width = max(0, _safe_int(page.get("border_width"), 0))
    border_color = _safe_color(page.get("border_color"), "#000000")
    border_style = _STR_TO_BORDER_STYLE.get(page.get("border_style"), Qt.PenStyle.SolidLine)
    vertical_align = _safe_str(page.get("vertical_align"), "top")
    if vertical_align not in ("top", "middle", "bottom"):
        vertical_align = "top"

    pending = loaded["pending"]
    pending_family = pending.get("family") if isinstance(pending.get("family"), str) else None
    pending_style = pending.get("style") if isinstance(pending.get("style"), str) else None
    pending_size = _safe_int(pending.get("size"), None)
    pending_color = _safe_color(pending.get("color"), None)
    pending_word_spacing = _safe_float(pending.get("word_spacing"), None)

    canvas.resize_page(width, height)
    canvas.set_margin(margin)
    canvas.set_background(background)
    canvas.set_background_image(background_image, background_image_mode)
    canvas.set_border(border_width, border_color, border_style)
    canvas.set_vertical_align(vertical_align)

    if pending_family:
        canvas.set_pending_family(pending_family)
    if pending_style is not None:
        canvas.set_pending_style(pending_style)
    if pending_size and pending_size > 0:
        canvas.set_pending_size(pending_size)
    if pending_color is not None:
        canvas.set_pending_color(pending_color)
    if pending_word_spacing is not None:
        canvas.set_word_spacing(pending_word_spacing)

    _deserialize_blocks(canvas.document(), loaded["blocks"])

    # Every step above (resize, margin, background, and especially
    # _deserialize_blocks()'s cursor.insertText() calls) is a real,
    # undoable document edit as far as QTextDocument is concerned -- left
    # alone, the first Ctrl+Z after opening a file would start undoing
    # the load itself, one fragment at a time, rather than doing nothing
    # (correct, since the user hasn't edited anything yet). See
    # PageCanvas.__init__'s matching call for the construction-time case.
    canvas.document().clearUndoRedoStacks()
