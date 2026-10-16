![CI Status](https://github.com)
# Urdu Typewriter

A desktop app for typing Urdu on an A4 page and exporting it as a PNG,
JPG, SVG, or PDF — with correct Nastaliq letter joining, Roman-Urdu and
voice input, and a page you can save and reopen to keep editing later.

## Why this actually joins letters correctly

Most tools that mangle Urdu (letters showing up separated instead of
joined) are drawing each character's default isolated glyph one after
another, with no script-aware shaping. This app doesn't do any of its own
text processing for typed Urdu — it hands plain Unicode text to Qt's text
engine (which uses HarfBuzz internally) and lets it do the actual joining,
exactly like a browser or Word would.

The one thing that *does* matter is the font. A Nastaliq font needs its
joining/positioning rules in standard OpenType `GSUB`/`GPOS` tables for
HarfBuzz to use them — some older Nastaliq fonts (e.g. SIL's Awami
Nastaliq) implement this via **Graphite** tables instead, which HarfBuzz
doesn't read, and will render as disconnected letters in this app no
matter what. The bundled default, **Noto Nastaliq Urdu**, is built the
OpenType way and is confirmed working. If you pick a font and the yellow
warning bar appears, that's this exact issue — try a different one.

## Setup

```bash
pip install -r requirements.txt
python main.py
```
Optional, only for the floating Voice to Urdu button:
```bash
pip install -r requirements-voice.txt
python download_model.py      # recommended, see requirements-voice.txt
```

To sanity-check your setup without opening the GUI:
```bash
python test_render.py     # Urdu shaping + all five export formats, incl. export-failure handling
python test_zoom.py       # cursor-anchored zoom math + voice button startup positioning
python test_voice.py      # voice transcription chunking/queue logic (no mic or model needed)
python test_download_model.py # download retry/verification logic (faked huggingface_hub, no network)
python test_roman_urdu.py # Roman Urdu -> Urdu script accuracy
python test_project_io.py # .utw save/load error handling (corrupted files, bad fields, etc.)
python test_svg_copy.py   # Copy as SVG (Curves): cropped to content/selection, clipboard formats incl. the Windows PDF name
python test_eps.py        # EPS export: well-formed file, matches the app's render (checked with Ghostscript if installed)
python test_font_gap.py   # experimental gap between words in different fonts: planning, layout, undo-safety, exports
python test_font_search.py # font dropdown's search popup: filtering, keyboard nav, picking
python test_spacing.py    # line height + word spacing: live apply, undo merging, cursor tracking
python test_spacing_toolbar.py # the same, through the real toolbar spin boxes
python test_prefs_startup.py   # saved Preferences (font/size/page size/autosave interval) are applied on launch
python test_context_menu.py    # right-click menu: opens/works correctly, ignores page zoom, offers Copy as SVG (Curves)
```

## Using it

**Font & style** — two dropdowns: font family, and the specific *style*
within that family (Regular / Medium / Bold / whatever the font actually
has), tracked independently so a font that ships several styles under one
family name — Jameel Noori Nastaleeq and similar fonts often do this —
gets each style addressed explicitly. Click the font dropdown for a search
box above the list — type part of a name to filter (words can be in any
order: "urdu noto" matches "Noto Nastaliq Urdu"), Up/Down or PageUp/PageDown
to move through matches, Enter to pick. Useful once you've enabled more
than a handful of fonts in the Font Manager. The same search box is used
for Preferences' default font.

**Color, size, alignment** — toolbar. Whatever you set here is what gets
typed next, even after you click elsewhere on the page first.

**Underline, strikethrough, highlight color** — Format menu, plus
Underline/Strikethrough as toolbar toggles. Same "sticky, applies to what
you type next" behavior as color/size/alignment above, for consistency.

**Letter spacing, line height, word spacing** — three separate settings,
easy to conflate but each doing something different. Letter spacing
(Format menu → Letter Spacing…, percentage-based, 100 = normal) changes
the space *inside* words, between individual letters — worth using
sparingly on Nastaliq body text, since it's a joining script and extra
space between glyphs can visually fight the connected letterforms; it's
more at home on a short decorative line than a full paragraph. Line height
and word spacing both live directly in the toolbar now (icons next to the
alignment buttons, plus Format menu → Line Height…/Word Spacing… for
typing an exact number) and apply live as the number changes — no OK
button, no need to click away from the page to see the result. Line height
is a percentage of the line's normal height (100 = single, 150 = 1.5×);
word spacing is extra pixels between whole words only, independent of
letter spacing (0 = normal, negative pulls words closer). Both apply to
the current selection, or the current paragraph/next-typed-text if nothing
is selected, and are saved with the project.

**Background image, border** — File → Page Setup…, alongside the existing
background color. The image can be set to Fit (whole image visible,
letterboxed if needed), Fill (covers the page, cropped if needed), Center,
or Tile. Both are embedded directly in `.utw` project files (not saved as
a path reference — see project_io.py) and rendered identically on screen
and in every exported format.

**Undo/Redo, Cut/Copy/Paste, Select All** — Edit menu, standard shortcuts
(see Shortcuts below). Ordinary QTextEdit functionality that was always
one keystroke away but wasn't previously in any menu.

**Find & Replace** — Edit menu or Ctrl+F. A small non-modal window (stays
open, doesn't block the page) with match case / whole word options and
Replace/Replace All.

**Recent Files** — File → Open Recent. Remembers your last 8 opened/saved
projects; entries for files that have since been moved or deleted quietly
drop off the list rather than showing a dead link.

**Autosave & recovery** — every 60 seconds by default (set your own
interval in Preferences — it's read at startup now, not just while the app
happens to still be open from when you changed it), if there are unsaved
changes, a snapshot is written to a recovery file outside your project
folder (not your actual .utw — your own Save/Save As are still the only
things that touch that). If the app didn't close normally, the next launch
offers to recover it. A clean exit (including choosing "Discard" when
asked about unsaved changes) clears the recovery file, so you're never
offered a snapshot that's already been handled one way or another.

**Print** — File → Print…, or Ctrl+P. Uses the same rendering as every
export format, scaled to fit the printable area of whatever paper size
your printer driver reports.

**Copy as Image** — File → Copy as Image, or Ctrl+Shift+C. Renders the
page at 2x and puts it directly on the system clipboard — no filename or
folder needed for a quick paste into a chat or document.

**Copy as SVG (Curves)** — File → Copy as SVG (Curves), the page's
right-click menu, or Ctrl+Alt+Shift+C. Puts your current selection (or,
with nothing selected, every letter on the page) on the clipboard as
scalable vector curves, the same outlined-text rendering the SVG/PDF/EPS
export uses (see "Font-independent, self-contained" below) — paste it into
something that reads vector clipboard data and the text is real
editable/scalable curves, not a font reference. Cropped tight to the
glyphs themselves — no page background, border, or empty page area riding
along. The clipboard gets genuine vector data in every form a receiving
app might look for, and nothing raster (an app offered a bitmap next to
real vectors can quietly take the bitmap, which looked like blurry pasted
text): an SVG (`image/svg+xml`); a PDF of the same curves
(`application/pdf`, which is what macOS and Linux apps look for); and, on
Windows, that same PDF again under the clipboard name **"Portable
Document Format"**. That last one is the fix for "pastes nothing at all
into Affinity": Windows matches clipboard formats by exact registered name,
and Affinity registers its own PDF clipboard data as "Portable Document
Format" — not the MIME-style `application/pdf` that Qt uses by default —
so with only the MIME names present the most likely explanation is that it
saw nothing it recognised. (That's an inference from how Windows clipboard
formats work and from Inkscape's issue tracker, not something that could be
tested against Affinity itself — see "What's verified" below.) If an app
still pastes nothing:
in Affinity, **Edit → Paste Special** (Windows desktop) lists exactly the
formats it sees on the clipboard; and Export ▸ SVG / PDF / EPS to a file
and the app's Open/Place always works, since it sidesteps clipboard
format guessing entirely. For a plain picture, use Copy as Image instead.

**Fit to Window / zoom presets** — View menu, alongside the existing
zoom in/out/reset.

**Word / character count** — bottom-right corner of the status bar,
updates a moment after you stop typing rather than on every keystroke.

**Roman → Urdu** (toolbar toggle) — type phonetically in Latin letters and
finishing a word (space, punctuation, or clicking away) converts it to
Urdu script automatically. Try typing `school` then a space. Backed by a
dictionary of roughly 9,600 words (grown from an original 715 — verb
conjugations, everyday vocabulary, and names) for common words, a
fuzzy-match step that catches near-miss spellings (`zindagy` → زندگی,
`accha` → اچھا) before falling back to phonetic pattern-matching for
everything else. The fuzzy-match step also requires a candidate correction
to have exactly the same consonants as what you typed, not just a similar-
looking string — a plain similarity score alone can't distinguish "typo of
a dictionary word" from "different word that happens to look similar"
(`taiba`, a name, was being silently "corrected" to `taliba`, "female
student," since the two score as more alike than some genuine typo fixes
do; they now don't share a consonant set, so the fuzzy step correctly
leaves `taiba` alone — and it's also its own dictionary entry now, so it
never reaches that step in the first place). This is a best-effort
phonetic convenience, not a guarantee of canonical spelling — a toggleable
guide table (Tools menu, or the toolbar button next to Roman → Urdu) shows
every Roman→Urdu letter mapping if a word isn't converting the way you
expect. **Edit → Convert Selection to Urdu** does the same conversion on
demand, for text already typed or pasted.

**Voice to Urdu** — a small floating circular button over the page (drag
it anywhere out of your way). Click to start; speech is transcribed in
the background as you go and lands on the page every few seconds, in
batches, without needing to stop recording first. While listening, a
white ring around the button tracks your actual input level in real
time — a genuine "yes, I'm hearing you" rather than a canned animation,
and the fastest way to tell a dead microphone from a threshold that's
merely too strict if voice input ever seems unresponsive. Click again to
stop; a rotating gold arc shows the tail end still being transcribed,
and the button settles to idle once that's done. Uses
`Systran/faster-whisper-medium`, running locally via faster-whisper — no
audio leaves your machine — with `beam_size=3` (explores a few candidate
decodings instead of just the greedy best guess) and
`condition_on_previous_text=False` (stops one early mistake from
dragging down the rest of a chunk) for a real accuracy bump beyond what
model size alone gives. `temperature=0.0` pins decoding to a single
deterministic pass — faster-whisper's default is a 6-value fallback
ladder that silently re-decodes a chunk from scratch, up to 6 times,
whenever it doesn't look confident enough, which is almost certainly why
transcription had been taking a roughly constant ~45-50s no matter how
short the clip was. This app has moved around on the speed/accuracy
trade-off a few times chasing the right balance — see the top of
`voice_to_urdu.py` for `MODEL_ID` alternatives (smaller/faster or
larger/more-accurate) if medium isn't right for your machine; it's a
one-line swap either direction. The button's color shows its state (grey
while the model loads in the background at startup, red while listening
— including while a chunk is quietly transcribing in the background,
gold while finishing up the last chunk or two after you click stop, red
again if the model failed to load — click it to retry). Clicking the
button while it's grey or gold now says so on the status bar instead of
silently doing nothing, which used to look exactly like voice input
being broken if you clicked before the model had finished loading. Run
`download_model.py` once for a permanent local copy instead of
downloading on first use every time (see requirements-voice.txt) — if
that download gets interrupted partway, the app notices the local copy
is incomplete or corrupted and says so plainly rather than failing with
a raw library error partway through a recording, or (a real case this
app hit) looping on "click to retry" forever because a required file
existed but was a truncated stub, which an existence-only check
couldn't tell apart from the real thing. Re-running `download_model.py`
in that state replaces the bad copy instead of trusting it as already
done (`test_download_model.py` covers this). This check only ever looks
at files at rest, never right after a download `download_model.py` just
ran itself — that specific timing (checking immediately after
`snapshot_download()` returns) raced against newer huggingface_hub
versions' separate download-then-reconstruct steps for large files and
produced a real false positive, throwing away a completely good ~1.5GB
download and forcing an unnecessary full re-download. `download_model.py`
trusts `snapshot_download()`'s own success/failure signal for that
reason — it has real visibility into its own transfer that an outside
check, run at an arbitrary moment, doesn't. A second, separate mistake in
this same check: its "too small to be real" minimum was one shared number
for all four required files, sized with `model.bin` (gigabytes) in mind —
which is nowhere near safely below `config.json` (~2.26 KB) or
`vocabulary.txt` (~460 KB), so those two failed the check on literally
every download, including perfectly good ones, forever. Fixed with a
per-file minimum checked against each file's actual real size instead of
one guessed number for all of them (`test_voice.py`'s "real-sized" case
writes files at their exact observed sizes specifically to catch a
too-strict threshold like that one).

Chunking is silence-based (see `UtteranceSegmenter` in `voice_to_urdu.py`)
but deliberately *not* eager: it holds off cutting on an ordinary pause
until a few seconds of speech have built up, only cutting sooner if the
pause runs unusually long. This app's first attempt at "live" cut on
every short pause instead, which turned out to work against the goal:
Whisper-family models always encode a fixed 30-second window internally
regardless of the actual clip length, so a 1-second clip and a
10-second clip cost roughly the same to transcribe. Cutting eagerly
maximized the number of times that mostly-fixed cost got paid over a
dictation session, which is what made "live" transcription feel
sluggish and unresponsive rather than fast — the fix isn't (only) a
smaller model, it's fewer, more worthwhile transcription calls. A single
very long run-on sentence still gets force-cut every 14 seconds so it
doesn't block feedback indefinitely; brief noise/breath sounds without
real speech are dropped rather than sent to the model. All of these are
tunable constants if your room, mic, or hardware need different values.

**Fonts…** — a checklist of every font on your system, pre-checked for
detected Arabic/Urdu support. Check/uncheck by hand; saved across
restarts.

**Zoom** — Ctrl+scroll over the page (zooms around wherever your cursor
is, the same convention as Adobe/Affinity apps), the +/− buttons, or
Ctrl+=/Ctrl+-/Ctrl+0 (these keep the view centered instead, since a
button click or shortcut has no "cursor position over the page" to anchor
to). Zoom is a view convenience only — it never changes the page's actual
pixel size or what gets exported. Plain scrolling (no Ctrl) pans the page
normally.

**Zoom Tool** — the magnifying-glass button next to the zoom controls.
While it's on, click the page to zoom in (anchored where you click);
right-click, or Alt+click, to zoom out the same way. Same view-only
zoom as above, just click-driven instead of scroll-driven — handy on a
trackpad, or when you want to zoom to one exact spot without hunting for
it after. Click the button again, or press Esc, to go back to normal
typing.

Getting cursor-anchoring right took two separate fixes, both explained in
comments where they live: the anchor point is now computed by hand
(`ZoomableGraphicsView._zoom_to` in `main.py`) rather than relying on
Qt's built-in mouse-anchor mode, which doesn't track the cursor correctly
here since the page widget intercepts the wheel event first — and the
page now sits on a padded, generously scrollable "pasteboard"
(`MainWindow.PASTEBOARD_MARGIN`), because Qt's view alignment forces
dead-center positioning on any axis where content fits inside the
viewport, which the default page (794px wide) does against the default
window — meaning the first fix alone still looked broken in the most
common starting condition. See `test_zoom.py` for both.

**Page Setup** — width/height in **px, in, cm, mm, or ft** (switching
units converts what's already there rather than resetting it), margin,
background color, or a preset. Defaults to A4. Unit conversion assumes
96px = 1 inch.

**Save / Open Project** (Ctrl+S / Ctrl+O) — saves everything (page
settings and every run of text with its exact font, style, size, and
color) to a `.utw` file — the same idea as an Illustrator `.ai` file. A
finished PNG/JPG/SVG/PDF export can't be reopened as editable text; `.utw`
always can.

**Export** (Ctrl+E) — PNG, JPG, SVG, PDF, or EPS.
- **PNG/JPG** are raster: width/height steppers control the exported
  resolution independently of the on-screen page size.
- **SVG/PDF/EPS** are vector: no resolution to set. All are fully
  self-contained — SVG and EPS export as real vector outlines (not a
  `<text>` tag or font reference) and the PDF embeds the font — so all
  display correctly on a machine without that font installed.
- **EPS** is written directly (no Ghostscript or other tool needed):
  Encapsulated PostScript Level 2, one closed filled path per glyph, the
  page background colour and border as vector shapes, and the page's
  size in points (px × 72/96) as its bounding box. A background *image*
  is embedded as a JPEG, since that's what it is. EPS has no
  transparency, so a translucent colour is flattened onto white.
- **Export All Formats** writes PNG, JPG, SVG, PDF, and EPS together next
  to each other, named from whatever base filename you choose.

**Gap Between Fonts (Experimental)** — Tools → Gap Between Fonts, choose
Off / Subtle / Normal (default) / Wide. When two neighbouring words on a
line are in different fonts, adds a small extra gap between them so they
don't touch — a word's space takes the width of whatever font it was
typed in, and Nastaliq's space is very narrow (~0.13 em vs ~0.32 em in a
typical text font) while its letters overhang. The gap is a fraction of the
font size (Subtle 4%, Normal 7%, Wide 12% — about 3.4 px at 36 pt for
Normal), applied only on the space between such words (the last one, if
there are several), never inside a word. It shows on the page and is in
every export, Copy as SVG, and printing alike. It lives purely in the page
layout: nothing is added to your text, formatting, undo history or saved
`.utw` files, so Off gives you back exactly the page you had. To remove the
feature entirely, delete `font_gap.py`, `test_font_gap.py`, and the few
lines mentioning `font_gap` in `main.py` and `exporter.py` (`font_gap.py`'s
docstring lists them).

**Superscript / Subscript, Clear Formatting, Format Painter** — Format
menu. Superscript and subscript are mutually exclusive (turning one on
turns the other off). Format Painter copies the formatting at the cursor
or selection, then applies it the next time you select something —
click it, then select; it deactivates itself after one use.

**Border style** — Page Setup, alongside width/color: solid, dashed,
dotted, or dash-dot.

**Text Vertical Align** — Page Setup: Top (default), Middle, or Bottom.
Pushes the text block down within the page when it doesn't fill the
page's height — recalculated automatically as you type.

**Insert Date** — Edit menu. Inserts today's date at the cursor.

**Document Statistics** — View menu. Word/character/line/paragraph
counts and a rough reading-time estimate.

**Preferences** (Ctrl+,) — Tools menu. Default font, default size,
default page size, and autosave interval. The default font/size/page size
apply the moment you launch the app, and immediately to the current page
if it's still blank; Page Setup still changes a page you've already
started on. The autosave interval applies right away, this session
included — no restart needed.

**About** — Help menu.

**Drag and drop** — drop an image file onto the window to set it as the
page background, or a `.utw` file to open it.

## Shortcuts

| Action | Shortcut |
|---|---|
| Page Setup | Ctrl+N |
| Open Project | Ctrl+O |
| Save Project | Ctrl+S |
| Save Project As | Ctrl+Shift+S |
| Export | Ctrl+E |
| Copy as Image | Ctrl+Shift+C |
| Copy as SVG (Curves) | Ctrl+Alt+Shift+C |
| Preferences | Ctrl+, |
| Clear Formatting | Ctrl+\ |
| Print | Ctrl+P |
| Undo / Redo | Ctrl+Z / Ctrl+Shift+Z |
| Cut / Copy / Paste | Ctrl+X / Ctrl+C / Ctrl+V |
| Select All | Ctrl+A |
| Find & Replace | Ctrl+F |
| Underline | Ctrl+U |
| Zoom in / out / reset / fit | Ctrl+= / Ctrl+- / Ctrl+0 / Ctrl+9 |
| Quit | Ctrl+Q |

## What's verified vs. what needs your testing

Most of this has been tested end-to-end in a sandboxed Linux environment
with no display and no microphone, using simulated keyboard/mouse events
and, where real hardware or network access to Hugging Face would
normally be involved, synthetic audio and mocked models instead:

- **Copy as SVG pasting nothing into Affinity, Copy as SVG in the
  right-click menu, EPS export, and the experimental gap between fonts** —
  *Pasting nothing:* Qt puts a PDF on the Windows clipboard under the name
  "application/pdf", but Windows matches clipboard formats by exact
  registered name and Affinity registers PDF as "Portable Document
  Format" (documented for Affinity Designer in Inkscape's tracker, which
  also notes some programs won't accept "application/pdf"), so the likeliest
  explanation is that it saw nothing to paste. The
  PDF is now also added under that name using Qt's documented
  `application/x-qt-windows-mime;value="…"` form, first in the list.
  Verified: the exact format list and order for Windows/macOS/Linux
  (platform is injectable so this is checkable anywhere), byte-identical PDF
  under both names, no raster image present, and the real clipboard flow.
  *Not* verified, because it can't be from here: that Affinity on your
  machine accepts it — there's no Windows or Affinity in the sandbox. Edit →
  Paste Special in Affinity shows what it sees; the EPS/SVG/PDF export is
  the guaranteed route. *Right-click menu:* "Copy as SVG (Curves)" is the same
  QAction as the File menu's, under a separator below Qt's standard
  entries; checked that clicking it really copies. *EPS:* written in pure
  Python; verified by rendering it with Ghostscript (an independent
  PostScript interpreter) and comparing with the app's own render — text,
  colours, dashed border and background image in all four fit modes match
  to within anti-aliasing/JPEG noise — plus structure checks (DSC header,
  integer bounding box enclosing the hi-res one, 7-bit ASCII, no line over
  255 characters, one path per glyph). *Gap between fonts:* 90 checks,
  including a 450-step randomised session of inserts, deletes, font
  changes, undo and redo asserting the layout is never stale or missing
  after any step, that document text/formats/revision/undo history are
  untouched, that saved projects are byte-identical to the feature being
  Off, and that screen and every export agree. The first design stored
  the gap as a character format in the document; that was dropped after
  testing showed Qt's undo-joining doesn't merge with real keystrokes, which
  would have left stray undo steps and "modified" noise — the
  layout-level approach has none of that. What only your eyes can judge:
  whether Normal is the right size, which is the experiment. Also worth
  knowing, found while testing and unchanged here: underline, strikethrough
  and highlight aren't included in any outline-based output (SVG, Copy as
  SVG, PDF-curves, EPS) — only the glyph shapes are.
