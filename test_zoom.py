"""
test_zoom.py — headless check that Ctrl+scroll zoom keeps the scene point
under the cursor fixed on screen (see ZoomableGraphicsView._zoom_to in
main.py), for both places a wheel event can arrive from: directly on the
view, and relayed from MainWindow's event filter when it lands on the page
widget instead. Also checks that both of those paths — not just the
event-filter one — stop zooming/scrolling while the standard right-click
menu is open (ZoomableGraphicsView.wheelEvent's own context_menu_open
check). Doesn't need a display or a real mouse — it drives the same
coordinate math a real Ctrl+scroll would, with a synthetic cursor
position, and checks the anchor point doesn't drift.

    python test_zoom.py
"""
import sys
from PyQt6.QtWidgets import QApplication, QGraphicsScene, QLabel, QMainWindow
from PyQt6.QtCore import QPointF, QPoint, Qt, QEvent
from PyQt6.QtGui import QWheelEvent, QMouseEvent

from main import ZoomableGraphicsView

app = QApplication(sys.argv)

scene = QGraphicsScene()
canvas_stub = QLabel()  # stand-in for PageCanvas -- only coordinate math is under test here
canvas_stub.setFixedSize(800, 1000)
proxy = scene.addWidget(canvas_stub)
scene.setSceneRect(0, 0, 800, 1000)

view = ZoomableGraphicsView(scene)
window = QMainWindow()
window.setCentralWidget(view)
window.resize(600, 500)
window.show()
app.processEvents()

results = []


def reset_view():
    view._zoom = 1.0
    view.resetTransform()
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().minimum())
    view.verticalScrollBar().setValue(view.verticalScrollBar().minimum())
    app.processEvents()


