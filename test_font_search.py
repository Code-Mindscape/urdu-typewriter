"""
test_font_search.py — headless checks for the searchable font dropdown:
click opens a popup with a search box; typing narrows the list; keyboard
and mouse both pick a font; Esc/no-match leave the current font alone.

    python test_font_search.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).parent))

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from dialogs import SearchableFontCombo

app = QApplication(sys.argv)
results = []


def check(label, ok, detail=""):
    print(
        f"[{'OK' if ok else 'FAIL'}] {label}"
        + (f" — {detail}" if detail and not ok else "")
    )
    results.append(ok)


FAMILIES = [
    "Alpha Sans",
    "Noto Nastaliq Urdu",
    "Noto Sans",
    "Jameel Noori Nastaleeq",
    "Beta Serif",
]

combo = SearchableFontCombo()
combo.addItems(FAMILIES)
combo.setCurrentIndex(0)
combo.show()
activated = []
combo.activated.connect(activated.append)


def open_popup():
    combo.showPopup()
    return combo._popup


def visible_names(popup):
    return [popup.list.item(i).text() for i in popup._visible_rows()]


# ---- opening ---------------------------------------------------------------
popup = open_popup()
check("clicking the combo opens the search popup", popup.isVisible())
check(
    "popup has a search box, ready for typing",
    popup.search.placeholderText() == "Search fonts…",
)
check("popup lists every font", visible_names(popup) == FAMILIES)
check(
    "current font is highlighted on open",
    popup.list.currentItem().text() == "Alpha Sans",
)
check("status shows the total", popup.status.text() == "5 fonts", popup.status.text())

# ---- typing filters --------------------------------------------------------
QTest.keyClicks(popup.search, "noto")
check(
    "typing narrows the list",
    visible_names(popup) == ["Noto Nastaliq Urdu", "Noto Sans"],
    str(visible_names(popup)),
)
check(
    "status shows matches of total",
    popup.status.text() == "2 of 5 fonts",
    popup.status.text(),
)
check(
    "top match is highlighted", popup.list.currentItem().text() == "Noto Nastaliq Urdu"
)

QTest.keyClicks(popup.search, " URDU")
check(
    "search is case-insensitive and matches words in any position",
    visible_names(popup) == ["Noto Nastaliq Urdu"],
    str(visible_names(popup)),
)

# ---- Enter picks -----------------------------------------------------------
QTest.keyClick(popup.search, Qt.Key.Key_Return)
check(
    "Enter picks the highlighted font",
    combo.currentText() == "Noto Nastaliq Urdu",
    combo.currentText(),
)
check("popup closes after picking", not popup.isVisible())
check("'activated' fired once for the pick", activated == [1], str(activated))

# ---- words out of order ----------------------------------------------------
popup = open_popup()
QTest.keyClicks(popup.search, "urdu noto")
check(
    "words can be typed in any order",
    visible_names(popup) == ["Noto Nastaliq Urdu"],
    str(visible_names(popup)),
)
QTest.keyClick(popup.search, Qt.Key.Key_Escape)
check(
    "Esc closes without changing the font",
    (not popup.isVisible()) and combo.currentText() == "Noto Nastaliq Urdu",
)

# ---- no match --------------------------------------------------------------
popup = open_popup()
QTest.keyClicks(popup.search, "zzzz")
check("no matches: list is empty", visible_names(popup) == [])
check(
    "no matches: status says so",
    popup.status.text() == "No fonts match your search",
    popup.status.text(),
)
before = combo.currentText()
QTest.keyClick(popup.search, Qt.Key.Key_Return)
check("Enter with no matches changes nothing", combo.currentText() == before)
popup.close()

# ---- arrow keys ------------------------------------------------------------
combo.setCurrentIndex(0)
popup = open_popup()
QTest.keyClick(popup.search, Qt.Key.Key_Down)
check(
    "Down moves to the next font",
    popup.list.currentItem().text() == "Noto Nastaliq Urdu",
    popup.list.currentItem().text(),
)
QTest.keyClick(popup.search, Qt.Key.Key_Down)
QTest.keyClick(popup.search, Qt.Key.Key_Up)
check("Up moves back", popup.list.currentItem().text() == "Noto Nastaliq Urdu")
QTest.keyClick(popup.search, Qt.Key.Key_Up)
QTest.keyClick(popup.search, Qt.Key.Key_Up)
check("Up stops at the first font", popup.list.currentItem().text() == "Alpha Sans")
QTest.keyClick(popup.search, Qt.Key.Key_PageDown)
check(
    "PageDown stops at the last font", popup.list.currentItem().text() == "Beta Serif"
)
popup.close()

# ---- arrows skip filtered-out rows ----------------------------------------
popup = open_popup()
QTest.keyClicks(popup.search, "sans")
check(
    "filter to 'sans'",
    visible_names(popup) == ["Alpha Sans", "Noto Sans"],
    str(visible_names(popup)),
)
QTest.keyClick(popup.search, Qt.Key.Key_Down)
check(
    "Down skips hidden fonts",
    popup.list.currentItem().text() == "Noto Sans",
    popup.list.currentItem().text(),
)
popup.close()

# ---- clearing the search restores the full list ---------------------------
combo.setCurrentIndex(3)
popup = open_popup()
QTest.keyClicks(popup.search, "alpha")
popup.search.clear()
check("clearing the search shows everything again", visible_names(popup) == FAMILIES)
check(
    "clearing the search re-highlights the current font",
    popup.list.currentItem().text() == "Jameel Noori Nastaleeq",
    popup.list.currentItem().text(),
)
popup.close()

# ---- mouse click picks -----------------------------------------------------
activated.clear()
popup = open_popup()
item = popup.list.item(4)  # Beta Serif
QTest.mouseClick(
    popup.list.viewport(),
    Qt.MouseButton.LeftButton,
    pos=popup.list.visualItemRect(item).center(),
)
check(
    "clicking a font picks it", combo.currentText() == "Beta Serif", combo.currentText()
)
check("popup closes after a click", not popup.isVisible())

# ---- an empty combo doesn't open anything ----------------------------------
empty = SearchableFontCombo()
empty.showPopup()
check("empty combo opens no popup", not hasattr(empty, "_popup"))

print("\nALL FONT SEARCH CHECKS PASSED" if all(results) else "\nSOME CHECKS FAILED")
sys.exit(0 if all(results) else 1)