- **Right-click menu still scaling with zoom, and Copy as SVG not
  actually pasting as curves** — two more reports on fixes from the
  round before this one, both tracing to a second, deeper layer underneath
  what was already fixed. The menu: last round's fix stopped it drifting
  out of place while open, but not an entirely separate problem visible in
  the very first screenshot this project started from — the menu itself
  rendering at the page's current zoom level, not a constant size. Root
  cause, confirmed directly rather than assumed: this page is embedded in
  its QGraphicsScene via QGraphicsProxyWidget, and Qt's own docs for that
  class say it "takes care of automatically embedding popup children of
  embedded widgets" — menu.graphicsProxyWidget() being a real, distinct
  proxy object once the menu is shown (not None) confirms that's exactly
  what's happening here, so the menu is painted through the same zoom
  transform as the page. Fix: ItemIgnoresTransformations, Qt's own flag
  for "this graphics item ignores the view's transform," set on that
  auto-created proxy. What this could confirm: the mechanism is real (that
  proxy object), the flag gets set, and the menu still opens, shows the
  right actions, and works normally afterward — switching from
  menu.exec() to menu.popup() plus a local QEventLoop was necessary to get
  a chance to set the flag before the menu is first painted. What it
  couldn't confirm: how the menu actually *looks* at a given zoom level —
  this sandbox has no real display, and the offscreen Qt platform doesn't
  reproduce the scaling bug at all (geometry queries come back identical
  with or without the fix), so this one genuinely needs trying on a real
  screen to be sure. Copy as SVG: removing the raster clipboard fallback
  last round (to stop it winning over the real vector data and showing up
  pixelated) turned out to remove the only thing Affinity was actually
  reading — its own forum confirms it doesn't read arbitrary external SVG
  text pasted onto its clipboard as curves at all, rasterizing it if it
  accepts it as anything. A PDF on the clipboard is the format several
  vector apps, Affinity included, are independently reported to read as
  real vector data, so copy_as_svg now puts both an SVG and a PDF of the
  same curves on the clipboard (render_pdf_curves_bytes in exporter.py,
  built the same cropped, selection-aware way as the SVG version) and
  nothing else — unlike the old raster/SVG pairing, both of these are
  genuine vector data, so there's no "wrong one wins" risk. What this
  could confirm: both formats land on the clipboard correctly, cropped
  and selection-aware, with no competing raster image, checked end to end
  through the real clipboard (test_svg_copy.py) and by rendering the
  generated PDF back with QPdfDocument to confirm its content matches the
  SVG's. What it couldn't confirm: whether Affinity specifically reads
  *this* PDF as curves — that depends on undocumented behavior of a
  specific third-party Windows application with no way to test it from
  here, and on Windows, Qt's clipboard format naming for a MIME type like
  "application/pdf" doesn't always land under the exact name a given app
  is checking for (some register their own `QWindowsMimeConverter`,
  essentially a format-name alias Qt doesn't apply automatically) — so if
  Affinity specifically still won't take it from the clipboard, Export ▸
  SVG or PDF to a file and Affinity's own Place/Import is the fallback
  that's guaranteed to work regardless of any of this, since it sidesteps
  clipboard format guessing entirely. Separately, chasing an intermittent
  hang in this round's new right-click-menu test surfaced a real gap in
  every test file that constructs a MainWindow(): QSettings was being
  pointed at an isolated temp file so tests could never read or write
  real preferences, but QStandardPaths.AppDataLocation (where the
  autosave-recovery file lives) wasn't, so a recovery file one test run
  happened to write could sit there and block a *later* run on a
  "recover unsaved work?" dialog with nothing in a headless test to click.
  QStandardPaths.setTestModeEnabled(True), now set alongside the existing
  QSettings isolation in every test that constructs a MainWindow(), closes
  that gap the same way for both.
- **This round's fixes** (zoom during the right-click menu, Copy as SVG
  accuracy/cropping, Preferences/autosave not taking effect, line
  height + word spacing, font search, a Roman-Urdu false-positive) —
  each traced to a specific root cause rather than patched by
  symptom, and each covered by a new, dedicated test file (see Setup):
  the right-click-menu zoom leak turned out to be a *second*,
  untested code path — `ZoomableGraphicsView.wheelEvent` handles wheel
  events landing directly on the view (the pasteboard backdrop around
  the page, which is most of the window at a high zoom level) and had
  no `context_menu_open` check of its own at all, separate from the one
  `MainWindow.eventFilter` already had for events landing on the page
  itself; fixing it needs the view to actually know about the canvas
  (`self.view.canvas = self.canvas`), and the first version of this fix
  forgot exactly that line — caught immediately by the new regression
  test in `test_zoom.py`, which is as good a demonstration as any of why
  that test exists. Copy as SVG's pixelated-in-Affinity report traced to
  a raster preview that used to ride alongside the real SVG data on the
  clipboard "for apps that can't read SVG" — several apps, given both,
  quietly prefer the bitmap; removed, so the clipboard carries vector
  data only. Its "pastes the whole page, not just what I selected" report
  was real too: `text_outlines.extract_groups()` now takes an optional
  character range so Copy as SVG can outline just a selection, and the
  result is cropped to the *outlined glyphs'* own bounding box rather
  than sized to the page — checked by rasterizing the produced SVG and
  measuring ink coverage/bounding box against a full-page export
  (`test_svg_copy.py`), not by eyeballing a paste into a real design app,
  which a sandbox can't do. Preferences turned out to be saved to disk
  correctly the whole time — reopening the dialog always showed your
  last values — but never read back anywhere except to re-populate that
  same dialog: default font/size/page size were never applied to an
  actual page, and the autosave interval was hardcoded at startup and
  only took the saved value if you reopened Preferences in that same
  session. Both now apply on launch (`test_prefs_startup.py`, against an
  isolated settings file so it can't touch your real preferences), and
  the font/size/page-size defaults also apply immediately if the current
  page is still blank. Line height and word spacing are now live toolbar
  controls (new icons) rather than only a Format-menu dialog — word
  spacing is a new setting entirely (extra pixels between words only,
  independent of letter spacing), and a short run of small adjustments
  (dragging a spin box, several arrow clicks) collapses into one undo
  step instead of one per tick, without swallowing an unrelated edit
  that happens to land in between (`test_spacing.py`,
  `test_spacing_toolbar.py` against the real toolbar). The font
  dropdown's new search popup (click to open, type to filter, arrow keys
  and Enter to pick) is covered by `test_font_search.py` using Qt's
  synthetic keyboard/mouse events — what that can't confirm is how it
  actually feels to use with a couple hundred real installed fonts. And
  the Roman-Urdu report ("TAIBA" converting wrong) traced to the
  fuzzy-spelling-correction step silently "fixing" it into the unrelated
  word "taliba" ("female student") — the two score as *more* alike by
  plain string similarity than some genuine typo corrections this same
  step is supposed to make (`mohabt` → `mohabbat`), so tightening the
  similarity threshold alone couldn't separate them without breaking
  real corrections; fixed by also requiring a fuzzy match to have the
  exact same set of consonants as what was typed, checked against 728
  synthetic typo variants of the existing dictionary with zero
  regressions before landing it (see the comment above
  `_fuzzy_dictionary_match` in `roman_urdu.py`), plus new entries for
  "taiba"/"tayyaba" so that specific word no longer reaches the fuzzy
  step at all.
