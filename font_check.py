"""
font_check.py — quick heuristic to detect whether a font can actually join
Arabic-script (Urdu) letters, so the app can warn instead of silently
rendering separated letters.

Some fonts (older Nastaliq fonts built around SIL Graphite, notably) have
no usable OpenType GSUB joining rules. Qt's HarfBuzz-based shaper can't do
anything with those and falls back to isolated-form glyphs for every
character. Rather than hard-coding a font blocklist, we test it directly:
shape a single joining letter alone, then shape it followed by a second
joining letter, and check whether the *first* letter's glyph changed. A
font that implements joining will substitute an initial-form glyph for the
isolated one in context; a font that can't will hand back the same glyph
either way.
"""

from PyQt6.QtGui import QFont, QTextLayout, QTextOption
from PyQt6.QtCore import Qt

_BEH = "\u0628"             # ب  - a letter that always joins to what follows
_BEH_TEH = "\u0628\u062A"   # ب + ت  - two letters that must join


def _first_glyph(family: str, text: str):
    try:
        layout = QTextLayout(text, QFont(family, 48))
        opt = QTextOption()
        opt.setTextDirection(Qt.LayoutDirection.RightToLeft)
        layout.setTextOption(opt)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(4000)
        layout.endLayout()
        runs = layout.glyphRuns()
        if not runs or not runs[0].glyphIndexes():
            return None
        return runs[0].glyphIndexes()[0]
    except Exception:
        return None


def supports_arabic_joining(family: str) -> bool:
    """Best-effort check. Returns False if the font can't render Arabic at
    all, or renders it but without joining — both look broken to a reader,
    so both are worth the same warning. Returns True (i.e. stays quiet) if
    the check itself fails unexpectedly — a broken check shouldn't nag
    about every font on the system."""
    try:
        isolated = _first_glyph(family, _BEH)
        joined = _first_glyph(family, _BEH_TEH)
        if isolated is None or joined is None:
            return False
        return isolated != joined
    except Exception:
        return True
