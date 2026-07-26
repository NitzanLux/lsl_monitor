"""PyQtGraph widgets for traces, phase planes, PSD, and connection health."""

from __future__ import annotations

from abc import abstractmethod

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from lsl_monitor.config import ConfigError, ViewConfig
from lsl_monitor.model import StreamSnapshot

BACKGROUND = "#101720"
FOREGROUND = "#d9e2ec"
MUTED = "#8ba2b8"
ACTIVE_DOT = "#22c55e"
INACTIVE_DOT = "#ef4444"
GRID_ALPHA = 0.18
DEFAULT_COLORS = ("#5eead4", "#60a5fa", "#f472b6", "#facc15", "#c084fc", "#fb923c")
RATE_WINDOW_SECONDS = 2.0

# A channel that steps more often than this inside the marker window is a
# continuous signal rather than a trigger line, so it contributes no markers.
MAX_DERIVED_MARKERS = 200


def _plot_color(snapshot: StreamSnapshot, position: int) -> str:
    return snapshot.channel_colors[position] or DEFAULT_COLORS[position % len(DEFAULT_COLORS)]


def _decimate(x: np.ndarray, y: np.ndarray, maximum: int = 5000) -> tuple[np.ndarray, np.ndarray]:
    if x.size <= maximum:
        return x, y
    step = max(1, x.size // maximum)
    return x[::step], y[::step]


def _relative_time(snapshot: StreamSnapshot) -> np.ndarray:
    if snapshot.timestamps.size == 0:
        return snapshot.timestamps
    return snapshot.timestamps - snapshot.now_lsl_time


def transmission_rate_text(snapshot: StreamSnapshot) -> str:
    """Describe the measured sample rate next to the rate the outlet advertises."""

    measured = snapshot.measured_rate_hz(RATE_WINDOW_SECONDS)
    channels = len(snapshot.channel_labels)
    if measured is None:
        return "no data received"
    nominal = (
        f"{snapshot.nominal_srate:g} Hz nominal"
        if snapshot.nominal_srate > 0
        else "irregular rate"
    )
    return (
        f"{measured:.1f} samples/s · {nominal} · "
        f"{channels} ch → {measured * channels:.0f} values/s"
    )


def _derived_markers(
    snapshot: StreamSnapshot, positions: list[int], start: float
) -> list[tuple[float, int, str]]:
    """Read markers from numeric trigger channels as steps to a non-zero value."""

    inside = snapshot.timestamps >= start
    times = snapshot.timestamps[inside]
    entries: list[tuple[float, int, str]] = []
    for position in positions:
        values = snapshot.samples[position][inside]
        if values.size < 2 or values.size != times.size:
            continue
        steps = np.flatnonzero(np.diff(values) != 0.0) + 1
        stepped = values[steps]
        steps = steps[np.isfinite(stepped) & (stepped != 0.0)]
        if not 0 < steps.size <= MAX_DERIVED_MARKERS:
            continue
        entries.extend(
            (snapshot.now_lsl_time - float(times[step]), position, f"{values[step]:g}")
            for step in steps
        )
    return entries


def marker_entries(
    snapshot: StreamSnapshot, positions: list[int], seconds: float
) -> list[tuple[float, int, str]]:
    """Return `(age, channel position, text)` markers inside the rolling window.

    Marker samples reported by the stream win; numeric streams fall back to
    derived trigger steps so a trigger channel is readable as events too.
    """

    start = snapshot.now_lsl_time - seconds
    selected = set(positions)
    if snapshot.markers:
        entries = [
            (snapshot.now_lsl_time - event.lsl_time, event.position, event.text)
            for event in snapshot.markers
            if event.position in selected and event.lsl_time >= start
        ]
    else:
        entries = _derived_markers(snapshot, positions, start)
    entries.sort(key=lambda entry: entry[0])
    return entries


class ActivityDot(QtWidgets.QWidget):
    """Small round light: green while the stream is active, red otherwise.

    Every panel carries one, so a stream's health is readable from any panel
    without giving up space to a dedicated `alive` view.
    """

    DIAMETER = 11

    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.setFixedSize(self.DIAMETER, self.DIAMETER)
        self.setToolTip("Waiting for stream")

    def set_state(self, active: bool, age_seconds: float | None) -> None:
        """Recolor the dot and describe the latest sample in its tooltip."""

        age = (
            "no samples received"
            if age_seconds is None
            else f"last sample {age_seconds:.2f} s ago (LSL time)"
        )
        self.setToolTip(f"{'Active' if active else 'Not active'} · {age}")
        if active != self.active:
            self.active = active
            self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        del event
        color = QtGui.QColor(ACTIVE_DOT if self.active else INACTIVE_DOT)
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        painter.setBrush(color)
        painter.setPen(QtGui.QPen(color.darker(150), 1))
        painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))


