"""
voice_to_urdu.py — microphone recording + Urdu speech-to-text, transcribed
live in short chunks as you speak (see UtteranceSegmenter below) rather
than only once at the end of a recording.

Uses faster-whisper (a CTranslate2 reimplementation of OpenAI's Whisper),
picked specifically because it does NOT pull in PyTorch as a dependency —
Whisper's original package does, which is a very heavy, fragile thing to
require just for this one feature.

Model: Systran/faster-whisper-medium — OpenAI's generic multilingual
Whisper "medium" (769M parameters). This app has moved around on the
speed/accuracy trade-off a few times: "small" first (felt inaccurate),
then a large Urdu-specific fine-tune (accurate, too slow), then medium
(still felt too slow — but that was before the chunking rework below
fixed the real problem, which was cutting into far more pieces than
necessary), then small again paired with the chunking fix (fast, but
accuracy came back as the complaint once speed stopped being one), then
medium once chunk sizing wasn't fighting itself (fewer, larger calls
meant a bigger model's extra cost per call mattered less overall), then
briefly small again when medium failed to allocate memory outright on
one machine. Back to medium: its files on Hugging Face are stored in
float16, and the failure traced to loading it with compute_type="int8"
(below) — that doesn't just read the file, it makes CTranslate2 hold
the float16 weights AND produce the int8 copy in memory at once, so the
PEAK memory while loading is noticeably higher than either format's own
resident size. get_model() below now loads medium as compute_type=
"float16" instead — the format it's already stored in, no on-the-fly
conversion, so no double-memory moment — trading a higher final resident
size and somewhat slower inference than int8 would have given for a
lower loading peak. If medium still fails to allocate even with that
fix, the machine's free RAM is very likely the binding constraint on
its own, not this particular inefficiency, and dropping MODEL_ID a size
(below) is the next lever. No pre-converted Urdu-specific model at this
size was available at the time this was written (only a large-v3-class
Urdu fine-tune — see below), so this is the generic multilingual
"medium" checkpoint, not an Urdu fine-tune. Options, roughly
fastest/smallest to slowest/most accurate:
    Systran/faster-whisper-base     (~140MB)  fastest, weakest accuracy
    Systran/faster-whisper-small    (~460MB)  faster than medium if it's
        still too slow, or medium still won't load, on your machine —
        noticeably weaker accuracy
    Systran/faster-whisper-medium   (~1.4GB, stored float16)  current
        default — see compute_type="float16" below for why
    kingabzpro/whisper-large-v3-urdu-ct2 (~1.5GB) fine-tuned specifically
        for Urdu, most accurate of these, but both loading it at startup
        and every chunk are noticeably slower on CPU, and it carries the
        same load-time considerations as medium above

A separate cost sat on top of all of the above, regardless of which size
was picked: faster-whisper's default `temperature` isn't one value, it's
a fallback LADDER (0.0, 0.2, 0.4, 0.6, 0.8, 1.0) — any decode that
doesn't look confident enough by its own heuristics gets silently
re-decoded from scratch at the next value, up to 6 full decode passes
for a single chunk. A language the checkpoint is less sure of than, say,
English or Spanish can trip that "not confident enough" check often, so
this was plausibly paying for several retries on nearly every chunk
rather than only occasionally — which would explain transcription
taking a roughly constant ~45-50s no matter how short the clip was
(each retry pays roughly the same cost, so the total scales with retry
*count*, not clip length). transcribe_urdu() below now pins temperature
to a single deterministic value, capping every chunk at one decode pass
— see its docstring for the trade-off.

Changing MODEL_ID below and re-running download_model.py is how you switch
— but delete the whisper_model/ folder FIRST. local_model_available()
(used by both download_model.py and get_model() below) only checks that
the required files exist and are large enough, not which model they
actually are, so a folder already holding a different, larger model's
files reads as "already downloaded": download_model.py would skip the
download entirely, and get_model() would go on loading the old model's
files regardless of what MODEL_ID now says. Delete the folder, then run
`python download_model.py` to fetch the model MODEL_ID now points to.

Loading prefers a local, explicitly-downloaded copy (see download_model.py)
over faster-whisper's default lazy-download-into-the-HuggingFace-cache
behavior — that default relies on symlinks, which need Developer Mode or
admin rights on Windows and otherwise print an alarming (but non-fatal)
warning on every startup. Run download_model.py once and that warning
never appears again; skip it and voice input still works, just noisier.

Both faster-whisper and sounddevice are optional — see
requirements-voice.txt. The rest of the app works fully without them.
"""

import os
import threading
from pathlib import Path

