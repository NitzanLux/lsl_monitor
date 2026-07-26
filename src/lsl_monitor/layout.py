"""Responsive normalized canvas for automatic and freeform panel layouts."""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets


@dataclass(frozen=True)
class RelativeRect:
    """A window-relative rectangle, where every value is in the 0..1 range."""

    x: float
    y: float
    width: float
    height: float

    def bounded(self, minimum_size: float = 0.08) -> RelativeRect:
        width = min(1.0, max(minimum_size, self.width))
        height = min(1.0, max(minimum_size, self.height))
        x = min(max(0.0, self.x), 1.0 - width)
        y = min(max(0.0, self.y), 1.0 - height)
        return RelativeRect(x, y, width, height)

    def as_document(self) -> dict[str, float]:
        return {
            "x": round(self.x, 4),
            "y": round(self.y, 4),
            "width": round(self.width, 4),
            "height": round(self.height, 4),
        }


def automatic_rectangles(count: int, columns: int) -> list[RelativeRect]:
    """Create balanced normalized cells for panels without custom geometry."""

    if count <= 0:
        return []
    columns = min(max(1, columns), count)
    rows = math.ceil(count / columns)
    rectangles = []
    for index in range(count):
        row, column = divmod(index, columns)
        rectangles.append(
            RelativeRect(
                x=column / columns,
                y=row / rows,
                width=1.0 / columns,
                height=1.0 / rows,
            )
        )
    return rectangles


class EditablePanelShell(QtWidgets.QWidget):
    """Designer-only wrapper that supports title dragging and corner resizing."""

    pixel_geometry_changed = QtCore.Signal(object, QtCore.QRect)

    def __init__(self, panel: QtWidgets.QWidget, parent: QtWidgets.QWidget) -> None:
        super().__init__(parent)
        self.panel = panel
        self._operation: str | None = None
        self._press_global = QtCore.QPoint()
        self._press_geometry = QtCore.QRect()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(panel)
        self.grip = QtWidgets.QLabel("◢", self)
        self.grip.setObjectName("panelResizeGrip")
        self.grip.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.grip.setCursor(QtCore.Qt.CursorShape.SizeFDiagCursor)
        self.grip.setToolTip("Drag to resize panel")
        self.grip.installEventFilter(self)
        title_label = getattr(panel, "title_label", None)
        if title_label is not None:
            title_label.setCursor(QtCore.Qt.CursorShape.SizeAllCursor)
            title_label.setToolTip("Drag to move panel")
            title_label.installEventFilter(self)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        size = 22
        self.grip.setGeometry(self.width() - size, self.height() - size, size, size)
        self.grip.raise_()
        super().resizeEvent(event)

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        event_type = event.type()
        if event_type == QtCore.QEvent.Type.MouseButtonPress:
            mouse_event = event
            if mouse_event.button() == QtCore.Qt.MouseButton.LeftButton:
                self._operation = "resize" if watched is self.grip else "move"
                self._press_global = mouse_event.globalPosition().toPoint()
                self._press_geometry = self.geometry()
                self.raise_()
                return True
        elif event_type == QtCore.QEvent.Type.MouseMove and self._operation:
            mouse_event = event
            self._apply_pointer_delta(mouse_event.globalPosition().toPoint())
            return True
        elif event_type == QtCore.QEvent.Type.MouseButtonRelease and self._operation:
            mouse_event = event
            self._apply_pointer_delta(mouse_event.globalPosition().toPoint())
            self._operation = None
            return True
        return super().eventFilter(watched, event)

    def _apply_pointer_delta(self, global_position: QtCore.QPoint) -> None:
        delta = global_position - self._press_global
        bounds = self.parentWidget().rect()
        geometry = QtCore.QRect(self._press_geometry)
        minimum_width = min(180, max(80, bounds.width()))
        minimum_height = min(130, max(70, bounds.height()))
        if self._operation == "move":
            geometry.moveTo(
                min(max(0, geometry.x() + delta.x()), max(0, bounds.width() - geometry.width())),
                min(
                    max(0, geometry.y() + delta.y()),
                    max(0, bounds.height() - geometry.height()),
                ),
            )
        else:
            geometry.setWidth(
                min(
                    max(minimum_width, geometry.width() + delta.x()),
                    max(minimum_width, bounds.width() - geometry.x()),
                )
            )
            geometry.setHeight(
                min(
                    max(minimum_height, geometry.height() + delta.y()),
                    max(minimum_height, bounds.height() - geometry.y()),
                )
            )
        self.setGeometry(geometry)
        self.pixel_geometry_changed.emit(self, geometry)