def scroll_to_middle():
    hbar, vbar = view.horizontalScrollBar(), view.verticalScrollBar()
    hbar.setValue((hbar.minimum() + hbar.maximum()) // 2)
    vbar.setValue((vbar.minimum() + vbar.maximum()) // 2)
    app.processEvents()


def check(name, cond, detail=""):
    status = "OK" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append(cond)


def anchor_drift_via_canvas_wheel(canvas_local_pos, angle_delta_y, n_steps=1):
    """Simulates n wheel ticks landing on the page widget (the common
    case — relayed via zoom_from_canvas_wheel, same as MainWindow's event
    filter does) and returns how far the point under the cursor drifted."""
    scene_pt = proxy.mapToScene(QPointF(*canvas_local_pos))
    before = view.mapFromScene(scene_pt)
    for _ in range(n_steps):
        view.zoom_from_canvas_wheel(QPointF(*canvas_local_pos), angle_delta_y, proxy)
    after = view.mapFromScene(scene_pt)
    return after.x() - before.x(), after.y() - before.y()


def anchor_drift_via_view_wheel(viewport_pos, angle_delta_y):
    """Same, but for a wheel event landing directly on the view (e.g. the
    backdrop around the page) rather than on the embedded page widget."""
    scene_pt = view.mapToScene(QPoint(*viewport_pos))
    before = view.mapFromScene(scene_pt)
    ev = QWheelEvent(
        QPointF(*viewport_pos), view.viewport().mapToGlobal(QPoint(*viewport_pos)).toPointF(),
        QPoint(0, 0), QPoint(0, angle_delta_y),
        Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
        Qt.ScrollPhase.NoScrollPhase, False,
    )
    view.wheelEvent(ev)
    after = view.mapFromScene(scene_pt)
    return after.x() - before.x(), after.y() - before.y()


# Interior points (comfortably away from any scroll boundary) should be
# pixel-perfect in both directions and via both event paths.
reset_view()
dx, dy = anchor_drift_via_canvas_wheel((50, 50), 1)
check("zoom in @ page-widget wheel, top-left area", dx == 0 and dy == 0, f"drift=({dx},{dy})")

reset_view()
scroll_to_middle()
dx, dy = anchor_drift_via_canvas_wheel((300, 200), -1)
check("zoom out @ page-widget wheel, scrolled to middle", dx == 0 and dy == 0, f"drift=({dx},{dy})")

reset_view()
dx, dy = anchor_drift_via_canvas_wheel((400, 400), 1, n_steps=5)
check("5x zoom in @ page-widget wheel, same spot", dx == 0 and dy == 0, f"drift=({dx},{dy})")

reset_view()
dx, dy = anchor_drift_via_view_wheel((100, 100), 120)
check("zoom in @ direct view wheel", dx == 0 and dy == 0, f"drift=({dx},{dy})")

reset_view()
scroll_to_middle()
dx, dy = anchor_drift_via_view_wheel((300, 200), -120)
check("zoom out @ direct view wheel, scrolled to middle", dx == 0 and dy == 0, f"drift=({dx},{dy})")

# Button/shortcut zoom (no cursor position) should keep the view CENTER
# fixed instead -- check the center point specifically stays put.
reset_view()
center = view.viewport().rect().center()
scene_center_before = view.mapToScene(center)
view.set_zoom(2.0)
scene_center_after = view.mapToScene(center)
check("toolbar/shortcut zoom keeps the view center anchored",
      abs(scene_center_before.x() - scene_center_after.x()) < 1
      and abs(scene_center_before.y() - scene_center_after.y()) < 1)

print()
print("Note: zooming out while already scrolled to an extreme edge of the")
print("page can't always perfectly preserve an off-center anchor -- there's")
print("no content beyond the edge to scroll into to compensate. Every app")
print("with cursor-zoom (Figma, Photoshop, browsers) clamps the same way")
print("at that boundary; the cases above are all realistic mid-document")
print("positions, which is what matters for actual use.")
print()

# ----------------------------------------------------------------------
# Real MainWindow, real default page (794x1123) and window (1100x850)
# size -- not a synthetic scene/view pair. This specific combination is
# what earlier testing here missed: the default page is *narrower* than
# the default window, so QGraphicsView's AlignCenter was forcing the
# view back to horizontal dead-center on every zoom, regardless of any
# scrollbar correction -- there's no scrollable range to correct within
# once content fits inside the viewport. See MainWindow.PASTEBOARD_MARGIN
# in main.py for the fix. A synthetic view where the stand-in content is
# always bigger than the viewport (as above) can't exercise this at all,
# which is exactly how it slipped through the first time.
# ----------------------------------------------------------------------
print("--- real MainWindow, real default page/window size ---")
import time
import types

# This test only cares about zoom/layout behavior, so give it a minimal
# fake voice_to_urdu rather than letting MainWindow's startup model-preload
# touch the real thing — with faster-whisper actually installed but no
# local model and no real network access, that preload could sit waiting
# on a network call for a long time, which has nothing to do with what
# this file is testing and would just make it flaky depending on what
# happens to be installed and reachable wherever it's run.
_fake_voice = types.ModuleType("voice_to_urdu")
_fake_voice.MicrophoneUnavailable = RuntimeError
_fake_voice.MODEL_ID = "fake-model"
_fake_voice.get_model = lambda model_id=None: object()
_fake_voice.local_model_available = lambda: True
sys.modules["voice_to_urdu"] = _fake_voice

import tempfile
from PyQt6.QtCore import QSettings
_settings_tmp_dir = tempfile.mkdtemp()
QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, _settings_tmp_dir)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _settings_tmp_dir)

import main as main_module

win = main_module.MainWindow()
win.show()
deadline = time.time() + 0.5
while time.time() < deadline:
    app.processEvents()
    time.sleep(0.01)

real_view = win.view
real_proxy = win.canvas_proxy

# Check startup positioning FIRST, before any zoom operations below
# legitimately move the view elsewhere.
top_left = real_view.mapFromScene(0, 0)
check("startup shows the page's top edge, not blank pasteboard above it",
      abs(top_left.y()) <= 1, f"page top is at viewport y={top_left.y()}")

check("default page (794px) is narrower than default window's viewport — "
      "the exact condition that exposed the bug",
      794 < real_view.viewport().width(), f"viewport width={real_view.viewport().width()}")

# The voice button's default position has the exact same construction-
# timing issue as the scene rect above (computed once, immediately after
# construction, before the window's real size exists) and needs the same
# deferred re-run fix — checked here since it's the same root cause.
overlay = win.voice_overlay
expected_x = win.view.width() - overlay.width() - 20
expected_y = win.view.height() - overlay.height() - 20
check("voice button lands at the real bottom-right corner on startup, "
      "not the pre-layout placeholder size's bottom-right corner",
      overlay.x() == expected_x and overlay.y() == expected_y,
      f"got ({overlay.x()},{overlay.y()}), expected ({expected_x},{expected_y})")