import numpy as np

# huggingface_hub reads these at its own import time, so they need setting
# before anything here imports it (get_model() below does that lazily,
# but sets these first regardless of exactly when that happens). Without
# an explicit value, a network that silently drops packets to
# huggingface.co rather than actively refusing the connection (a strict
# corporate firewall, a proxy with an odd blocklist policy, etc.) can hang
# well past huggingface_hub's already-short 10s default — since that
# default only bounds the HTTP-level wait, not a lower-level connection
# that never resolves at all. setdefault() leaves a value already set in
# the environment (by the person running this, or their OS) alone.
os.environ.setdefault("HF_HUB_ETAG_TIMEOUT", "15")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "15")

MODEL_ID = "Systran/faster-whisper-medium"
MODEL_DIR = Path(__file__).parent / "whisper_model"
SAMPLE_RATE = 16000  # what Whisper's models are trained on — recording at
# this rate directly avoids needing to resample later


class MicrophoneUnavailable(RuntimeError):
    pass


class Recorder:
    """Accumulates microphone input into an in-memory buffer from start()
    until stop(). The actual capture happens on sounddevice's own internal
    audio thread via its callback, so start()/stop() themselves don't block
    the GUI thread. pull_new_audio() lets a caller (see the polling timer
    in main.py) incrementally consume audio *while still recording*, which
    is what makes live/chunked transcription possible — stop() still
    returns the rest exactly as before for anyone that just wants the
    whole recording at once."""

    def __init__(self, samplerate=SAMPLE_RATE):
        self.samplerate = samplerate
        self._chunks = []
        self._pulled = (
            0  # how many of self._chunks pull_new_audio() has already returned
        )
        self._stream = None

    def start(self):
        try:
            import sounddevice as sd
        except Exception as e:
            raise MicrophoneUnavailable(
                "The 'sounddevice' package (and its PortAudio library) isn't available. "
                "See requirements-voice.txt for how to install voice input."
            ) from e

        self._chunks = []
        self._pulled = 0
        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate,
                channels=1,
                dtype="float32",
                callback=self._callback,
            )
            self._stream.start()
        except Exception as e:
            self._stream = None
            raise MicrophoneUnavailable(f"Couldn't open the microphone: {e}") from e

    def _callback(self, indata, _frames, _time_info, _status):
        # Runs on sounddevice's own audio thread, not the GUI thread —
        # list.append() is atomic under the GIL, so this is safe to do
        # concurrently with pull_new_audio() reading self._chunks below.
        self._chunks.append(indata.copy())

    def is_recording(self) -> bool:
        return self._stream is not None

    def pull_new_audio(self) -> np.ndarray:
        """Returns audio captured since the last call to this method (or
        since start(), for the first call), as a flat float32 array, and
        marks it consumed. Safe to call frequently (e.g. from a polling
        timer) — returns an empty array if nothing new has arrived yet.
        Snapshotting the length before slicing (rather than slicing then
        reading the length after) keeps this correct even though the
        audio callback can append to self._chunks concurrently, on its
        own thread, in between."""
        total = len(self._chunks)
        new_chunks = self._chunks[self._pulled : total]
        self._pulled = total
        if not new_chunks:
            return np.zeros((0,), dtype=np.float32)
        return np.concatenate(new_chunks, axis=0).reshape(-1)

    def stop(self) -> np.ndarray:
        """Stops the stream and returns whatever audio hasn't yet been
        retrieved via pull_new_audio() (the entire recording, for a
        caller that never called pull_new_audio() at all). Safe to call
        even if start() was never called or already stopped — returns an
        empty array in that case."""
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        return self.pull_new_audio()


