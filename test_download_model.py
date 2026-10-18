"""
test_download_model.py — headless check of download_model.py's retry logic,
using a faked huggingface_hub so it needs no network access and no real
~1.4GB download.

Two things this locks in, both from real bugs:
  - A pre-existing local folder with a truncated file (e.g. model.bin cut
    short by an earlier interrupted download) is replaced by a fresh
    download instead of being accepted as "Already downloaded" just
    because every required filename happens to exist. See
    voice_to_urdu.py's local_model_available() docstring.
  - A snapshot_download() call that raises nothing is trusted as done,
    even if an immediate size check of the result would say otherwise.
    An earlier version of this file re-checked completeness right after
    every download and retried on a "no" -- newer huggingface_hub
    versions reconstruct large (Xet-backed) files as a distinct step that
    can still be finishing when snapshot_download() returns, so that
    immediate re-check produced a real false positive: a completely good
    ~1.5GB download got declared "incomplete" and thrown away for an
    unnecessary full re-download. snapshot_download() raising is what
    retries are driven by now -- it has real visibility into its own
    transfer that an outside size check, run at an arbitrary moment,
    doesn't.

    python test_download_model.py
"""

import shutil
import sys
import tempfile
import types
from pathlib import Path

import voice_to_urdu as vu

REQUIRED_FILES = vu._REQUIRED_MODEL_FILES
MIN_FILE_BYTES = vu._MIN_FILE_BYTES

import download_model as dm

dm.time.sleep = lambda seconds: None  # skip real exponential-backoff delays in tests

results = []


def check(name, cond, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append(cond)


def write_fake_model_file(path, size):
    with open(path, "wb") as f:
        f.truncate(size)  # sparse file of the given size, no need to actually write it


def write_complete_model(output_dir):
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, min_bytes in MIN_FILE_BYTES.items():
        write_fake_model_file(output_dir / name, min_bytes + 100)


class FakeSnapshotDownload:
    """Each call pops the next scripted behavior off a queue:
    "ok" writes a real-looking complete model and raises nothing.
    "incomplete" ALSO raises nothing, but leaves model.bin as a tiny stub
        -- standing in for a real download that's still mid-reconstruction
        (see the module docstring). This is deliberately trusted, not
        retried -- there's no exception here for download_model() to act on.
    "raise" raises, like a dropped connection mid-transfer would.
    """

    def __init__(self, behaviors):
        self.behaviors = list(behaviors)
        self.calls = 0

    def __call__(self, repo_id, local_dir, **kwargs):
        self.calls += 1
        behavior = self.behaviors.pop(0)
        if behavior == "raise":
            raise ConnectionError("simulated dropped connection")
        write_complete_model(local_dir)
        if behavior == "incomplete":
            write_fake_model_file(Path(local_dir) / "model.bin", 200)


def install_fake_snapshot_download(behaviors):
    fake_hub = types.ModuleType("huggingface_hub")
    fake = FakeSnapshotDownload(behaviors)
    fake_hub.snapshot_download = fake
    sys.modules["huggingface_hub"] = fake_hub
    return fake


_had_hub = "huggingface_hub" in sys.modules
_orig_hub = sys.modules.get("huggingface_hub")
_orig_model_dir = vu.MODEL_DIR


def restore_hub():
    if _had_hub:
        sys.modules["huggingface_hub"] = _orig_hub
    else:
        sys.modules.pop("huggingface_hub", None)


def run_download(d, model_id="fake/model", max_retries=3):
    # local_model_available() (used throughout download_model()) checks
    # voice_to_urdu.MODEL_DIR directly, not download_model()'s output_dir
    # parameter -- keep them pointed at the same place, exactly like the
    # real app does by always calling download_model() with its defaults.
    vu.MODEL_DIR = Path(d)
    return dm.download_model(model_id=model_id, output_dir=d, max_retries=max_retries)


tmp_root = tempfile.mkdtemp()

try:
    # --- 1. a genuinely complete existing folder: no download attempted ---
    d = Path(tmp_root) / "already-complete"
    write_complete_model(d)
    fake = install_fake_snapshot_download(
        []
    )  # popping an empty list would raise IndexError
    ok = run_download(d)
    check(
        "an already-complete local folder is accepted without calling snapshot_download",
        ok is True and fake.calls == 0,
    )

    # --- 2. first attempt succeeds cleanly ---
    d = Path(tmp_root) / "first-try-ok"
    fake = install_fake_snapshot_download(["ok"])
    ok = run_download(d)
    check(
        "a clean successful download is accepted on the first attempt",
        ok is True and fake.calls == 1 and vu.local_model_available(),
    )

    # --- 3. snapshot_download raises twice, then succeeds ---
    d = Path(tmp_root) / "raises-then-ok"
    fake = install_fake_snapshot_download(["raise", "raise", "ok"])
    ok = run_download(d, max_retries=5)
    check(
        "retries past repeated raised errors and succeeds once one attempt works",
        ok is True and fake.calls == 3,
    )

    # --- 4. snapshot_download() returning cleanly is trusted even if the result
    # would fail our own completeness check -- regression test for a real bug:
    # an earlier version of this function re-checked local_model_available()
    # immediately after every download and treated a "no" as a failure to
    # retry. Newer huggingface_hub versions reconstruct large (Xet-backed)
    # files as a distinct step that can still be finishing when
    # snapshot_download() returns, so that immediate re-check produced a
    # real false positive: a completely good ~1.5GB download got declared
    # "incomplete" and thrown away for an unnecessary full re-download.
    # snapshot_download() itself raising is what retries should be driven
    # by -- it has real visibility into its own transfer; a size check
    # from the outside, run at an arbitrary moment, doesn't.
    d = Path(tmp_root) / "reports-success-trusted-even-if-looks-incomplete"
    fake = install_fake_snapshot_download(["incomplete"])
    ok = run_download(d, max_retries=5)
    check(
        "a snapshot_download() call that raises nothing is trusted as done in one "
        "attempt, even if the result wouldn't pass local_model_available() on its own",
        ok is True and fake.calls == 1,
    )

    # --- 5. every attempt fails: give up cleanly after max_retries, no broken folder left ---
    d = Path(tmp_root) / "always-fails"
    fake = install_fake_snapshot_download(["raise", "raise", "raise"])
    ok = run_download(d, max_retries=3)
    check(
        "gives up after max_retries and reports failure",
        ok is False and fake.calls == 3,
    )
    check("doesn't leave a broken folder behind after giving up", not d.exists())

    # --- 6. a pre-existing truncated folder (today's actual bug) gets replaced
    # rather than trusted as-is ---
    d = Path(tmp_root) / "pre-existing-truncated"
    write_complete_model(d)
    write_fake_model_file(d / "model.bin", 200)  # corrupt just the one file
    fake = install_fake_snapshot_download(["ok"])
    ok = run_download(d)
    check(
        "a pre-existing folder with a truncated file is re-downloaded rather than "
        "accepted as already complete",
        ok is True and fake.calls == 1,
    )
finally:
    restore_hub()
    vu.MODEL_DIR = _orig_model_dir
    shutil.rmtree(tmp_root, ignore_errors=True)

print()
if all(results):
    print(f"ALL {len(results)} DOWNLOAD_MODEL CHECKS PASSED")
else:
    print(f"{results.count(False)} of {len(results)} CHECKS FAILED")
    sys.exit(1)