def real_app_drift(canvas_local_pos, angle_delta_y, n_steps=1):
    scene_pt = real_proxy.mapToScene(QPointF(*canvas_local_pos))
    before = real_view.mapFromScene(scene_pt)
    for _ in range(n_steps):
        real_view.zoom_from_canvas_wheel(QPointF(*canvas_local_pos), angle_delta_y, real_proxy)
    after = real_view.mapFromScene(scene_pt)
    return after.x() - before.x(), after.y() - before.y()


dx, dy = real_app_drift((50, 500), 1)
check("real app: zoom in near LEFT edge of the (narrower-than-window) page",
      dx == 0 and dy == 0, f"drift=({dx},{dy})")

dx, dy = real_app_drift((750, 500), 1)
check("real app: zoom in near RIGHT edge of the page", dx == 0 and dy == 0, f"drift=({dx},{dy})")

dx, dy = real_app_drift((200, 300), 1, n_steps=5)
check("real app: 5x zoom in at an off-center point", dx == 0 and dy == 0, f"drift=({dx},{dy})")

dx, dy = real_app_drift((600, 800), -1)
check("real app: zoom out after zooming in", dx == 0 and dy == 0, f"drift=({dx},{dy})")

# Zoom tool: a click on the page should zoom instead of placing the text
# cursor while the tool is active, and go back to normal the moment it's
# switched off — see MainWindow.eventFilter's MouseButtonPress handling
# and _toggle_zoom_tool.
def press(button, modifiers=Qt.KeyboardModifier.NoModifier):
    pt = QPointF(50, 50)
    event = QMouseEvent(QEvent.Type.MouseButtonPress, pt, pt, button, button, modifiers)
    return win.eventFilter(win.canvas, event)


check("zoom tool starts off", win.zoom_tool_act.isChecked() is False)

win.zoom_tool_act.setChecked(True)
check("enabling the zoom tool changes the page's cursor",
      win.canvas.viewport().cursor().shape() == Qt.CursorShape.CrossCursor)

before = win.view.zoom()
before_text_pos = win.canvas.textCursor().position()
consumed = press(Qt.MouseButton.LeftButton)
check("left click while zoom tool is on is consumed (not left for QTextEdit)", consumed is True)
check("left click while zoom tool is on zooms IN", win.view.zoom() > before,
      f"{before} -> {win.view.zoom()}")
check("left click while zoom tool is on does not move the text cursor",
      win.canvas.textCursor().position() == before_text_pos)

before = win.view.zoom()
press(Qt.MouseButton.RightButton)
check("right click while zoom tool is on zooms OUT", win.view.zoom() < before,
      f"{before} -> {win.view.zoom()}")

before = win.view.zoom()
press(Qt.MouseButton.LeftButton, Qt.KeyboardModifier.AltModifier)
check("alt+left click while zoom tool is on also zooms OUT", win.view.zoom() < before,
      f"{before} -> {win.view.zoom()}")

page_width_before = win.canvas.page_width
check("the page's actual size is untouched by any of the above zoom-tool clicks",
      win.canvas.page_width == page_width_before)

win.zoom_tool_act.setChecked(False)
check("disabling the zoom tool restores the normal page cursor",
      win.canvas.viewport().cursor().shape() != Qt.CursorShape.CrossCursor)

consumed = press(Qt.MouseButton.LeftButton)
check("with the zoom tool off, a left click is left alone (normal typing/cursor placement)",
      consumed is False)

# Zoom must not move the page while the standard right-click menu is
# open — PageCanvas.contextMenuEvent sets context_menu_open for exactly
# the span that menu is up, and MainWindow's event filter checks it
# before zooming from either the Ctrl+scroll or the Zoom Tool path (see
# canvas.py's contextMenuEvent docstring for why: the menu is a separate
# top-level popup, so zooming the page under it just visibly drifts the
# two apart rather than changing anything useful).
win.canvas.context_menu_open = True

