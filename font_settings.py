"""
font_settings.py — a persisted allowlist of which system fonts show up in
the font picker.

By default every font on the system that declares Arabic-script support is
enabled (a reasonable starting point for an Urdu-only tool), and everything
else is left unchecked but still visible/editable in the Font Manager
dialog — the user has full manual control from there, including fonts our
Arabic-support detection might have missed or gotten wrong.

Persisted via QSettings, so the choice survives restarts and is edited the
same way every time: Fonts... in the toolbar.
"""

from PyQt6.QtCore import QSettings
from PyQt6.QtGui import QFontDatabase

ORG = "UrduTypewriter"
APP = "UrduTypewriter"
KEY_ENABLED = "fonts/enabled_families"
KEY_INITIALIZED = "fonts/initialized"


def all_families():
    return list(QFontDatabase.families())


def arabic_capable_families():
    out = []
    for fam in QFontDatabase.families():
        try:
            systems = QFontDatabase.writingSystems(fam)
        except Exception:
            continue
        if QFontDatabase.WritingSystem.Arabic in systems:
            out.append(fam)
    return out


def get_enabled_families(default_family_to_guarantee=None):
    """Returns the persisted set of enabled family names. On first run
    (nothing saved yet), seeds it with every Arabic-capable font found."""
    settings = QSettings(ORG, APP)
    if not settings.value(KEY_INITIALIZED, False, type=bool):
        seed = set(arabic_capable_families())
        if default_family_to_guarantee:
            seed.add(default_family_to_guarantee)
        set_enabled_families(seed)
        return seed
    saved = settings.value(KEY_ENABLED, [], type=list)
    enabled = set(saved)
    # Guard against an empty/corrupted save leaving the picker with nothing.
    if not enabled:
        enabled = set(arabic_capable_families())
        if default_family_to_guarantee:
            enabled.add(default_family_to_guarantee)
    return enabled


def set_enabled_families(family_set):
    settings = QSettings(ORG, APP)
    settings.setValue(KEY_ENABLED, sorted(family_set))
    settings.setValue(KEY_INITIALIZED, True)
    settings.sync()
