"""
test_voice.py — headless check of Voice to Urdu's live-transcription
pipeline, in three independent layers:

  1. UtteranceSegmenter — does synthetic audio get cut into utterances at
     the right points (pauses, forced max-length, noise rejection)?
  2. PageCanvas.insert_dictated_text — do live chunks land with correct
     spacing, in a real (if headless) QTextEdit?
  3. MainWindow's recording queue/state machine — with voice_to_urdu
     replaced by an in-memory fake (no real mic or model needed), does a
     burst of chunks queue and drain in order, does the button state
     transition correctly, and does a failed chunk recover cleanly?

Doesn't need a microphone, faster-whisper, or sounddevice installed —
layer 3 fakes voice_to_urdu entirely, the same way you'd fake any slow
external dependency in a test.

    python test_voice.py
"""

import sys
import time
import types
import tempfile

import numpy as np
from PyQt6.QtWidgets import QApplication, QMessageBox

app = QApplication(sys.argv)

results = []


def check(name, cond, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append(cond)


def pump(seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


# ======================================================================
# Layer 1 — UtteranceSegmenter, with the real implementation
# ======================================================================
print("--- segmenter ---")
from voice_to_urdu import UtteranceSegmenter, Recorder, SAMPLE_RATE  # noqa: E402
import voice_to_urdu as vu  # noqa: E402 -- for the model-folder completeness checks below

rng = np.random.default_rng(0)


def speech(seconds, amp=0.2):
    return rng.standard_normal(int(SAMPLE_RATE * seconds)).astype(np.float32) * amp


def silence(seconds, amp=0.0005):
    return rng.standard_normal(int(SAMPLE_RATE * seconds)).astype(np.float32) * amp


def feed_in_pieces(seg, audio, piece_ms=200):
    piece_len = int(SAMPLE_RATE * piece_ms / 1000)
    ready = []
    for i in range(0, len(audio), piece_len):
        ready.extend(seg.feed(audio[i : i + piece_len]))
    return ready


seg = UtteranceSegmenter()
ready = feed_in_pieces(seg, np.concatenate([speech(1.5), silence(1.0)]))
check(
    "short speech + short pause -> NOT cut yet (below TARGET_CHUNK_SECONDS, "
    "below EXTENDED_SILENCE_HOLD_MS)",
    len(ready) == 0,
    f"got {len(ready)}",
)

seg = UtteranceSegmenter()
ready = feed_in_pieces(seg, np.concatenate([speech(1.5), silence(3.0)]))
check(
    "short speech + a LONG pause -> cut anyway via the extended-silence fallback",
    len(ready) == 1,
    f"got {len(ready)}",
)

seg = UtteranceSegmenter()
ready = feed_in_pieces(
    seg, np.concatenate([speech(1.0), silence(1.0), speech(0.8), silence(1.0)])
)
check(
    "two short utterances joined by a short pause -> combined into ONE chunk, "
    "not cut on the short pause between them (this is the point of the fix: "
    "don't pay Whisper's fixed per-call cost twice for two tiny utterances)",
    len(ready) == 0,
    f"got {len(ready)} (should still be buffering, waiting for "
    f"either enough total content or a long-enough pause)",
)
flushed = seg.flush()
check(
    "...and flush() at stop-time delivers that combined buffer as one segment",
    flushed is not None and flushed.size > 0,
)

seg = UtteranceSegmenter()
long_enough_speech = UtteranceSegmenter.TARGET_CHUNK_SECONDS + 0.5
ready = feed_in_pieces(seg, np.concatenate([speech(long_enough_speech), silence(1.0)]))
check(
    "once TARGET_CHUNK_SECONDS worth has accumulated, a normal pause DOES cut "
    "immediately (no need to wait for the extended-silence fallback)",
    len(ready) == 1,
    f"got {len(ready)}",
)

seg = UtteranceSegmenter()
ready = feed_in_pieces(seg, speech(UtteranceSegmenter.MAX_CHUNK_SECONDS + 4))
check(
    "run-on speech forces a cut near MAX_CHUNK_SECONDS",
    len(ready) >= 1
    and abs(len(ready[0]) / SAMPLE_RATE - UtteranceSegmenter.MAX_CHUNK_SECONDS) < 0.2,
)

seg = UtteranceSegmenter()
ready = feed_in_pieces(seg, np.concatenate([speech(0.05), silence(3.0)]))
check(
    "sub-minimum noise blip produces no segment even after a long pause",
    len(ready) == 0,
    f"got {len(ready)}",
)

seg = UtteranceSegmenter()
feed_in_pieces(seg, speech(1.2))  # still "talking", never paused
flushed = seg.flush()
check(
    "flush() captures an in-progress utterance at stop-time",
    flushed is not None and flushed.size > 0,
)

r = Recorder()
r._chunks, r._pulled, r._stream = [], 0, object()
r._chunks.append(speech(0.5))
first_pull = r.pull_new_audio()
r._chunks.append(speech(0.3))
r._stream = None
remainder = r.stop()
check(
    "Recorder.pull_new_audio/stop split audio correctly with no loss/duplication",
    first_pull.size == int(SAMPLE_RATE * 0.5)
    and remainder.size == int(SAMPLE_RATE * 0.3),
)

# --- local model folder completeness (catches an interrupted download) ---
import os
import shutil as _shutil
from pathlib import Path

_orig_model_dir = vu.MODEL_DIR
_tmp_model_root = tempfile.mkdtemp()


def _write_fake_model_file(path, size):
    with open(path, "wb") as f:
        f.truncate(size)  # sparse file of the given size -- no need to


try:
    # Complete folder: all required files present *and* large enough to be
    # the real thing, not just present -- see the truncated case below.
    complete = os.path.join(_tmp_model_root, "complete")
    os.makedirs(complete)
    for name, min_bytes in vu._MIN_FILE_BYTES.items():
        _write_fake_model_file(os.path.join(complete, name), min_bytes + 100)
    vu.MODEL_DIR = Path(complete)
    check(
        "local_model_available() is True when all required files are present and full-sized",
        vu.local_model_available(),
    )
    check(
        "_local_model_incomplete() is False for a complete folder",
        not vu._local_model_incomplete(),
    )

    # Regression test for a real bug: an earlier version of this check used
    # one shared minimum sized for model.bin (megabytes), which rejected
    # config.json and vocabulary.txt on literally every download, forever,
    # because those two are legitimately much smaller than model.bin to
    # begin with -- deleting and re-downloading could never fix it. Write
    # every file at its REAL observed size (not just "min + 100") so this
    # actually catches a too-strict threshold instead of trivially passing.
    real_sized = os.path.join(_tmp_model_root, "real-sized")
    os.makedirs(real_sized)
    real_sizes = {
        "config.json": 2_260,  # actual size on huggingface.co
        "vocabulary.txt": 460_000,
        "tokenizer.json": 2_200_000,
        "model.bin": 1_530_000_000,
    }
    for name, size in real_sizes.items():
        _write_fake_model_file(os.path.join(real_sized, name), size)
    vu.MODEL_DIR = Path(real_sized)
    check(
        "local_model_available() accepts files at their real observed sizes -- "
        "config.json ~2.26KB and vocabulary.txt ~460KB are correctly NOT flagged "
        "as too small, the way one shared multi-megabyte minimum once did",
        vu.local_model_available(),
    )

    # Partial folder: an interrupted download (missing model.bin, e.g.)
    partial = os.path.join(_tmp_model_root, "partial")
    os.makedirs(partial)
    _write_fake_model_file(
        os.path.join(partial, "config.json"), vu._MIN_FILE_BYTES["config.json"] + 100
    )
    vu.MODEL_DIR = Path(partial)
    check(
        "local_model_available() is False for a partial folder",
        not vu.local_model_available(),
    )
    check(
        "_local_model_incomplete() is True for a partial folder (not just 'no local copy')",
        vu._local_model_incomplete(),
    )

    # Truncated folder (regression test for "couldn't load model, click to
    # retry" looping forever on a completed-looking download): every
    # required file exists -- a naive existence check would call this
    # complete -- but model.bin is a tiny stub, as an interrupted transfer
    # or a saved error page would leave behind.
    truncated = os.path.join(_tmp_model_root, "truncated")
    os.makedirs(truncated)
    for name, min_bytes in vu._MIN_FILE_BYTES.items():
        size = 200 if name == "model.bin" else min_bytes + 100
        _write_fake_model_file(os.path.join(truncated, name), size)
    vu.MODEL_DIR = Path(truncated)
    check(
        "local_model_available() is False when a required file exists but is too small "
        "(a truncated download, not a real model file)",
        not vu.local_model_available(),
    )
    check(
        "_local_model_incomplete() is True for a truncated folder",
        vu._local_model_incomplete(),
    )
    check(
        "the incomplete-folder diagnosis calls out the truncated file specifically, "
        "not the files that are actually fine",
        "model.bin" in vu._bad_model_files()
        and "config.json" not in vu._bad_model_files(),
    )

    # No folder at all: this is the ordinary "no local copy yet" case,
    # not an error -- get_model() should fall through to a normal
    # lazy-download attempt, not report "incomplete".
    vu.MODEL_DIR = Path(os.path.join(_tmp_model_root, "does_not_exist"))
    check(
        "_local_model_incomplete() is False when there's no folder at all "
        "(that's the normal 'not downloaded yet' case, not an error)",
        not vu._local_model_incomplete(),
    )
finally:
    vu.MODEL_DIR = _orig_model_dir
    _shutil.rmtree(_tmp_model_root, ignore_errors=True)

# --- transcribe_urdu() decode params (regression test for the fix for
# transcription taking a roughly constant ~45-50s no matter how short the
# clip was -- see the "A separate cost..." paragraph in voice_to_urdu.py's
# module docstring). Whatever WhisperModel.transcribe() actually receives
# is what matters here, so fake faster_whisper itself rather than mocking
# transcribe_urdu() -- a real bug in these kwargs would otherwise pass.
import threading as _threading

_had_faster_whisper = "faster_whisper" in sys.modules
_orig_faster_whisper = sys.modules.get("faster_whisper")
_orig_model_cache = dict(vu._model_cache)
vu._model_cache.clear()

captured_kwargs = {}


class _FakeSegment:
    def __init__(self, text):
        self.text = text


class _FakeWhisperModelForKwargs:
    def __init__(self, *a, **kw):
        pass

    def transcribe(self, audio, **kwargs):
        captured_kwargs.update(kwargs)
        return [_FakeSegment("result")], None


fake_faster_whisper = types.ModuleType("faster_whisper")
fake_faster_whisper.WhisperModel = _FakeWhisperModelForKwargs
sys.modules["faster_whisper"] = fake_faster_whisper
try:
    text = vu.transcribe_urdu(speech(1.0), model_id="fake-kwargs-model")
    check(
        "transcribe_urdu() returns the joined segment text",
        text == "result",
        repr(text),
    )
    check(
        "temperature pinned to a single deterministic value (the main speed fix -- "
        "faster-whisper's default is a 6-value fallback ladder, up to 6 full "
        "decode passes per chunk)",
        captured_kwargs.get("temperature") == 0.0,
        captured_kwargs.get("temperature"),
    )
    check(
        "beam_size trimmed from the old default of 5 (now only paid once per "
        "chunk instead of up to 6 times, so it's worth being leaner here too)",
        captured_kwargs.get("beam_size") == 3,
        captured_kwargs.get("beam_size"),
    )
    check("vad_filter still on", captured_kwargs.get("vad_filter") is True)
    check(
        "condition_on_previous_text still off",
        captured_kwargs.get("condition_on_previous_text") is False,
    )
finally:
    vu._model_cache.clear()
    vu._model_cache.update(_orig_model_cache)
    if _had_faster_whisper:
        sys.modules["faster_whisper"] = _orig_faster_whisper
    else:
        del sys.modules["faster_whisper"]

# --- get_model() serializes concurrent construction (regression test for
# ModelLoadThread's startup preload racing an eager first TranscribeThread
# and each building its own separate, slow copy of the model) ---
_orig_model_cache = dict(vu._model_cache)
vu._model_cache.clear()
construction_count = {"n": 0}


class _FakeWhisperModelSlow:
    def __init__(self, *a, **kw):
        time.sleep(0.15)  # long enough that concurrent callers overlap
        construction_count["n"] += 1


fake_faster_whisper_slow = types.ModuleType("faster_whisper")
fake_faster_whisper_slow.WhisperModel = _FakeWhisperModelSlow
sys.modules["faster_whisper"] = fake_faster_whisper_slow
try:
    model_results = []
    threads = [
        _threading.Thread(
            target=lambda: model_results.append(vu.get_model("fake-concurrent-model"))
        )
        for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    check(
        "concurrent get_model() calls build the model exactly once",
        construction_count["n"] == 1,
        construction_count["n"],
    )
    check(
        "...and every caller gets back that same instance",
        len(model_results) == 5 and all(r is model_results[0] for r in model_results),
    )
finally:
    vu._model_cache.clear()
    vu._model_cache.update(_orig_model_cache)
    if _had_faster_whisper:
        sys.modules["faster_whisper"] = _orig_faster_whisper
    else:
        del sys.modules["faster_whisper"]

# ======================================================================
# Layer 2 — canvas dictation insertion/spacing
# ======================================================================
print("\n--- canvas insertion ---")
from canvas import PageCanvas  # noqa: E402

canvas = PageCanvas(800, 1000, default_family="Arial")
canvas.insert_dictated_text("پہلا")
canvas.insert_dictated_text("دوسرا")
canvas.insert_dictated_text("   ")  # no-op
text = canvas.toPlainText()
check(
    "chunks join with exactly one space, blank chunk ignored",
    text == "پہلا دوسرا",
    repr(text),
)

canvas2 = PageCanvas(800, 1000, default_family="Arial")
canvas2.insertPlainText("ٹائپ کیا ")  # user's own typing, already ends in a space
canvas2.insert_dictated_text("بولا")
check(
    "no double space after the user's own trailing space",
    canvas2.toPlainText() == "ٹائپ کیا بولا",
    repr(canvas2.toPlainText()),
)

# ======================================================================
# Layer 3 — MainWindow's recording queue/state machine, voice_to_urdu faked
# ======================================================================
print("\n--- recording state machine (voice_to_urdu faked, no real mic/model) ---")

fake = types.ModuleType("voice_to_urdu")


class MicrophoneUnavailable(RuntimeError):
    pass


class FakeRecorder:
    instances = []

    def __init__(self, samplerate=16000):
        self._chunks, self._pulled, self._started = [], 0, False
        FakeRecorder.instances.append(self)

    def start(self):
        self._started = True

    def push(self, label):
        self._chunks.append(np.full(4, float(label), dtype=np.float32))

    def pull_new_audio(self):
        total = len(self._chunks)
        new = self._chunks[self._pulled : total]
        self._pulled = total
        return np.concatenate(new) if new else np.zeros((0,), dtype=np.float32)

    def stop(self):
        self._started = False
        return self.pull_new_audio()

    def is_recording(self):
        return self._started


class FakeSegmenter:
    def __init__(self, samplerate=16000):
        self.last_rms = 0.0
        self.ever_heard_speech = False

    def feed(self, new_audio):
        if new_audio.size:
            self.last_rms = (
                0.05  # a plausible non-silent reading, for set_audio_level()
            )
            self.ever_heard_speech = True
            return [new_audio]
        return []

    def flush(self):
        return None


FAIL_ON_LABEL = {}


def transcribe_urdu(audio, model_id=None, samplerate=16000):
    label = int(audio[0])
    time.sleep(0.05)
    if FAIL_ON_LABEL.get(label):
        raise RuntimeError(f"simulated failure on chunk {label}")
    return f"chunk{label}"


fake.MicrophoneUnavailable = MicrophoneUnavailable
fake.Recorder = FakeRecorder
fake.UtteranceSegmenter = FakeSegmenter
fake.transcribe_urdu = transcribe_urdu
fake.get_model = lambda model_id=None: object()
fake.local_model_available = lambda: True
fake.MODEL_ID = "fake-model"
sys.modules["voice_to_urdu"] = fake

critical_calls = []
QMessageBox.critical = staticmethod(
    lambda *a, **kw: (critical_calls.append(a), QMessageBox.StandardButton.Ok)[1]
)

from PyQt6.QtCore import QSettings as _QSettings

_settings_tmp_dir = tempfile.mkdtemp()
_QSettings.setPath(
    _QSettings.Format.NativeFormat, _QSettings.Scope.UserScope, _settings_tmp_dir
)
_QSettings.setPath(
    _QSettings.Format.IniFormat, _QSettings.Scope.UserScope, _settings_tmp_dir
)

from PyQt6.QtCore import QStandardPaths as _QStandardPaths

# Isolates QStandardPaths.AppDataLocation too -- not just QSettings above --
# so MainWindow's autosave-recovery check can never find a real recovery
# file and block this test on a dialog with nothing to click.
_QStandardPaths.setTestModeEnabled(True)

import main  # noqa: E402

win = main.MainWindow()
pump(0.2)
check(
    "button reaches idle after (fake) model load", win.voice_overlay.state() == "idle"
)

# Three chunks arrive faster than the fake 50ms-per-chunk transcription
# can keep up -- queue should hold the backlog and drain it IN ORDER.
win._on_voice_button_clicked()  # start
recorder = FakeRecorder.instances[-1]
for label in (1, 2, 3):
    recorder.push(label)
    win._poll_recording()
check(
    "stays 'listening' while chunks are mid-transcription",
    win.voice_overlay.state() == "listening",
)
pump(1.0)
check(
    "all three chunks inserted in spoken order",
    win.canvas.toPlainText() == "chunk1 chunk2 chunk3",
    repr(win.canvas.toPlainText()),
)

# A late chunk arrives right as Stop is clicked -- the final flush should
# still drain it before settling to idle.
recorder.push(4)
win._poll_recording()
win._on_voice_button_clicked()  # stop
pump(1.0)
check(
    "late chunk drains via the post-stop flush",
    win.canvas.toPlainText() == "chunk1 chunk2 chunk3 chunk4",
    repr(win.canvas.toPlainText()),
)
check("settles back to idle", win.voice_overlay.state() == "idle")
check("poll timer stopped", not win._poll_timer.isActive())

# Stop with zero speech at all -- should return straight to idle, no error.
win._on_voice_button_clicked()
win._on_voice_button_clicked()
pump(0.3)
check(
    "start+stop with no speech settles to idle without error",
    win.voice_overlay.state() == "idle" and len(critical_calls) == 0,
)

# A chunk failing mid-recording should show one error and recover to
# 'listening' (mic is still open), not silently drop to idle.
FAIL_ON_LABEL[10] = True
win._on_voice_button_clicked()
FakeRecorder.instances[-1].push(10)
win._poll_recording()
pump(0.5)
check(
    "failed chunk shows exactly one error dialog",
    len(critical_calls) == 1,
    critical_calls,
)
check(
    "recovers to 'listening' after a mid-recording failure (mic still open)",
    win.voice_overlay.state() == "listening",
)
win._on_voice_button_clicked()  # stop to clean up
pump(0.3)

# --- Clicking while busy gives visible feedback instead of doing nothing ---
print("\n--- busy-state clicks give feedback instead of being silently ignored ---")
win.voice_overlay.set_state("loading")
win.statusBar().clearMessage()
win._on_voice_button_clicked()
check(
    "clicking during 'loading' shows a status message instead of nothing",
    win.statusBar().currentMessage() != "",
    repr(win.statusBar().currentMessage()),
)
check("...and does NOT start a recording", win.voice_recorder is None)

win.voice_overlay.set_state("transcribing")
win.statusBar().clearMessage()
win._on_voice_button_clicked()
check(
    "clicking during 'transcribing' shows a status message instead of nothing",
    win.statusBar().currentMessage() != "",
    repr(win.statusBar().currentMessage()),
)
check("...and does NOT start a recording either", win.voice_recorder is None)
win.voice_overlay.set_state("idle")

# --- Live audio level reaches the button during recording ---
print("\n--- audio level meter is wired up during recording ---")
win._on_voice_button_clicked()  # start
recorder = FakeRecorder.instances[-1]
level_before = win.voice_overlay._audio_level
recorder.push(99)  # FakeSegmenter.feed() reports last_rms=0.05 for any non-empty push
win._poll_recording()
check(
    "voice button's audio level updates from the segmenter's last_rms during polling",
    win.voice_overlay._audio_level > level_before,
    f"before={level_before}, after={win.voice_overlay._audio_level}",
)
win._on_voice_button_clicked()  # stop
pump(0.5)
check("audio level resets once recording stops", win.voice_overlay._audio_level == 0.0)

# --- Proactive "not hearing anything" warning ---
print("\n--- proactive no-speech warning ---")


class SilentFakeSegmenter(FakeSegmenter):
    """Reports never having heard speech, however long it's fed -- models
    a dead mic / wrong input device for this specific check."""

    def feed(self, new_audio):
        return []  # never ready, never counts as speech


fake.UtteranceSegmenter = SilentFakeSegmenter
win._on_voice_button_clicked()  # start
check("no warning yet immediately after starting", win._silence_warning_shown is False)
# Simulate enough elapsed poll ticks without ever hearing speech.
win._recording_poll_ticks = 4000 // main.MainWindow.POLL_INTERVAL_MS
win._poll_recording()
check(
    "silence warning fires once enough time passes with zero speech detected",
    win._silence_warning_shown is True,
)
msg_after_warning = win.statusBar().currentMessage()
win._poll_recording()
check(
    "silence warning does not repeat every tick (shown only once per recording)",
    win.statusBar().currentMessage() == msg_after_warning
    or win.statusBar().currentMessage() == "",
)
win._on_voice_button_clicked()  # stop to clean up
pump(0.3)
fake.UtteranceSegmenter = FakeSegmenter  # restore for anything after this point

print()
if all(results):
    print(f"ALL {len(results)} VOICE CHECKS PASSED")
else:
    print(f"{results.count(False)} of {len(results)} CHECKS FAILED")
    sys.exit(1)