class UtteranceSegmenter:
    """Buffers incoming audio and decides when it's ready to send off for
    transcription — a natural pause once there's a worthwhile amount of
    speech buffered, a longer pause regardless of size, or the buffer
    growing long enough that waiting any further would stop feeling
    "live" at all.

    "Worthwhile amount" needs explaining, since it's tempting to assume
    shorter chunks always mean a more live feel — that's backwards for
    Whisper-family models specifically. The encoder's attention window is
    a fixed 30 seconds; shorter audio gets silently padded up to that
    same size rather than processed proportionally faster, so a 2-second
    clip and a 12-second clip cost roughly the same to transcribe. Cutting
    on every short pause (this app's first attempt at "live") maximizes
    exactly the wrong thing: the number of times that mostly-fixed cost
    gets paid over the course of a dictation session. TARGET_CHUNK_SECONDS
    below waits for a real pause *and* a chunk big enough to be worth that
    cost, so the wait-per-chunk is spent transcribing several seconds of
    real speech instead of mostly padding. This trades away sub-second
    word-by-word feedback — genuinely not achievable with an offline
    Whisper model on CPU, no matter how the chunking is tuned — for
    periodic updates every several seconds that don't each carry the same
    overhead as transcribing the whole recording would.

    A simple short-time energy (RMS) check stands in for a full voice-
    activity-detection library: good enough for one person dictating into
    a mic at a normal distance, without adding a compiled dependency
    (webrtcvad and similar) on top of sounddevice/faster-whisper. If your
    room is noisy enough — or your mic quiet enough — that this misfires
    (cutting mid-word, or never triggering at all), SILENCE_RMS is the
    first constant to adjust: raise it if silence isn't being recognized
    as silence, lower it if normal speech is being treated as silence.
    Deliberately biased toward "too sensitive" rather than "too strict"
    here — a false positive costs one wasted transcription call on a
    near-silent clip (which comes back as empty text and is discarded,
    see main.py's _on_chunk_transcribed), but a false negative that never
    triggers at all makes the entire feature look broken with no
    indication why. last_rms (below) exposes the most recent window's
    level so the UI can show a live meter — both a genuine "is this
    thing on" indicator while you're talking, and a fast way to tell a
    capture problem (meter never moves at all) from a threshold problem
    (meter moves, chunks still don't trigger) if voice input seems dead.
    """

    SILENCE_RMS = 0.004  # short windows averaging at/below this = "silence" --
    # see the class docstring for why this leans sensitive
    WINDOW_MS = 30  # analysis window size
    SILENCE_HOLD_MS = 900  # this much continuous trailing silence, PLUS at least
    # TARGET_CHUNK_SECONDS already buffered -> cut now
    EXTENDED_SILENCE_HOLD_MS = (
        2500  # this much trailing silence cuts regardless of size --
    )
    # long enough that the person has clearly stopped, not
    # just paused for breath mid-thought
    TARGET_CHUNK_SECONDS = 5  # don't cut on an ordinary pause until roughly this much
    # audio has accumulated -- see class docstring for why
    # small-and-frequent is actually the slower choice here
    MIN_SPEECH_MS = (
        250  # ignore a buffer with less real speech than this (mic noise, breath)
    )
    MAX_CHUNK_SECONDS = 14  # force a cut even mid-sentence past this long, so one long
    # run-on sentence can't block live feedback indefinitely

    def __init__(self, samplerate=SAMPLE_RATE):
        self.samplerate = samplerate
        self._window_size = max(1, int(samplerate * self.WINDOW_MS / 1000))
        self._unprocessed = np.zeros(
            (0,), dtype=np.float32
        )  # < 1 window, awaiting more samples
        self._pending_windows = (
            []
        )  # committed windows for the current in-progress utterance
        self._pending_samples = 0
        self._trailing_silence_ms = 0.0
        self._speech_ms_in_pending = 0.0
        self.last_rms = 0.0  # most recent window's level — read this for a live meter
        self.ever_heard_speech = False  # True once any window has crossed SILENCE_RMS —
        # lets main.py warn if a whole recording goes by without it

    def feed(self, new_audio: np.ndarray):
        """Add newly captured audio (e.g. from Recorder.pull_new_audio()).
        Returns a list of finished utterance segments — each a flat
        float32 array — ready to transcribe. Usually 0 or 1 per call at a
        normal polling interval, but returns as many as apply."""
        if new_audio is None or new_audio.size == 0:
            return []
        self._unprocessed = np.concatenate([self._unprocessed, new_audio.reshape(-1)])
        ready = []
        w = self._window_size
        while len(self._unprocessed) >= w:
            window, self._unprocessed = self._unprocessed[:w], self._unprocessed[w:]
            self._pending_windows.append(window)
            self._pending_samples += w
            rms = float(np.sqrt(np.mean(np.square(window))))
            self.last_rms = rms
            is_speech = rms >= self.SILENCE_RMS
            if is_speech:
                self.ever_heard_speech = True
                self._speech_ms_in_pending += self.WINDOW_MS
                self._trailing_silence_ms = 0.0
            else:
                self._trailing_silence_ms += self.WINDOW_MS

            pending_seconds = self._pending_samples / self.samplerate

            if self._trailing_silence_ms >= self.EXTENDED_SILENCE_HOLD_MS:
                # Waited long enough that the person has clearly stopped,
                # regardless of how little we've got -- cut now if it's
                # real speech, else drop it as noise.
                if self._speech_ms_in_pending >= self.MIN_SPEECH_MS:
                    ready.append(self._cut_and_reset())
                else:
                    self._reset()
            elif (
                self._trailing_silence_ms >= self.SILENCE_HOLD_MS
                and pending_seconds >= self.TARGET_CHUNK_SECONDS
            ):
                # The common case: a natural pause, and enough buffered
                # that transcribing it now is worth the (mostly fixed)
                # per-call cost.
                ready.append(self._cut_and_reset())
            elif pending_seconds >= self.MAX_CHUNK_SECONDS:
                # No pause long enough has come along at all (a long
                # run-on sentence) -- cut anyway so live feedback isn't
                # blocked indefinitely.
                ready.append(self._cut_and_reset())
        return ready

    def flush(self):
        """Called once recording stops: returns the final in-progress
        segment if it has enough real speech in it to be worth
        transcribing, else None. Discards any leftover sub-window audio
        (a few ms at most) either way."""
        segment = None
        if self._speech_ms_in_pending >= self.MIN_SPEECH_MS and self._pending_windows:
            segment = np.concatenate(self._pending_windows)
        self._reset()
        return segment

    def _cut_and_reset(self):
        segment = np.concatenate(self._pending_windows)
        self._reset()
        return segment

    def _reset(self):
        self._pending_windows = []
        self._pending_samples = 0
        self._trailing_silence_ms = 0.0
        self._speech_ms_in_pending = 0.0


