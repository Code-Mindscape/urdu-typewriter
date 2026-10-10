"""
test_prefs_startup.py — headless check that saved Preferences are actually
applied when the app starts (they used to be written to disk but never
read back), and that bad saved values fall back safely.

Runs against a throwaway settings file in a temp folder, so it never reads
or modifies your real preferences on any platform.

    python test_prefs_startup.py
"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtCore import QSettings, QStandardPaths
from PyQt6.QtWidgets import QApplication

# Isolate settings so this can never read or modify your real preferences.
# The app builds every QSettings as QSettings(ORG, APP), which is the
# platform-native store (the registry on Windows) — and setDefaultFormat()
# and setPath() don't redirect that constructor. So swap in a factory, in
# the only two modules that create QSettings, that returns an INI file in a
# temp folder instead.
_tmp = tempfile.mkdtemp(prefix="utw_prefs_test_")
_Real = QSettings
_Real.setPath(_Real.Format.IniFormat, _Real.Scope.UserScope, _tmp)


def _isolated_settings(org, app=None, *_ignored):
    return _Real(_Real.Format.IniFormat, _Real.Scope.UserScope, org, app)


import font_settings

font_settings.QSettings = _isolated_settings

app = QApplication(sys.argv)
# Isolates QStandardPaths.AppDataLocation too -- not just QSettings above --
# so MainWindow's autosave-recovery check can never find a real recovery
# file and block this test on a dialog with nothing to click.
QStandardPaths.setTestModeEnabled(True)
import main

main.QSettings = _isolated_settings

results = []


def check(label, ok, detail=""):
    print(f"[{'OK' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))
    results.append(ok)


def settings():
    return _isolated_settings(font_settings.ORG, font_settings.APP)


assert Path(settings().fileName()).is_relative_to(_tmp), "settings are not isolated — refusing to run"

# 1. Nothing saved: the original built-in start.
st = settings(); st.clear(); st.sync()
w = main.MainWindow()
check("fresh install: page is 794x1123",
      (w.canvas.page_width, w.canvas.page_height) == (794, 1123),
      f"{w.canvas.page_width}x{w.canvas.page_height}")
check("fresh install: 36pt", w.canvas.pending_size() == 36, str(w.canvas.pending_size()))
check("fresh install: autosave every 60s", w._autosave_timer.interval() == 60000,
      str(w._autosave_timer.interval()))
bundled = w.canvas.pending_family()
w.close()

# 2. Saved prefs are applied on the next launch.
st = settings()
st.setValue("prefs/defaultWidth", 900)
st.setValue("prefs/defaultHeight", 600)
st.setValue("prefs/defaultSize", 48)
st.setValue("prefs/autosaveIntervalSec", 30)
st.setValue("prefs/defaultFamily", bundled)
st.sync()
w = main.MainWindow()
check("saved prefs: page is 900x600",
      (w.canvas.page_width, w.canvas.page_height) == (900, 600),
      f"{w.canvas.page_width}x{w.canvas.page_height}")
check("saved prefs: 48pt", w.canvas.pending_size() == 48, str(w.canvas.pending_size()))
check("saved prefs: size box shows 48", w.size_spin.value() == 48, str(w.size_spin.value()))
check("saved prefs: autosave every 30s", w._autosave_timer.interval() == 30000,
      str(w._autosave_timer.interval()))
check("saved prefs: family kept", w.canvas.pending_family() == bundled, w.canvas.pending_family())
w.close()

# 3. A saved family that no longer exists falls back safely.
st = settings(); st.setValue("prefs/defaultFamily", "Definitely Not A Real Font"); st.sync()
w = main.MainWindow()
check("missing saved font falls back to the bundled one",
      w.canvas.pending_family() == bundled, w.canvas.pending_family())
check("font combo matches the canvas", w.font_combo.currentText() == w.canvas.pending_family(),
      f"{w.font_combo.currentText()!r} vs {w.canvas.pending_family()!r}")
w.close()

# 4. A corrupt/zero autosave interval can't create a tight loop.
st = settings(); st.setValue("prefs/autosaveIntervalSec", 0); st.sync()
w = main.MainWindow()
check("zero autosave interval floored to 5s", w._autosave_timer.interval() == 5000,
      str(w._autosave_timer.interval()))
w.close()

print("\nALL PREFS STARTUP CHECKS PASSED" if all(results) else "\nSOME CHECKS FAILED")
sys.exit(0 if all(results) else 1)
