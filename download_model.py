"""
download_model.py — one-time download of the Urdu speech model into a
plain local folder (./whisper_model), instead of relying on faster-whisper's
default lazy-download-into-the-HuggingFace-cache behavior.

Why this exists: the default behavior uses huggingface_hub's cache system,
which relies on symlinks — unsupported without Developer Mode or admin
rights on Windows, producing a scary-looking (but non-fatal) warning on
every startup. Downloading explicitly into a plain folder sidesteps that
entirely, and makes the model an explicit, visible, permanent part of the
project instead of something buried in a cache directory.

Run this once:
    python download_model.py

voice_to_urdu.py automatically prefers ./whisper_model if it exists, and
only falls back to the HF-cache-based lazy download if it doesn't — so
running this script is optional, but recommended, and turns "downloads on
first use, every fresh machine" into "downloaded once, permanently."
"""

import shutil
import sys
import time
from pathlib import Path

from voice_to_urdu import MODEL_ID, MODEL_DIR, local_model_available  # single source of

# truth for which model this app uses —
# see voice_to_urdu.py to change it
MAX_RETRIES = 6


def download_model(model_id=MODEL_ID, output_dir=MODEL_DIR, max_retries=MAX_RETRIES):
    try:
        from huggingface_hub import snapshot_download
    except ImportError:
        print("faster-whisper isn't installed yet. Run:")
        print("    pip install -r requirements-voice.txt")
        return False

    output_dir = Path(output_dir)
    if local_model_available():
        print(
            f"Already downloaded at {output_dir} — delete that folder first if you want to re-download."
        )
        return True
    if output_dir.exists() and any(output_dir.iterdir()):
        print(
            f"{output_dir} exists but looks incomplete (an interrupted earlier download?) "
            f"— removing it and starting over."
        )
        shutil.rmtree(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {model_id} into {output_dir} ...")
    print(
        "This is a one-time download (roughly 1.4GB for the default model — see MODEL_ID "
        "in voice_to_urdu.py for smaller/larger alternatives). Needs internet; the app "
        "works fully offline afterwards."
    )

    for attempt in range(1, max_retries + 1):
        try:
            snapshot_download(
                repo_id=model_id,
                local_dir=str(output_dir),
                # Plain-file download, not the symlink-based cache layout —
                # this is the whole point of downloading explicitly instead
                # of letting faster-whisper do it lazily on first use.
            )
            # Deliberately NOT re-checking local_model_available() here.
            # Newer huggingface_hub versions download large files (Xet)
            # in two phases -- transfer, then a separate reconstruction
            # step that assembles the final file on disk -- and
            # snapshot_download() can return before reconstruction has
            # fully caught up. Checking file sizes immediately afterward
            # raced against that and produced a real false positive: a
            # completely good ~1.5GB download got declared "incomplete"
            # and thrown away for a costly, unnecessary full re-download.
            # snapshot_download() raising (below) is a far more reliable
            # failure signal than a size check with no visibility into
            # whether a background step is still finishing. A stale,
            # already-broken folder sitting on disk from an earlier run
            # is still caught -- that's local_model_available() used
            # elsewhere (see the top of this function, and get_model() in
            # voice_to_urdu.py), checking files at rest with nothing left
            # to race against.
            print(f"Done. Model is ready at {output_dir}")
            return True
        except Exception as e:
            print(f"Attempt {attempt}/{max_retries} failed: {e}")
            if attempt < max_retries:
                wait = min(2**attempt, 60)
                print(f"Retrying in {wait}s...")
                time.sleep(wait)
                # No cleanup between retries -- snapshot_download() is
                # built to resume an interrupted local_dir download on
                # its own, and clearing the folder out from under it on
                # every attempt is exactly the kind of extra interference
                # between here and the actual download call that isn't
                # worth the risk. Whatever's left behind if every retry
                # is ultimately exhausted still gets cleaned up below.
            else:
                print()
                print(
                    "All retries failed. If this keeps happening, you can download the files"
                )
                print(f"by hand from https://huggingface.co/{model_id}/tree/main")
                print(f"and place them in: {output_dir}")
                shutil.rmtree(output_dir, ignore_errors=True)
                return False
    return False


if __name__ == "__main__":
    ok = download_model()
    sys.exit(0 if ok else 1)