- **SVG outline grouping, Copy as SVG, and the Zoom Tool** —
  `extract_groups()`'s new "run" mode (one outline-extraction call per
  same-formatting span instead of one per character, specifically to
  stop cursive Nastaliq letters that should sit flush against each other
  from showing a hairline gap on some fonts) was checked against the
  real Nastaliq test string in `test_render.py`: same total glyph count
  and same overall bounding box as the old per-character extraction, just
  far fewer, larger groups (20 down to 1, for that string) — proof nothing
  is dropped or moved, only batched differently, and a check that would
  catch the grouping silently doing nothing. What that check can't do is
  look at a rendered SVG and confirm a gap is actually gone by eye — open
  the exported/copied SVG on your own machine and compare closely-set
  Nastaliq letters against the old export if you still have one. Copy as
  SVG's clipboard write, threading (it reuses the same off-GUI-thread
  `ExportThread` file export already used, since a full page's outlines
  can take a moment), and its raster fallback for paste targets that
  can't read SVG were all exercised directly; pasting the result into a
  real vector app (Illustrator, Inkscape) is not something a headless
  sandbox can confirm. The Zoom Tool's click-to-zoom-in, right-click and
  Alt+click-to-zoom-out, cursor change, Esc to exit, and that a normal
  click is untouched when the tool is off are all covered by
  `test_zoom.py` against a real `MainWindow` — what that can't confirm is
  that the magnifying-glass cursor actually looks right on your desktop,
  or that right-click-to-zoom-out doesn't *also* pop the normal
  right-click text menu on every platform (it shouldn't — consuming the
  click should stop that menu from ever being triggered — but that's a
  platform-level behavior worth a quick look). Separately: the
  *standard* right-click menu (Cut/Copy/Paste/etc., which Qt provides
  automatically for any QTextEdit — this app doesn't build it) is a real
  top-level popup, not part of the page, so it doesn't move or resize
  when the page does; a Ctrl+scroll or a Zoom Tool click landing while
  that menu happens to still be open would zoom the page out from under
  it, visibly drifting the two apart. `PageCanvas.contextMenuEvent` now
  tracks exactly when that menu is open, and both zoom paths check it
  first and do nothing while it's up — covered in `test_zoom.py` by
  mocking the menu open/closed rather than actually popping a real one
  (nothing to click in a headless sandbox), which stops short of a real
  right-click on a real screen.
- **This round's additions** (superscript/subscript, Clear Formatting,
  Format Painter, vertical page alignment, border style, page drop
  shadow, batch export, Preferences, Document Statistics, Insert Date,
  drag-and-drop, window geometry/export folder/format persistence) were
  checked by actually running the code, not just compiling it — twice
  that distinction mattered: a `RecursionError` (`textChanged` →
  `setFrameFormat()` → `textChanged` again for vertical alignment, and
  the same pattern via `selectionChanged` for Format Painter — both
  fixed with a reentrancy guard) and two plain missing imports in a new
  dialog only surfaced once the dialog was actually opened. Also found
  this way: `test_zoom.py` and `test_voice.py` construct a real
  `MainWindow`, which now reads and writes window geometry via the same
  `QSettings` store the real app uses — without isolating that, a test
  run could restore whatever geometry a *previous* run (or actual use of
  the app) had saved, rather than testing the real default. Both now
  redirect `QSettings` to a temp location for the duration of the test.