class PanelCanvas(QtWidgets.QWidget):
    """Places panels using responsive normalized rectangles."""

    geometry_changed = QtCore.Signal(int, object)
    GAP = 4

    def __init__(self, columns: int = 2, editable: bool = False) -> None:
        super().__init__()
        self.columns = columns
        self.editable = editable
        self._panels: list[QtWidgets.QWidget] = []
        self._hosts: list[QtWidgets.QWidget] = []
        self._custom_rectangles: list[RelativeRect | None] = []

    def clear_panels(self) -> None:
        for host in self._hosts:
            host.hide()
            host.deleteLater()
        self._panels.clear()
        self._hosts.clear()
        self._custom_rectangles.clear()

    def add_panel(
        self, panel: QtWidgets.QWidget, rectangle: RelativeRect | None = None
    ) -> None:
        if self.editable:
            host = EditablePanelShell(panel, self)
            host.pixel_geometry_changed.connect(self._host_geometry_changed)
        else:
            panel.setParent(self)
            host = panel
        self._panels.append(panel)
        self._hosts.append(host)
        self._custom_rectangles.append(rectangle.bounded() if rectangle else None)
        host.show()
        self._apply_layout()

    def set_rectangle(self, index: int, rectangle: RelativeRect | None) -> None:
        if not (0 <= index < len(self._hosts)):
            return
        self._custom_rectangles[index] = rectangle.bounded() if rectangle else None
        self._apply_layout()

    def rectangles(self) -> list[RelativeRect]:
        automatic = automatic_rectangles(len(self._hosts), self.columns)
        return [
            custom if custom is not None else automatic[index]
            for index, custom in enumerate(self._custom_rectangles)
        ]

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        self._apply_layout()
        super().resizeEvent(event)

    def _apply_layout(self) -> None:
        if not self._hosts or self.width() <= 0 or self.height() <= 0:
            return
        for host, rectangle in zip(self._hosts, self.rectangles(), strict=True):
            host.setGeometry(self._pixel_rectangle(rectangle))

    def _pixel_rectangle(self, rectangle: RelativeRect) -> QtCore.QRect:
        gap = self.GAP
        left = round(rectangle.x * self.width()) + gap
        top = round(rectangle.y * self.height()) + gap
        right = round((rectangle.x + rectangle.width) * self.width()) - gap
        bottom = round((rectangle.y + rectangle.height) * self.height()) - gap
        return QtCore.QRect(
            left,
            top,
            max(1, right - left),
            max(1, bottom - top),
        )

    @QtCore.Slot(object, QtCore.QRect)
    def _host_geometry_changed(
        self, host: EditablePanelShell, geometry: QtCore.QRect
    ) -> None:
        if self.width() <= 0 or self.height() <= 0:
            return
        try:
            index = self._hosts.index(host)
        except ValueError:
            return
        gap = self.GAP
        outer_left = max(0, geometry.x() - gap)
        outer_top = max(0, geometry.y() - gap)
        outer_right = min(self.width(), geometry.right() + 1 + gap)
        outer_bottom = min(self.height(), geometry.bottom() + 1 + gap)
        rectangle = RelativeRect(
            x=outer_left / self.width(),
            y=outer_top / self.height(),
            width=(outer_right - outer_left) / self.width(),
            height=(outer_bottom - outer_top) / self.height(),
        ).bounded()
        self._custom_rectangles[index] = rectangle
        self.geometry_changed.emit(index, rectangle)
