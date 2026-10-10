"""
text_outlines.py — extracts a document's rendered text as a list of
independently-grouped vector outlines: (paths, color, source text), one
group per same-formatting run, per word, or per character depending on
the requested granularity, already positioned in document coordinates.

Shared by:
- exporter.py's SVG export and "Copy as SVG" (portable, font-independent
  vector text)

The key trick: instead of asking "how many glyphs does this span of text
produce, and how do I know which ones," we ask Qt's *already-shaped* line
layout for exactly the glyphs belonging to one run/word/character at a
time, via QTextLine.glyphRuns(start, length). That slice of the
already-correctly-shaped text hands back however many glyphs it actually
takes (could be more or fewer than the character count — Nastaliq
sometimes produces multiple glyphs per letter, e.g. a body plus a
separately-positioned dot), each still correctly joined and positioned,
with no need to reimplement anything about Arabic shaping ourselves.
Verified empirically: the union of every individual character's glyphs
exactly reproduces the whole-string shaping, glyph-for-glyph -- grouping
choice only changes how that same set of glyphs is batched, never which
glyphs come out.
"""

from PyQt6.QtGui import QImage, QPainter, QColor


def _force_layout(document):
    """QTextDocument computes line/glyph geometry lazily; a throwaway draw
    forces it before we go reading positions."""
    dummy = QImage(1, 1, QImage.Format.Format_ARGB32)
    p = QPainter(dummy)
    document.drawContents(p)
    p.end()


def _char_format_at(block, char_pos_in_block):
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid():
            frag_start = frag.position() - block.position()
            if frag_start <= char_pos_in_block < frag_start + frag.length():
                return frag.charFormat()
        it += 1
    return None


def _word_ranges(text, start, end):
    ranges = []
    i = start
    while i < end:
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < end and not text[j].isspace():
            j += 1
        ranges.append((i, j - i))
        i = j
    return ranges


def _fragment_ranges(block, start, end):
    """Yield (range_start, range_length, char_format) for each fragment
    overlapping [start, end) of this block. A QTextFragment is already
    Qt's own maximal same-formatting run -- text only splits into a new
    fragment where its font/color/size/etc. actually changes -- so this
    needs no grouping logic of its own, just clipping each fragment to
    the line range being extracted."""
    it = block.begin()
    while not it.atEnd():
        frag = it.fragment()
        if frag.isValid():
            frag_start = frag.position() - block.position()
            frag_end = frag_start + frag.length()
            lo, hi = max(frag_start, start), min(frag_end, end)
            if lo < hi:
                yield (lo, hi - lo, frag.charFormat())
        it += 1


def extract_groups(document, granularity="character", char_range=None):
    """granularity: "character", "word", or "run".

    char_range: an optional (start, end) pair of absolute document
    character positions -- the same numbering QTextCursor.selectionStart()
    and selectionEnd() use -- restricting extraction to just that span, so
    "Copy as SVG" can outline only what's selected instead of the whole
    page. Blocks and lines outside it are skipped outright, and a line or
    fragment it only partly covers is clipped to the covered part. None
    (the default) extracts everything, exactly as before.

    Returns a list of dicts, each: {"text": str, "paths": [QPainterPath,
    ...], "color": QColor, "bbox": QRectF}. Every QPainterPath is already
    translated into absolute document coordinates — the same coordinate
    system document.drawContents() draws into, and the same one
    doc.setTextWidth(page_width) wraps against. Whitespace is skipped
    (nothing to show or move for a space character).

    "run" extracts one group per maximal same-formatting span per line
    (see _fragment_ranges) rather than one per character or per word.
    This is the one to use for painting: pulling glyphs out one character
    at a time re-derives each glyph's position independently, and for a
    cursive, contextually-shaped script like Nastaliq that can round
    differently than the original whole-run shaping did, showing up as a
    hairline gap between two letters that should sit flush against each
    other -- worse on some fonts than others depending on how tightly
    that font's glyphs are designed to overlap their neighbors. Asking
    for a whole same-formatted span's glyphs in one glyphRuns() call
    instead reuses that span's original shaping wholesale, so letters
    that were already touching stay touching; it only ever splits where
    a real formatting change (or a line break) justifies a seam anyway.
    It's also noticeably fewer, larger calls into Qt's layout for the
    same page, so it's faster than "character" too. "word" and
    "character" are still here for anything that specifically wants
    per-word or per-character groups (e.g. reporting which source word a
    path came from) rather than the most seamless possible render.
    """
    _force_layout(document)
    groups = []

    range_start, range_end = char_range if char_range is not None else (None, None)

    block = document.begin()
    while block.isValid():
        block_text = block.text()

        # Translate char_range (absolute document positions) into this
        # block's own local offsets, [local_lo, local_hi), and skip the
        # block outright if the range never reaches it. With no range
        # the whole block is in scope, which leaves every line's start/end
        # below exactly what it was before char_range existed.
        if range_start is not None:
            block_pos = block.position()
            local_lo = max(0, range_start - block_pos)
            local_hi = min(len(block_text), range_end - block_pos)
            if local_lo >= local_hi:
                block = block.next()
                continue
        else:
            local_lo, local_hi = 0, len(block_text)

        layout = block.layout()
        layout_pos = layout.position()

        for li in range(layout.lineCount()):
            line = layout.lineAt(li)
            line_start = max(line.textStart(), local_lo)
            line_end = min(line.textStart() + line.textLength(), local_hi)
            if line_start >= line_end:
                continue  # empty line, or entirely outside char_range

            if granularity == "run":
                ranges = [
                    (start, length) for start, length, _fmt
                    in _fragment_ranges(block, line_start, line_end)
                ]
            elif granularity == "word":
                ranges = _word_ranges(block_text, line_start, line_end)
            else:
                ranges = [
                    (i, 1) for i in range(line_start, line_end)
                    if not block_text[i].isspace()
                ]

            for start, length in ranges:
                runs = line.glyphRuns(start, length)
                paths = []
                bbox = None
                fmt = _char_format_at(block, start)
                color = fmt.foreground().color() if fmt is not None else QColor("black")
                if not color.isValid():
                    color = QColor("black")

                for run in runs:
                    raw_font = run.rawFont()
                    for gi, gpos in zip(run.glyphIndexes(), run.positions()):
                        path = raw_font.pathForGlyph(gi)
                        if path.isEmpty():
                            continue  # whitespace and other zero-ink glyphs
                        path.translate(gpos)
                        path.translate(layout_pos)
                        paths.append(path)
                        pb = path.boundingRect()
                        bbox = pb if bbox is None else bbox.united(pb)

                if paths:
                    groups.append({
                        "text": block_text[start:start + length],
                        "paths": paths,
                        "color": color,
                        "bbox": bbox,
                    })
        block = block.next()
    return groups