class MonitorPanel(QtWidgets.QFrame):
    """Shared panel chrome and update contract."""

    def __init__(self, title: str, status_dot: bool = True) -> None:
        super().__init__()
        self.setObjectName("monitorPanel")
        self.layout = QtWidgets.QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(4)
        self.dot = ActivityDot()
        self.dot.setVisible(status_dot)
        self.title_label = QtWidgets.QLabel(title)
        self.title_label.setObjectName("panelTitle")
        self.stream_label = QtWidgets.QLabel()
        self.stream_label.setObjectName("panelStream")
        self.stream_label.setVisible(False)
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(6)
        header.addWidget(self.dot, 0, QtCore.Qt.AlignmentFlag.AlignVCenter)
        header.addWidget(self.title_label, 1)
        header.addWidget(
            self.stream_label,
            0,
            QtCore.Qt.AlignmentFlag.AlignVCenter | QtCore.Qt.AlignmentFlag.AlignRight,
        )
        self.layout.addLayout(header)

    def set_stream_label(self, stream_id: str | None) -> None:
        """Name the stream this panel belongs to, beside its title.

        Panels carry a freely chosen title, so without this the stream a plot
        belongs to is unreadable once several streams are monitored together.
        """

        self.stream_label.setText(stream_id or "")
        self.stream_label.setToolTip(f"Stream {stream_id}" if stream_id else "")
        self.stream_label.setVisible(bool(stream_id))

    def update_snapshot(self, snapshot: StreamSnapshot) -> None:
        """Update the shared chrome, then render the panel body."""

        self.dot.set_state(snapshot.active, snapshot.age_seconds)
        self.render_snapshot(snapshot)

    @abstractmethod
    def render_snapshot(self, snapshot: StreamSnapshot) -> None:
        """Render a stream snapshot."""


class PlotPanel(MonitorPanel):
    """Panel containing a consistently styled PlotWidget."""

    def __init__(self, title: str, status_dot: bool = True) -> None:
        super().__init__(title, status_dot)
        self.plot = pg.PlotWidget(background=BACKGROUND)
        self.plot.showGrid(x=True, y=True, alpha=GRID_ALPHA)
        self.plot.getAxis("left").setTextPen(FOREGROUND)
        self.plot.getAxis("bottom").setTextPen(FOREGROUND)
        self.plot.getAxis("left").setPen(FOREGROUND)
        self.plot.getAxis("bottom").setPen(FOREGROUND)
        self.layout.addWidget(self.plot, 1)


