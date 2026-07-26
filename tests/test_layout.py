import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6 import QtWidgets

from lsl_monitor.layout import PanelCanvas, RelativeRect, automatic_rectangles


def test_automatic_rectangles_balance_panels() -> None:
    rectangles = automatic_rectangles(count=5, columns=2)

    assert len(rectangles) == 5
    assert rectangles[0] == RelativeRect(0.0, 0.0, 0.5, 1 / 3)
    assert rectangles[4] == RelativeRect(0.0, 2 / 3, 0.5, 1 / 3)


def test_canvas_scales_custom_geometry_with_window() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    canvas = PanelCanvas(editable=False)
    panel = QtWidgets.QLabel("panel")
    canvas.add_panel(panel, RelativeRect(0.1, 0.2, 0.5, 0.4))
    canvas.resize(1000, 800)
    canvas.show()
    application.processEvents()

    geometry = panel.geometry()
    assert abs(geometry.x() - 104) <= 1
    assert abs(geometry.y() - 164) <= 1
    assert abs(geometry.width() - 492) <= 1
    assert abs(geometry.height() - 312) <= 1
    canvas.close()