- **This round's editor features** (underline/strikethrough/highlight,
  letter/line spacing, background image + border, Undo/Redo,
  Find & Replace, Recent Files, autosave/recovery, Print, Copy as Image,
  Fit to Window, word count) were checked by rendering the actual widgets
  offscreen and inspecting the resulting images pixel-by-pixel (not just
  "the code ran") — this is how a real, previously-invisible bug got
  caught: `PageCanvas`'s own setup (`setAlignment`, `setDocumentMargin`)
  counts as an undoable edit to Qt, which meant `isUndoAvailable()` was
  already `True` before a person had typed anything, and the Undo menu
  item's enabled-state tracking (listening for a false→true transition)
  would never have fired — fixed via `clearUndoRedoStacks()` in both
  `PageCanvas.__init__` and after loading a project. What a sandbox
  genuinely can't stand in for: a real printer (Print goes through your
  OS's normal print dialog, untested past that point), pasting the
  clipboard image into another real application, and — hardest to fake
  convincingly — recovering from an actual crash or force-quit rather
  than a clean process exit. Worth specifically trying those three.
- **Zoom-to-cursor** no longer relies on Qt's built-in mouse-anchor mode
  (see the `ZoomableGraphicsView` comments in `main.py` for why that
  didn't track the cursor correctly here) — it's now done by hand,
  comparing scene coordinates before and after each zoom step and
  correcting the scroll position directly. A second, separate bug meant
  that fix alone wasn't enough: Qt's view alignment forces dead-center
  positioning on any axis where content fits inside the viewport, which
  the default page does against the default window, so the very first
  thing most people would try (zoom in from a fresh document) still
  looked broken. Fixed by giving the page a large, generously scrollable
  margin (`MainWindow.PASTEBOARD_MARGIN`) so that condition never
  applies. `test_zoom.py` checks both: the anchor math in isolation, and
  the real `MainWindow` at its real default page/window size specifically
  (the case that exposed the alignment bug and that a synthetic
  bigger-than-viewport test scene couldn't catch). The one thing that's
  inherently not fully preservable — zooming *out* while already
  scrolled to an extreme edge of the (now much larger) scrollable area —
  clamps gracefully there, the same way Figma/Photoshop/browsers do.
- **Voice transcription's chunking, ordering, and busy-state handling** —
  `UtteranceSegmenter`'s silence detection (including the "hold off
  until there's a worthwhile amount of speech" behavior that's the crux
  of the current chunking strategy), the recording queue, button state
  transitions, and error recovery are covered by `test_voice.py` against
  synthetic audio and a fully mocked `voice_to_urdu`, including chunks
  arriving faster than they can be transcribed (queued and drained in
  order), a chunk failing mid-recording (recovers to listening, since the
  mic is still open), stopping with no speech captured at all, clicking
  the button while it's busy (now surfaces a status message instead of
  being silently ignored — previously indistinguishable from voice input
  being broken if you clicked before the model finished loading), and an
  interrupted/partial model download being detected and reported clearly
  rather than failing deep inside a library call — including the case
  where every required file exists but one is a truncated stub, which
  looks "present" to a naive check but isn't real, and (the flip side,
  after this check itself had a bug) real files at their real sizes
  correctly passing rather than being rejected by too-strict thresholds
  (`local_model_available()`'s per-file size check, and `test_voice.py`'s
  "truncated folder" and "real-sized" cases for each).