_model_cache = {}
_model_cache_lock = threading.Lock()  # see get_model() — keeps ModelLoadThread's
# startup preload and an eager first
# TranscribeThread (if recording starts
# before preload finishes) from both
# building their own separate copy of
# the model at once


# The files faster-whisper actually needs to load a local model, and the
# minimum size (bytes) each one has to be to count as real rather than an
# empty or truncated stub from an interrupted download -- checking mere
# existence would call a half-finished download "available" and then fail
# every time it's actually loaded, with no way to tell the two situations
# apart from the outside. This is deliberately PER FILE rather than one
# shared number for all four: a single threshold sized to be safely below
# model.bin (gigabytes) is nowhere near safely below config.json, which is
# only a couple KB to begin with -- an earlier version of this check used
# one shared 1MB minimum, which rejected config.json and vocabulary.txt on
# literally every download, including perfectly good ones, forever. Real
# sizes for Systran/faster-whisper-medium, for reference:
#   config.json      2.26 KB      tokenizer.json   2.2 MB
#   vocabulary.txt   460 KB       model.bin        1.53 GB
# (https://huggingface.co/Systran/faster-whisper-medium/tree/main). Each
# threshold below is set well under its real size, so switching MODEL_ID
# to a smaller/larger model (see the module docstring) or small natural
# variation between model sizes can't false-positive a genuinely complete
# download the way one shared number already did once.
_MIN_FILE_BYTES = {
    "config.json": 500,  # real size ~2.26 KB
    "vocabulary.txt": 50_000,  # real size ~460 KB
    "tokenizer.json": 200_000,  # real size ~2.2 MB
    "model.bin": 10_000_000,  # real size ~140MB (base) to ~1.53GB (medium)
}
_REQUIRED_MODEL_FILES = tuple(_MIN_FILE_BYTES)


def local_model_available() -> bool:
    if not MODEL_DIR.exists():
        return False
    return all(
        (MODEL_DIR / name).is_file() and (MODEL_DIR / name).stat().st_size >= min_bytes
        for name, min_bytes in _MIN_FILE_BYTES.items()
    )


def _local_model_incomplete() -> bool:
    """True only for the specific case of "the folder exists but is
    missing (or too small to be real) required files" — worth a distinct,
    clearer error message than either "no local copy" or a raw
    ctranslate2 load failure."""
    return (
        MODEL_DIR.exists() and any(MODEL_DIR.iterdir()) and not local_model_available()
    )


def _bad_model_files():
    """Required files that are either missing or too small to be the
    real thing -- see _MIN_FILE_BYTES. Used only to build a specific,
    actionable error message; local_model_available() above is the
    actual source of truth."""
    bad = []
    for name, min_bytes in _MIN_FILE_BYTES.items():
        path = MODEL_DIR / name
        if not path.is_file() or path.stat().st_size < min_bytes:
            bad.append(name)
    return bad