before = win.view.zoom()
consumed = win.eventFilter(win.canvas, QWheelEvent(
    QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
    Qt.ScrollPhase.NoScrollPhase, False,
))
check("Ctrl+scroll is consumed but does not zoom while the right-click menu is open",
      consumed is True and win.view.zoom() == before,
      f"consumed={consumed} zoom {before} -> {win.view.zoom()}")

win.zoom_tool_act.setChecked(True)
before = win.view.zoom()
press(Qt.MouseButton.LeftButton)
check("zoom-tool click does not zoom while the right-click menu is open",
      win.view.zoom() == before, f"{before} -> {win.view.zoom()}")
win.zoom_tool_act.setChecked(False)

# Regression test: the check above goes through MainWindow.eventFilter,
# which only ever sees wheel events that land on self.canvas (the
# embedded page widget) -- installEventFilter was only ever called with
# that as the target. A wheel event landing directly on the view itself
# (e.g. the pasteboard backdrop around the page, which is most of the
# window at a high zoom level) skips eventFilter entirely and goes
# straight to ZoomableGraphicsView.wheelEvent, which used to have no
# context_menu_open check of its own at all -- so scrolling the backdrop
# while the right-click menu was open still zoomed/panned the page out
# from under it. See that method's docstring.
check("real app: view.canvas is wired up (so wheelEvent above can check it)",
      win.view.canvas is win.canvas)
before = win.view.zoom()
win.view.wheelEvent(QWheelEvent(
    QPointF(500, 500), win.view.viewport().mapToGlobal(QPoint(500, 500)).toPointF(),
    QPoint(0, 0), QPoint(0, 120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
    Qt.ScrollPhase.NoScrollPhase, False,
))
check("Ctrl+scroll landing DIRECTLY ON THE VIEW (not the page) does not "
      "zoom while the right-click menu is open",
      win.view.zoom() == before, f"{before} -> {win.view.zoom()}")
before = win.view.zoom()
win.view.wheelEvent(QWheelEvent(
    QPointF(500, 500), win.view.viewport().mapToGlobal(QPoint(500, 500)).toPointF(),
    QPoint(0, 0), QPoint(0, 120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    Qt.ScrollPhase.NoScrollPhase, False,
))
check("a plain (non-Ctrl) scroll directly on the view is also blocked while "
      "the menu is open, not just Ctrl+scroll zoom",
      win.view.zoom() == before, f"{before} -> {win.view.zoom()}")

win.canvas.context_menu_open = False
before = win.view.zoom()
win.view.wheelEvent(QWheelEvent(
    QPointF(500, 500), win.view.viewport().mapToGlobal(QPoint(500, 500)).toPointF(),
    QPoint(0, 0), QPoint(0, 120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
    Qt.ScrollPhase.NoScrollPhase, False,
))
check("...and scrolling directly on the view zooms normally again once the "
      "menu closes", win.view.zoom() > before, f"{before} -> {win.view.zoom()}")
win.canvas.context_menu_open = True  # restore for the eventFilter checks below

win.canvas.context_menu_open = False
before = win.view.zoom()
win.eventFilter(win.canvas, QWheelEvent(
    QPointF(50, 50), QPointF(50, 50), QPoint(0, 0), QPoint(0, 120),
    Qt.MouseButton.NoButton, Qt.KeyboardModifier.ControlModifier,
    Qt.ScrollPhase.NoScrollPhase, False,
))
check("Ctrl+scroll zooms normally again once the menu closes",
      win.view.zoom() != before, f"{before} -> {win.view.zoom()}")

# Close the window explicitly rather than just letting the script end —
# MainWindow.closeEvent() terminates the background model-load thread it
# started; skipping this risks a "QThread: Destroyed while thread is
# still running" abort at process exit if that thread is still working
# (e.g. actually trying to reach the network for a model, once
# faster-whisper is installed) when Python tears down.
win.close()
for _ in range(20):
    app.processEvents()
    time.sleep(0.02)

print()

print()
if all(results):
    print(f"ALL {len(results)} ZOOM CHECKS PASSED")
else:
    print(f"{results.count(False)} of {len(results)} CHECKS FAILED")
    sys.exit(1)