- **`download_model.py`'s retry loop against a faked `huggingface_hub`**
  — `test_download_model.py` scripts snapshot_download to raise (retried
  with backoff, cleaned up only if every attempt is exhausted — trusting
  its own resume handling for anything in between), to "succeed" while
  actually leaving a truncated file (trusted anyway — see below for why),
  and to succeed for real, plus a pre-existing truncated folder getting
  replaced rather than trusted as already-downloaded — all without a
  real network call.
- **Voice to Urdu's actual microphone capture, real transcription speed,
  and accuracy on real Urdu speech** — and the real download in
  `download_model.py` — are the things a sandboxed environment genuinely
  can't stand in for. Worth trying first. This app has swung on the
  speed/accuracy trade-off a few times now, and landed on: chunking that
  doesn't cut so small that Whisper's fixed per-call cost (see the
  chunking note above) got paid over and over, `medium` rather than a
  smaller or larger model, `beam_size=3` +
  `condition_on_previous_text=False` for extra accuracy that doesn't cost
  the encoder pass much extra, `temperature=0.0` so a chunk is decoded
  exactly once instead of faster-whisper's default up-to-6-attempt
  fallback ladder silently retrying from scratch whenever it isn't
  confident enough (the actual cause of transcription taking a roughly
  constant ~45-50s regardless of clip length — worth specifically
  re-testing, since a sandbox can confirm the right kwargs reach
  `.transcribe()` but not real wall-clock time on real hardware), and
  `SILENCE_RMS` lowered to 0.004 (was
  0.010) since the old threshold could plausibly miss a quieter mic or
  conservative OS input gain entirely — a false trigger on background
  noise just wastes one transcription call that comes back empty, but a
  threshold that's too strict makes the whole feature look silently dead,
  so this deliberately leans toward over- rather than under-sensitive.
  The live level ring on the button (see above) is the fastest way to
  tell those apart if voice input still doesn't seem to be working: no
  movement at all as you talk means audio capture itself isn't reaching
  the app (wrong input device, OS-level mute, a permissions prompt you
  may have missed) — some movement but chunks never landing means the
  threshold, not the capture, is still the thing to retune. Separately —
  found while chasing down exactly this class of issue, not something a
  sandboxed environment could have surfaced on its own — a network that
  silently drops connections to huggingface.co rather than actively
  refusing them (a strict firewall, an odd proxy policy) could leave the
  very first attempt to load the model waiting far longer than
  reasonable, with the button stuck on grey and no explanation. Both
  `HF_HUB_ETAG_TIMEOUT` and `HF_HUB_DOWNLOAD_TIMEOUT` are now set
  explicitly (15s, up from the library's own default of 10s, without
  overriding a value you've already set yourself) so that fails fast
  and reports clearly instead.
  `MODEL_ID` in `voice_to_urdu.py` is still a one-line swap if the
  speed/accuracy balance isn't right for you, and
  `TARGET_CHUNK_SECONDS`/`SILENCE_RMS`/`SILENCE_HOLD_MS` on
  `UtteranceSegmenter` are the first things to retune if chunks feel too
  batched, cut mid-word, or still never trigger — these depend on your
  specific mic, room, and how patient you want the app to be before
  cutting.
- **Roman Urdu -> Urdu script accuracy** — `test_roman_urdu.py` checks
  the dictionary has no key silently mapping to two different spellings
  (an easy mistake when adding entries — Python's dict just keeps
  whichever definition ran last, with no warning), specific fixed bugs
  (a transposed-letter typo in "walaikum"; mid-word "an"/"in"/"un"
  previously being misread as word-final nasalization, e.g. turning
  "mangal" into "ماں" (mother) + garbage), and a broad set of newly-added
  vocabulary (days, months, tech terms, professions, places, transport,
  common connectors). Real Urdu speakers will still find gaps — the
  dictionary is the reliable path, character-by-character phonetic
  reconstruction for anything outside it is inherently approximate (Urdu
  keeps a lot of Arabic/Persian etymological spelling plain phonetics
  can't recover) — but this cuts the fallback down substantially and
  fixes what was actually wrong versus just imprecise.
- **.utw project file error handling** — `test_project_io.py` checks
  that a corrupted, wrong-type, or future-format-version file fails with
  a clear message instead of a raw Python traceback, and specifically
  that it fails *before* touching the canvas — no more losing your
  current document to a `document.clear()` that ran right before a
  malformed field crashed the rest of the load. A bad individual field
  (wrong type on one run's font size, etc.) is skipped/coerced instead of
  aborting the whole load. Export (PNG/JPG/SVG/PDF) had a related silent-
  failure bug: `QImage.save()` and Qt's SVG/PDF writers return `False` on
  failure rather than raising, so a bad export path used to report
  "Exported to ..." on the status bar while writing nothing at all —
  `test_render.py` now checks all three raise instead.
- **UI polish** — a deliberate system-font stack for the app chrome
  (toolbar/menus/dialogs — never the Urdu canvas text, which is
  unaffected), new icons for alignment and zoom replacing text-only
  toolbar buttons, hardcoded colors in the dialogs that had drifted from
  the actual theme palette fixed to match, a redesigned voice button
  (soft shadow, hover state, an ambient pulse while listening, a
  real-time level ring on top of that tracking actual input volume, and
  a rotating spinner while finishing up transcription after you stop),
  and a click-to-reset zoom percentage in the toolbar. Also surfaced a
  real bug the same way the zoom one was found: the voice button was
  positioning itself against Qt's pre-layout placeholder window size
  rather than the real one, landing in the wrong spot on every startup —
  same root cause and same fix pattern as the zoom issue, now checked in
  `test_zoom.py`.
- **The Font Manager / spinbox / dropdown icons and the floating voice
  button** on your specific OS — rendered and checked on Linux.

The app shows a clear dialog rather than crashing silently if something
goes wrong — paste whatever it says if you hit an issue.

## Known v1 limitations

- No pagination — text taller than the page just gets clipped.
- No Bold/Italic toggle buttons — use the style dropdown for a font's
  real weight variants instead (faking bold distorts Nastaliq's joins).
- Roman-Urdu conversion is phonetic best-effort for anything outside its
  dictionary, not guaranteed-correct spelling.
- Outline-based outputs (SVG, Copy as SVG, the curves PDF, EPS) draw only
  the glyph shapes: underline, strikethrough, and highlight color aren't
  carried into them (PNG/JPG and printing do show them).
- EPS has no transparency (a translucent colour is flattened onto white).

## Project structure

```
main.py               — window, menu, toolbar, cursor-anchored zoom/scroll, voice recording queue,
                         threaded export, autosave/recovery, Recent Files, Find & Replace, Print
canvas.py              — the page widget (RTL text, sticky formatting incl. underline/strikethrough/
                         highlight/letter+word spacing/line height, background image + border,
                         Roman-Urdu convert, live dictation)
dialogs.py             — Page Setup / Export / Font Manager / color picker / Find & Replace dialogs,
                         SearchableFontCombo (the font dropdown's type-to-filter popup)
exporter.py            — PNG/JPG/SVG/PDF/EPS/clipboard/print rendering, incl. background image + border,
                         the cropped curves-only SVG + PDF Copy as SVG uses, and its clipboard payload
text_outlines.py       — glyph-outline extraction used by SVG/PDF/EPS export and Copy as SVG (optionally
                         restricted to a character range, for selection-only copies)
font_gap.py            — EXPERIMENTAL gap between neighbouring words in different fonts (layout-only,
                         see its docstring for how to remove it)
project_io.py          — save/load for the app's own .utw project format (embeds background images
                         directly), with validated, non-destructive error handling for bad files
font_check.py          — heuristic that flags fonts unable to join Urdu letters
font_settings.py       — persisted font allowlist (Font Manager's checkbox state)
roman_urdu.py          — Roman Urdu -> Urdu script transliteration (dictionary + fuzzy + rules)
voice_to_urdu.py       — microphone recording, live-utterance segmentation, Whisper transcription
voice_overlay.py       — the floating draggable record button
download_model.py      — one-time local speech-model download (recommended)
theme.py               — the app's flat dark theme, UI font stack, and the icons/ SVG assets it uses
icons/                 — toolbar and control icons (SVG), incl. line-height and word-spacing
fonts/                 — bundled default font family, 4 weights (see license below)
test_render.py         — headless smoke test: Urdu shaping + export + export-failure handling, see Setup
test_zoom.py           — headless smoke test: cursor-anchored zoom math + startup positioning, see Setup
test_voice.py          — headless smoke test: voice transcription chunking/queue logic, see Setup
test_download_model.py — headless smoke test: download retry/verification logic, see Setup
test_roman_urdu.py     — headless smoke test: Roman Urdu -> Urdu script accuracy, see Setup
test_project_io.py     — headless smoke test: .utw save/load, incl. this round's fields, see Setup
test_svg_copy.py       — headless smoke test: Copy as SVG (Curves) cropping/selection + clipboard formats, see Setup
test_eps.py            — headless smoke test: EPS export structure + Ghostscript render comparison, see Setup
test_font_gap.py       — headless smoke test: font-boundary gap planning/layout/undo-safety/exports, see Setup
test_font_search.py    — headless smoke test: font dropdown search popup, see Setup
test_spacing.py        — headless smoke test: line height + word spacing canvas behavior, see Setup
test_spacing_toolbar.py — headless smoke test: the same, through the real toolbar controls, see Setup
test_prefs_startup.py  — headless smoke test: saved Preferences are applied on launch, see Setup
test_context_menu.py   — headless smoke test: right-click menu opens/works, ignores page zoom, see Setup
```

## Third-party credits

- `fonts/NotoNastaliqUrdu-*.ttf` — Google's Noto Nastaliq Urdu, © The Noto
  Project Authors, SIL Open Font License 1.1 (full text in
  `fonts/NotoNastaliqUrdu-LICENSE.txt`).
- `roman_urdu.py`'s dictionary and matching algorithm — ported from the
  MIT-licensed [romanUrdu2UrduTranslitration](https://github.com/spyhunk/romanUrdu2UrduTranslitration)
  by Hamza Afzal.
- Voice input model — [Systran/faster-whisper-medium](https://huggingface.co/Systran/faster-whisper-medium)
  on Hugging Face, the official faster-whisper conversion of OpenAI's
  Whisper "medium".