def get_model(model_id=MODEL_ID):
    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        raise MicrophoneUnavailable(
            "The 'faster-whisper' package isn't available. "
            "See requirements-voice.txt for how to install voice input."
        ) from e

    if _local_model_incomplete():
        raise MicrophoneUnavailable(
            f"The local model folder ({MODEL_DIR}) exists but looks incomplete or "
            f"corrupted — missing or unexpectedly small: {', '.join(_bad_model_files())}. "
            f"This usually means a download got interrupted partway through.\n\n"
            f"Delete that folder and run 'python download_model.py' again."
        )

    key = str(MODEL_DIR) if local_model_available() else model_id
    # Held across the cache check *and* the (slow) construction below, not
    # just around the dict write -- otherwise two threads can each see
    # "not cached yet", and both pay the full load cost concurrently. The
    # main case this guards against: ModelLoadThread's startup preload is
    # still running when the user starts recording and speaks fast enough
    # that a TranscribeThread calls get_model() before preload finishes.
    with _model_cache_lock:
        if key not in _model_cache:
            try:
                # compute_type="float16", not "int8": Systran/faster-whisper-medium's
                # files on disk are stored in float16 (see its HF model card's
                # conversion command) -- loading with compute_type="int8" doesn't
                # just read the file, it has CTranslate2 hold the float16 weights
                # AND produce the int8 copy in memory at the same time, so the
                # PEAK memory during loading is noticeably higher than either
                # format's own resident size. "float16" here loads the file as
                # stored, no on-the-fly conversion, which is the more memory-safe
                # choice specifically for a model this size on a RAM-constrained
                # machine -- at the cost of a higher final resident size than
                # int8 would have (float16 keeps the full 16-bit weights resident,
                # not a quantized-down copy) and somewhat slower inference. If
                # MODEL_ID above is ever dropped to something smaller (base/small),
                # switch this back to "int8": a smaller model's float16-to-int8
                # conversion peak is much less likely to be the binding constraint,
                # and int8 is faster and lighter at rest once it's loaded.
                # cpu_threads: CTranslate2's own default (cpu_threads=0) doesn't
                # always use every available core — pointing it at the actual
                # core count directly measurably speeds up CPU inference.
                _model_cache[key] = WhisperModel(
                    key,
                    device="cpu",
                    compute_type="float16",
                    cpu_threads=os.cpu_count() or 4,
                )
            except Exception as e:
                hint = (
                    ""
                    if local_model_available()
                    else "\n\nTip: run 'python download_model.py' once to download it permanently "
                    "instead of on every first use."
                )
                raise MicrophoneUnavailable(
                    f"Couldn't load the Urdu speech model. The first use of voice input needs an "
                    f"internet connection to download it (cached afterwards).{hint}\n\n"
                    f"Underlying error: {e}"
                ) from e
    return _model_cache[key]


def transcribe_urdu(
    audio: np.ndarray, model_id=MODEL_ID, samplerate=SAMPLE_RATE
) -> str:
    """audio: flat float32 mono array (as returned by Recorder.stop()).
    Returns the transcribed Urdu text, or "" if no speech was recognized."""
    if audio is None or audio.size == 0:
        return ""
    model = get_model(model_id)
    segments, _info = model.transcribe(
        audio,
        language="ur",
        task="transcribe",
        beam_size=3,  # explores a few candidate decodings instead of
        # just taking the greedy best guess at each step —
        # a real, well-established accuracy improvement
        # over beam_size=1. Trimmed from 5: most of beam
        # search's benefit over greedy shows up going to
        # ~3, and decoder cost (unlike the encoder — the
        # fixed-cost part discussed in UtteranceSegmenter's
        # docstring) scales with this *and* now only runs
        # once per chunk instead of up to 6 times (see
        # temperature below), so it's worth being a bit
        # more careful with here than before that fix.
        temperature=0.0,  # single deterministic decode, no fallback
        # retries — see the module docstring's "A separate
        # cost..." paragraph for why this is almost
        # certainly the main fix for the ~45-50s-per-chunk
        # slowness. Trade-off: a genuinely garbled/
        # ambiguous segment no longer gets a second,
        # more-random attempt at a better reading. Rare
        # for a person deliberately dictating close to the
        # mic, and vad_filter below already keeps silence/
        # noise from being sent in as a "segment" at all.
        condition_on_previous_text=False,  # each internally-detected segment
        # is decoded independently rather than
        # conditioned on what came right before it in
        # the same chunk. The default (True) lets one
        # early mistake or hallucination drag down
        # everything decoded after it within that
        # chunk — a bigger risk now that chunks
        # regularly hold several seconds of speech
        # (see UtteranceSegmenter) rather than one
        # short phrase.
        vad_filter=True,  # trims leading/trailing silence, reduces hallucinated text on quiet clips
    )
    return "".join(seg.text for seg in segments).strip()