class TracePanel(PlotPanel):
    """Time traces aligned as normalized stacked lanes or raw overlays."""

    #: Emitted with the reordered channel positions after a lane is dragged.
    channels_reordered = QtCore.Signal(object)

    LANE_PEN_WIDTH = 1.5
    DRAGGED_PEN_WIDTH = 3.0

    def __init__(
        self,
        title: str,
        view: ViewConfig,
        positions: list[int],
        history_seconds: float,
        editable: bool = False,
    ) -> None:
        super().__init__(title, view.status_dot)
        self.view = view
        self.positions = list(positions)
        self.history_seconds = history_seconds
        self.curves: list[pg.PlotDataItem] = []
        self._pen_state: tuple[list[int], int | None] | None = None
        self._drag_lane: int | None = None
        self._drag_origin: list[int] = []
        self.plot.setLabel("bottom", "LSL time relative to now", units="s")
        self.plot.setLabel("left", "channels")
        # Lanes only exist as separate rows in stacked mode, so that is the only
        # alignment where dragging one of them means anything.
        self.reorderable = editable and view.alignment == "stacked"
        if self.reorderable:
            self._enable_lane_dragging()

    def _enable_lane_dragging(self) -> None:
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setToolTip("Drag a lane up or down to reorder this panel's channels")
        viewport = self.plot.viewport()
        viewport.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
        viewport.installEventFilter(self)

    def _lane_at(self, point: QtCore.QPointF) -> int:
        scene_point = self.plot.mapToScene(point.toPoint())
        view_point = self.plot.plotItem.vb.mapSceneToView(scene_point)
        lane = int(round(view_point.y()))
        return min(max(lane, 0), len(self.positions) - 1)

    def move_lane(self, source: int, target: int) -> bool:
        """Move the channel shown in `source` to lane `target`."""

        last = len(self.positions) - 1
        if not (0 <= source <= last) or not (0 <= target <= last) or source == target:
            return False
        self.positions.insert(target, self.positions.pop(source))
        return True

    def eventFilter(self, watched: QtCore.QObject, event: QtCore.QEvent) -> bool:
        if not self.reorderable or watched is not self.plot.viewport():
            return super().eventFilter(watched, event)
        event_type = event.type()
        if event_type == QtCore.QEvent.Type.MouseButtonPress:
            if event.button() != QtCore.Qt.MouseButton.LeftButton:
                return super().eventFilter(watched, event)
            self._drag_lane = self._lane_at(event.position())
            self._drag_origin = list(self.positions)
            watched.setCursor(QtCore.Qt.CursorShape.ClosedHandCursor)
            return True
        if self._drag_lane is None:
            return super().eventFilter(watched, event)
        if event_type == QtCore.QEvent.Type.MouseMove:
            self._drag_to_lane(self._lane_at(event.position()))
            return True
        if event_type == QtCore.QEvent.Type.MouseButtonRelease:
            self._drag_to_lane(self._lane_at(event.position()))
            self._drag_lane = None
            watched.setCursor(QtCore.Qt.CursorShape.OpenHandCursor)
            if self.positions != self._drag_origin:
                self.channels_reordered.emit(list(self.positions))
            return True
        return super().eventFilter(watched, event)

    def _drag_to_lane(self, target: int) -> None:
        if self._drag_lane is not None and self.move_lane(self._drag_lane, target):
            self._drag_lane = target

    def _ensure_curves(self, snapshot: StreamSnapshot) -> None:
        while len(self.curves) < len(self.positions):
            self.curves.append(self.plot.plot())
        state = (list(self.positions), self._drag_lane)
        if self._pen_state == state:
            return
        # Colors belong to the channel, so they follow it into its new lane.
        for lane, (curve, position) in enumerate(
            zip(self.curves, self.positions, strict=True)
        ):
            width = (
                self.DRAGGED_PEN_WIDTH
                if lane == self._drag_lane
                else self.LANE_PEN_WIDTH
            )
            curve.setPen(pg.mkPen(_plot_color(snapshot, position), width=width))
        self._pen_state = state

    def render_snapshot(self, snapshot: StreamSnapshot) -> None:
        self._ensure_curves(snapshot)
        x = _relative_time(snapshot)
        if snapshot.samples.shape[1] != x.size:
            return
        self.plot.setXRange(-self.history_seconds, 0.0, padding=0)
        labels: list[tuple[float, str]] = []
        for lane, (curve, position) in enumerate(zip(self.curves, self.positions, strict=True)):
            y = snapshot.samples[position]
            mask = x >= -self.history_seconds
            x_visible, y_visible = x[mask], y[mask]
            if self.view.alignment == "stacked":
                center = float(np.nanmedian(y_visible)) if y_visible.size else 0.0
                spread = (
                    float(np.nanpercentile(np.abs(y_visible - center), 99))
                    if y_visible.size
                    else 1.0
                )
                spread = spread if np.isfinite(spread) and spread > 1e-12 else 1.0
                y_visible = (y_visible - center) / spread * 0.38 + lane
                labels.append((lane, snapshot.channel_labels[position]))
            x_plot, y_plot = _decimate(x_visible, y_visible)
            curve.setData(x_plot, y_plot)

        if self.view.alignment == "stacked":
            self.plot.getAxis("left").setTicks([labels])
            self.plot.setYRange(-0.55, max(0.55, len(self.positions) - 0.45), padding=0)
        else:
            self.plot.getAxis("left").setTicks(None)
            self.plot.enableAutoRange(axis="y", enable=True)


class PlanePanel(PlotPanel):
    """Two-channel trajectory with opacity increasing toward the latest sample."""

    TRAIL_SEGMENTS = 18

    def __init__(
        self,
        title: str,
        view: ViewConfig,
        positions: list[int],
        history_seconds: float,
    ) -> None:
        super().__init__(title, view.status_dot)
        self.view = view
        self.positions = positions
        self.trail_seconds = view.trail_window(history_seconds)
        self.curve: pg.PlotDataItem | None = None
        self.trail_curves: list[pg.PlotDataItem] = []
        self.head = pg.ScatterPlotItem(size=9, brush=pg.mkBrush("#ffffff"))
        self.plot.addItem(self.head)

    def _ensure_trail(self, snapshot: StreamSnapshot) -> None:
        if self.trail_curves:
            return
        color = pg.mkColor(_plot_color(snapshot, self.positions[0]))
        for segment in range(self.TRAIL_SEGMENTS):
            progress = (segment + 1) / self.TRAIL_SEGMENTS
            segment_color = QtGui.QColor(color)
            segment_color.setAlphaF(0.06 + 0.94 * progress**1.7)
            curve = self.plot.plot(
                pen=pg.mkPen(segment_color, width=1.0 + progress * 1.2)
            )
            self.trail_curves.append(curve)
        self.curve = self.trail_curves[-1]

    def render_snapshot(self, snapshot: StreamSnapshot) -> None:
        if len(self.positions) != 2:
            return
        first, second = self.positions
        self._ensure_trail(snapshot)
        self.plot.setLabel("bottom", snapshot.channel_labels[first])
        self.plot.setLabel("left", snapshot.channel_labels[second])
        relative = _relative_time(snapshot)
        mask = relative >= -self.trail_seconds
        x = snapshot.samples[first][mask]
        y = snapshot.samples[second][mask]
        x, y = _decimate(x, y)
        boundaries = np.linspace(0, x.size, self.TRAIL_SEGMENTS + 1, dtype=int)
        for segment, curve in enumerate(self.trail_curves):
            start = boundaries[segment]
            stop = boundaries[segment + 1]
            if segment > 0 and start > 0:
                start -= 1
            curve.setData(x[start:stop], y[start:stop])
        self.head.setData(x=x[-1:] if x.size else [], y=y[-1:] if y.size else [])
        if x.size:
            self.plot.enableAutoRange(x=True, y=True)


class PsdPanel(PlotPanel):
    """Windowed single-sided power spectral density."""

    def __init__(self, title: str, view: ViewConfig, positions: list[int]) -> None:
        super().__init__(title, view.status_dot)
        self.view = view
        self.positions = positions
        self.curves: list[pg.PlotDataItem] = []
        self.plot.setLabel("bottom", "frequency", units="Hz")
        self.plot.setLabel("left", "PSD", units="dB/Hz")

    def _ensure_curves(self, snapshot: StreamSnapshot) -> None:
        while len(self.curves) < len(self.positions):
            position = self.positions[len(self.curves)]
            self.curves.append(
                self.plot.plot(
                    pen=pg.mkPen(_plot_color(snapshot, position), width=1.5),
                    name=snapshot.channel_labels[position],
                )
            )

    @staticmethod
    def _sample_rate(snapshot: StreamSnapshot) -> float:
        if snapshot.timestamps.size > 2:
            difference = np.diff(snapshot.timestamps[-min(1000, snapshot.timestamps.size) :])
            difference = difference[np.isfinite(difference) & (difference > 0)]
            if difference.size:
                return 1.0 / float(np.median(difference))
        return snapshot.nominal_srate

    def render_snapshot(self, snapshot: StreamSnapshot) -> None:
        self._ensure_curves(snapshot)
        sample_rate = self._sample_rate(snapshot)
        if sample_rate <= 0:
            return
        maximum_size = min(self.view.fft_size, snapshot.samples.shape[1])
        if maximum_size < 16:
            return
        fft_size = 1 << (maximum_size.bit_length() - 1)
        window = np.hanning(fft_size)
        normalization = sample_rate * np.sum(window**2)
        frequencies = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate)
        for curve, position in zip(self.curves, self.positions, strict=True):
            values = snapshot.samples[position, -fft_size:]
            values = values - np.nanmean(values)
            spectrum = np.fft.rfft(values * window)
            psd = (np.abs(spectrum) ** 2) / max(normalization, np.finfo(float).eps)
            if fft_size > 1:
                psd[1:-1] *= 2.0
            decibels = 10.0 * np.log10(np.maximum(psd, np.finfo(float).tiny))
            curve.setData(frequencies, decibels)
        maximum_frequency = sample_rate / 2.0
        if self.view.frequency_range:
            low, high = self.view.frequency_range
            self.plot.setXRange(low, min(high, maximum_frequency), padding=0)
        else:
            self.plot.setXRange(0.0, maximum_frequency, padding=0)
        self.plot.enableAutoRange(axis="y", enable=True)


class MarkerPanel(PlotPanel):
    """Markers rolling up a time window, newest entering at the bottom.

    Age is the vertical axis, so every event drifts upward at real speed and
    fades out as it leaves the window, like rolling credits.
    """

    TEXT_X = 0.07
    DOT_X = 0.03

    def __init__(
        self,
        title: str,
        view: ViewConfig,
        positions: list[int],
        marker_seconds: float,
    ) -> None:
        super().__init__(title, view.status_dot)
        self.view = view
        self.positions = positions
        self.marker_seconds = marker_seconds
        self.limit = max(1, view.marker_limit)
        self.entries: list[tuple[float, int, str]] = []
        self.texts: list[pg.TextItem] = []
        self.dots = pg.ScatterPlotItem(size=7, pen=None)
        self.plot.addItem(self.dots)
        self.plot.showGrid(x=False, y=True, alpha=GRID_ALPHA)
        self.plot.setLabel("left", "age in LSL time", units="s")
        self.plot.getAxis("bottom").setStyle(showValues=False)
        self.plot.setMouseEnabled(x=False, y=False)
        self.plot.hideButtons()
        self.plot.setXRange(0.0, 1.0, padding=0)
        self.summary = QtWidgets.QLabel()
        self.summary.setStyleSheet(f"color: {MUTED};")
        self.layout.addWidget(self.summary)

    def _ensure_texts(self, count: int) -> None:
        while len(self.texts) < min(count, self.limit):
            item = pg.TextItem(anchor=(0.0, 0.5))
            self.plot.addItem(item, ignoreBounds=True)
            self.texts.append(item)

    def _display_text(self, snapshot: StreamSnapshot, position: int, text: str) -> str:
        if len(self.positions) < 2:
            return text
        return f"{snapshot.channel_labels[position]} · {text}"

    def render_snapshot(self, snapshot: StreamSnapshot) -> None:
        self.entries = marker_entries(snapshot, self.positions, self.marker_seconds)
        self.plot.setYRange(0.0, self.marker_seconds, padding=0)
        visible = self.entries[: self.limit]
        self._ensure_texts(len(visible))
        ages: list[float] = []
        brushes: list[QtGui.QBrush] = []
        for item, (age, position, text) in zip(self.texts, visible, strict=False):
            color = QtGui.QColor(_plot_color(snapshot, position))
            remaining = 1.0 - min(1.0, age / max(self.marker_seconds, 1e-9))
            color.setAlphaF(0.25 + 0.75 * remaining**0.7)
            item.setText(self._display_text(snapshot, position, text), color=color)
            item.setPos(self.TEXT_X, age)
            item.setVisible(True)
            ages.append(age)
            brushes.append(pg.mkBrush(color))
        for item in self.texts[len(visible) :]:
            item.setVisible(False)
        self.dots.setData(x=[self.DOT_X] * len(ages), y=ages, brush=brushes)
        self.summary.setText(self._summary_text())

    def _summary_text(self) -> str:
        window = f"last {self.marker_seconds:g} s"
        if not self.entries:
            return f"No markers in the {window}"
        latest = self.entries[0][0]
        hidden = max(0, len(self.entries) - self.limit)
        text = f"{len(self.entries)} markers in the {window} · newest {latest:.2f} s ago"
        return f"{text} · {hidden} older hidden" if hidden else text


class AlivePanel(MonitorPanel):
    """Large green/red stream health indicator with LSL-time sample age."""

    def __init__(self, title: str, view: ViewConfig) -> None:
        super().__init__(title, view.status_dot)
        self.view = view
        self.indicator = QtWidgets.QLabel("NOT ACTIVE")
        self.indicator.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        font = QtGui.QFont()
        font.setPointSize(20)
        font.setBold(True)
        self.indicator.setFont(font)
        self.rate = QtWidgets.QLabel("no data received")
        self.rate.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.rate.setWordWrap(True)
        self.rate.setStyleSheet(f"color: {FOREGROUND}; font-weight: 600;")
        self.details = QtWidgets.QLabel("Waiting for stream")
        self.details.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.details.setWordWrap(True)
        self.details.setStyleSheet(f"color: {MUTED};")
        self.layout.addWidget(self.indicator, 1)
        self.layout.addWidget(self.rate)
        self.layout.addWidget(self.details)

    def render_snapshot(self, snapshot: StreamSnapshot) -> None:
        if snapshot.active:
            color = "#15803d"
            self.indicator.setText("ACTIVE")
        else:
            color = "#b91c1c"
            self.indicator.setText("NOT ACTIVE")
        self.indicator.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 8px; padding: 12px;"
        )
        self.rate.setText(transmission_rate_text(snapshot))
        if snapshot.age_seconds is None:
            age = "no samples received"
        else:
            age = f"last sample {snapshot.age_seconds:.2f} s ago (LSL time)"
        self.details.setText(f"{snapshot.message}\n{age}")


def create_panel(
    title: str,
    view: ViewConfig,
    positions: list[int],
    history_seconds: float,
    editable: bool = False,
    stream_id: str | None = None,
) -> MonitorPanel:
    """Create the configured panel for either the live monitor or designer.

    `editable` enables the designer-only gestures, currently dragging trace
    lanes into a new channel order. `stream_id` names the stream in the panel
    header.
    """

    panel = _build_panel(title, view, positions, history_seconds, editable)
    panel.set_stream_label(stream_id)
    return panel


def _build_panel(
    title: str,
    view: ViewConfig,
    positions: list[int],
    history_seconds: float,
    editable: bool,
) -> MonitorPanel:
    if view.type == "traces":
        return TracePanel(title, view, positions, history_seconds, editable=editable)
    if view.type == "plane_2d":
        if len(positions) != 2:
            raise ConfigError(f"{title!r}: plane_2d requires exactly 2 channels")
        return PlanePanel(title, view, positions, history_seconds)
    if view.type == "psd":
        return PsdPanel(title, view, positions)
    if view.type == "markers":
        return MarkerPanel(title, view, positions, view.marker_window(history_seconds))
    return AlivePanel(title, view)
