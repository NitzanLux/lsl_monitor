"""Visual editor for building monitor configurations with mock signals."""

from __future__ import annotations

import copy
import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from lsl_monitor.config import (
    COLORMAPS,
    DEFAULT_COLORMAP,
    DEFAULT_DYNAMIC_RANGE_DB,
    DEFAULT_LEVEL_SECONDS,
    DEFAULT_MARKER_LIMIT,
    ConfigError,
    MonitorConfig,
    StreamConfig,
    configured_view_positions,
    default_schema_path,
    monitor_config_from_document,
)
from lsl_monitor.layout import PanelCanvas, RelativeRect, stretched_rectangles
from lsl_monitor.mock import (
    DEFAULT_MOCK_MODEL,
    MOCK_MODELS,
    make_mock_snapshot,
    mock_model_label,
)
from lsl_monitor.views import DEFAULT_COLORS, MonitorPanel, TracePanel, create_panel

Document = dict[str, Any]

VIEW_TYPES = ("traces", "plane_2d", "psd", "spectrogram", "audio", "alive", "markers")

VIEW_TITLES = {
    "traces": "Signal traces",
    "plane_2d": "2D plane",
    "psd": "Power spectrum",
    "spectrogram": "Spectrogram",
    "audio": "Audio monitor",
    "alive": "Stream health",
    "markers": "Marker roll",
}

#: The arguments each panel type accepts, beyond its title, channels, and layout.
VIEW_ARGUMENTS: dict[str, tuple[str, ...]] = {
    "traces": ("alignment",),
    "plane_2d": ("trail_seconds",),
    "psd": ("fft_size", "frequency_range"),
    "spectrogram": (
        "fft_size",
        "frequency_range",
        "spectrogram_seconds",
        "dynamic_range_db",
        "colormap",
    ),
    "audio": ("level_seconds", "audio_gain", "audio_muted"),
    "markers": ("marker_seconds", "marker_limit"),
    "alive": (),
}

#: Arguments every panel type accepts.
SHARED_VIEW_ARGUMENTS = ("status_dot",)

VIEW_ARGUMENT_HELP = {
    "traces": "Stacked lanes normalize each channel; overlay shares one axis.",
    "plane_2d": "The trail window is how much history the trajectory keeps.",
    "psd": "FFT size sets the resolution; the limit crops the frequency axis.",
    "spectrogram": (
        "The heat map draws the first selected channel. FFT size trades time "
        "resolution for frequency resolution, and the dynamic range is measured "
        "down from the loudest bin."
    ),
    "audio": (
        "Meters read every selected channel; one of them is played, chosen in "
        "the panel. Gain scales both, and playback starts muted unless asked."
    ),
    "markers": "The roll window and row count decide how many credits stay on screen.",
    "alive": "The health panel is driven by the stream alone.",
}

# LSL does not constrain StreamInfo.type(); these are the content types recommended
# by the XDF meta-data conventions plus the generic names used by common outlets.
# The picker stays editable because any other string is equally valid.
LSL_STREAM_TYPES = (
    "EEG",
    "MEG",
    "ECoG",
    "EMG",
    "ECG",
    "EOG",
    "EDA",
    "GSR",
    "NIRS",
    "Respiration",
    "HeartRate",
    "Temperature",
    "Gaze",
    "EyeTracking",
    "Pupil",
    "MoCap",
    "Position",
    "Orientation",
    "Accelerometer",
    "Gyroscope",
    "Magnetometer",
    "Force",
    "Audio",
    "VideoRaw",
    "VideoCompressed",
    "Keyboard",
    "Mouse",
    "Markers",
    "Signals",
    "Control",
)


def channel_reference(channel: Document) -> int | str:
    """Return the channel's canonical LSL selector."""

    if "index" in channel:
        return int(channel["index"])
    return str(channel["name"])


def new_document() -> Document:
    """Return a useful two-channel starter document."""

    return {
        "$schema": "../schemas/lsl-monitor.schema.json",
        "window": {
            "title": "My LSL monitor",
            "history_seconds": 10,
            "refresh_hz": 20,
            "columns": 2,
            "inactive_after_seconds": 2,
            "max_points_per_channel": 100000,
        },
        "streams": [
            {
                "id": "mock_stream",
                "match": {"identity": "My stream", "type": "Signals"},
                "channels": [
                    {"index": 0, "label": "Channel 1", "color": DEFAULT_COLORS[0]},
                    {"index": 1, "label": "Channel 2", "color": DEFAULT_COLORS[1]},
                ],
                "views": [
                    {
                        "type": "traces",
                        "title": "Signal traces",
                        "channels": [0, 1],
                        "alignment": "stacked",
                    },
                    {"type": "alive", "title": "Stream health"},
                ],
            }
        ],
    }


def document_for_path(document: Document, path: str | Path) -> Document:
    """Copy a document and make its schema reference relative to its save path."""

    target = Path(path).resolve()
    result = copy.deepcopy(document)
    relative_schema = os.path.relpath(default_schema_path(), target.parent)
    result["$schema"] = Path(relative_schema).as_posix()
    monitor_config_from_document(result, target)
    return result


def save_document(document: Document, path: str | Path) -> Path:
    """Validate and save a designer document."""

    target = Path(path).resolve()
    exported = document_for_path(document, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(exported, indent=2) + "\n", encoding="utf-8")
    return target


def load_document(path: str | Path) -> Document:
    """Load a configuration into an editable document."""

    source = Path(path).resolve()
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read {source}: {error}") from error
    monitor_config_from_document(document, source)
    return document


def reordered(order: Sequence[int], source: int, target: int) -> list[int]:
    """Return `order` with the entry at `source` moved to position `target`."""

    result = list(order)
    last = len(result) - 1
    if not (0 <= source <= last) or not (0 <= target <= last):
        return result
    result.insert(target, result.pop(source))
    return result


class ReorderableTable(QtWidgets.QTableWidget):
    """Table whose whole rows are dragged into a new order.

    Qt's built-in internal move drops individual cells, so the drop is handled
    here and reported as a permutation for the owner to apply to its document.
    """

    rows_reordered = QtCore.Signal(object)

    def __init__(self, rows: int, columns: int) -> None:
        super().__init__(rows, columns)
        self.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        if event.source() is not self:
            super().dropEvent(event)
            return
        # The rows are rebuilt from the reordered document, so the default cell
        # move is suppressed to keep the table and the document in step.
        event.setDropAction(QtCore.Qt.DropAction.IgnoreAction)
        event.accept()
        source = self.currentRow()
        target = self._drop_row(event.position().toPoint(), source)
        if source >= 0 and target != source:
            self.rows_reordered.emit(reordered(range(self.rowCount()), source, target))
            # The owner refills the table, so the moved row is re-selected here.
            self.setCurrentCell(target, max(0, self.currentColumn()))

    def _drop_row(self, position: QtCore.QPoint, source: int) -> int:
        """Return the row `source` lands on for a drop at `position`."""

        index = self.indexAt(position)
        if not index.isValid():
            return self.rowCount() - 1
        row = index.row()
        if position.y() > self.visualRect(index).center().y():
            row += 1
        if row > source:
            row -= 1
        return min(max(row, 0), self.rowCount() - 1)


class ReorderableList(QtWidgets.QListWidget):
    """Checkable list whose items are dragged into a new order."""

    items_reordered = QtCore.Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.SingleSelection
        )
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setDragDropMode(QtWidgets.QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.DropAction.MoveAction)

    def dropEvent(self, event: QtGui.QDropEvent) -> None:
        # Qt re-creates the moved items, so itemChanged would fire against a
        # half-rebuilt list; one signal after the move replaces that noise.
        self.blockSignals(True)
        try:
            super().dropEvent(event)
        finally:
            self.blockSignals(False)
        self.items_reordered.emit()


class FrequencyRangeEdit(QtWidgets.QWidget):
    """Optional lower and upper frequency bounds for a PSD panel."""

    changed = QtCore.Signal()

    DEFAULT_RANGE = (0.0, 60.0)

    def __init__(self) -> None:
        super().__init__()
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self.enabled = QtWidgets.QCheckBox()
        self.enabled.setToolTip("Crop the frequency axis instead of showing 0 Hz to Nyquist")
        self.low = self._spin()
        self.high = self._spin()
        row.addWidget(self.enabled)
        row.addWidget(self.low, 1)
        row.addWidget(QtWidgets.QLabel("to"))
        row.addWidget(self.high, 1)
        self.set_value(None)
        self.enabled.toggled.connect(self._changed)
        self.low.valueChanged.connect(self._changed)
        self.high.valueChanged.connect(self._changed)

    @staticmethod
    def _spin() -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(0.0, 100_000.0)
        spin.setDecimals(1)
        spin.setSuffix(" Hz")
        return spin

    def value(self) -> tuple[float, float] | None:
        """Return the requested bounds, or `None` while the limit is switched off."""

        if not self.enabled.isChecked():
            return None
        low = self.low.value()
        return low, max(self.high.value(), low + 1.0)

    def set_value(self, value: Sequence[float] | None) -> None:
        low, high = value if value else self.DEFAULT_RANGE
        self.enabled.setChecked(value is not None)
        self.low.setValue(float(low))
        self.high.setValue(float(high))
        self._update_enabled_state()

    def _update_enabled_state(self) -> None:
        for spin in (self.low, self.high):
            spin.setEnabled(self.enabled.isChecked())

    @QtCore.Slot()
    def _changed(self) -> None:
        self._update_enabled_state()
        self.changed.emit()


class DashboardPreview(QtWidgets.QFrame):
    """Live dashboard preview backed by generated mock snapshots."""

    panel_geometry_changed = QtCore.Signal(object, object)
    panel_channels_reordered = QtCore.Signal(object, object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("previewSurface")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        self.canvas = PanelCanvas(editable=True)
        self.canvas.geometry_changed.connect(self._canvas_geometry_changed)
        layout.addWidget(self.canvas)
        self.error_label = QtWidgets.QLabel()
        self.error_label.setObjectName("previewError")
        self.error_label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)
        self.config: MonitorConfig | None = None
        self.panels: list[tuple[MonitorPanel, str]] = []
        self.panel_paths: list[tuple[int, int]] = []
        self.models: list[str] = []
        self.started_at = time.monotonic()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(50)

    def _clear(self) -> None:
        self.canvas.clear_panels()
        self.panels.clear()
        self.panel_paths.clear()

    def set_document(
        self, document: Document, models: Sequence[str] | None = None
    ) -> str | None:
        """Rebuild the preview, returning a validation error when incomplete.

        `models` names one mock signal model per stream, in document order.
        """

        self._clear()
        self.models = list(models or ())
        try:
            self.config = monitor_config_from_document(document)
        except ConfigError as error:
            self.config = None
            self.canvas.hide()
            self.error_label.setText(str(error))
            self.error_label.show()
            return str(error)

        self.error_label.hide()
        self.canvas.show()
        self.canvas.columns = self.config.window.columns
        for stream_index, stream in enumerate(self.config.streams):
            for view_index, view in enumerate(stream.views):
                positions = configured_view_positions(view.channels, stream.channels)
                title = view.title or view.type
                panel = create_panel(
                    title,
                    view,
                    positions,
                    self.config.window.history_seconds,
                    editable=True,
                    stream_id=stream.id,
                )
                if isinstance(panel, TracePanel) and panel.reorderable:
                    panel.channels_reordered.connect(
                        lambda order, path=(stream_index, view_index): (
                            self.panel_channels_reordered.emit(path, order)
                        )
                    )
                rectangle = (
                    RelativeRect(
                        view.layout.x,
                        view.layout.y,
                        view.layout.width,
                        view.layout.height,
                    )
                    if view.layout
                    else None
                )
                self.canvas.add_panel(panel, rectangle)
                self.panels.append((panel, stream.id))
                self.panel_paths.append((stream_index, view_index))
        self.refresh()
        return None

    def model_for_stream(self, index: int) -> str:
        """Return the mock signal model selected for a stream position."""

        if 0 <= index < len(self.models):
            return self.models[index]
        return DEFAULT_MOCK_MODEL

    def current_layouts(self) -> list[tuple[tuple[int, int], RelativeRect]]:
        """Return effective geometry for every preview panel."""

        return list(zip(self.panel_paths, self.canvas.rectangles(), strict=True))

    @QtCore.Slot(int, object)
    def _canvas_geometry_changed(self, index: int, rectangle: RelativeRect) -> None:
        if 0 <= index < len(self.panel_paths):
            self.panel_geometry_changed.emit(self.panel_paths[index], rectangle)

    def _marker_seconds(self, stream: StreamConfig) -> float:
        """Return the longest marker roll requested by a stream's panels."""

        history = self.config.window.history_seconds if self.config else 10.0
        return max(
            (
                view.marker_window(history)
                for view in stream.views
                if view.type == "markers"
            ),
            default=history,
        )

    @QtCore.Slot()
    def refresh(self) -> None:
        if self.config is None:
            return
        now = 1000.0 + time.monotonic() - self.started_at
        snapshots = {
            stream.id: make_mock_snapshot(
                stream,
                now,
                self.config.window.history_seconds,
                model=self.model_for_stream(index),
                marker_seconds=self._marker_seconds(stream),
            )
            for index, stream in enumerate(self.config.streams)
        }
        for panel, stream_id in self.panels:
            panel.update_snapshot(snapshots[stream_id])


class DesignerWindow(QtWidgets.QMainWindow):
    """Form-based configuration editor with an immediate visual preview."""

    def __init__(
        self,
        document: Document | None = None,
        source_path: str | Path | None = None,
    ) -> None:
        super().__init__()
        self.document = copy.deepcopy(document or new_document())
        self.source_path = Path(source_path).resolve() if source_path else None
        self.stream_models: list[str] = []
        self._loading = False
        self._preview_timer = QtCore.QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.timeout.connect(self._refresh_preview)

        self.setWindowTitle("LSL Monitor · Layout Designer")
        self.resize(1600, 980)
        self._build_toolbar()
        self._build_interface()
        self._load_document_into_controls()

    def _build_toolbar(self) -> None:
        toolbar = QtWidgets.QToolBar("File", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for text, shortcut, slot in (
            ("New", "Ctrl+N", self.new_file),
            ("Open", "Ctrl+O", self.open_file),
            ("Save", "Ctrl+S", self.save_file),
            ("Save as", "Ctrl+Shift+S", self.save_file_as),
        ):
            action = toolbar.addAction(text)
            action.setShortcut(QtGui.QKeySequence(shortcut))
            action.triggered.connect(slot)
        toolbar.addSeparator()
        hint = QtWidgets.QLabel("  Mock signals preview your production layout")
        hint.setObjectName("toolbarHint")
        toolbar.addWidget(hint)

    def _build_interface(self) -> None:
        splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        editor_scroll = QtWidgets.QScrollArea()
        editor_scroll.setWidgetResizable(True)
        editor_scroll.setMinimumWidth(410)
        editor_scroll.setMaximumWidth(560)
        editor = QtWidgets.QWidget()
        self.editor_layout = QtWidgets.QVBoxLayout(editor)
        self.editor_layout.setContentsMargins(14, 14, 14, 14)
        self.editor_layout.setSpacing(12)
        editor_scroll.setWidget(editor)
        splitter.addWidget(editor_scroll)

        self._build_window_card()
        self._build_stream_card()
        self._build_channels_card()
        self._build_views_card()
        self.editor_layout.addStretch(1)

        preview_host = QtWidgets.QWidget()
        preview_layout = QtWidgets.QVBoxLayout(preview_host)
        preview_layout.setContentsMargins(14, 14, 14, 14)
        preview_layout.setSpacing(8)
        header_row = QtWidgets.QHBoxLayout()
        preview_title = QtWidgets.QLabel("LIVE LAYOUT PREVIEW")
        preview_title.setObjectName("sectionHeading")
        header_row.addWidget(preview_title)
        header_row.addStretch(1)
        self.fit_to_screen_button = QtWidgets.QPushButton("Fit to Screen")
        self.fit_to_screen_button.setToolTip(
            "Proportionally stretch the existing layout to fill the monitor window"
        )
        self.fit_to_screen_button.clicked.connect(self._fit_to_screen)
        header_row.addWidget(self.fit_to_screen_button)
        self.validation_status = QtWidgets.QLabel()
        self.validation_status.setObjectName("validationStatus")
        header_row.addWidget(self.validation_status)
        preview_layout.addLayout(header_row)
        self.preview = DashboardPreview()
        self.preview.panel_geometry_changed.connect(self._preview_geometry_changed)
        self.preview.panel_channels_reordered.connect(self._preview_channels_reordered)
        preview_layout.addWidget(self.preview, 1)
        splitter.addWidget(preview_host)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    @staticmethod
    def _card(title: str) -> tuple[QtWidgets.QFrame, QtWidgets.QVBoxLayout]:
        card = QtWidgets.QFrame()
        card.setObjectName("editorCard")
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        heading = QtWidgets.QLabel(title.upper())
        heading.setObjectName("sectionHeading")
        layout.addWidget(heading)
        return card, layout

    @staticmethod
    def _form_row(
        layout: QtWidgets.QFormLayout, label: str, widget: QtWidgets.QWidget
    ) -> None:
        layout.addRow(label, widget)

    def _build_window_card(self) -> None:
        card, layout = self._card("Dashboard")
        form = QtWidgets.QFormLayout()
        self.title_edit = QtWidgets.QLineEdit()
        self.columns_spin = QtWidgets.QSpinBox()
        self.columns_spin.setRange(1, 8)
        self.history_spin = QtWidgets.QDoubleSpinBox()
        self.history_spin.setRange(0.5, 3600)
        self.history_spin.setSuffix(" s")
        self.history_spin.setDecimals(1)
        self.refresh_spin = QtWidgets.QSpinBox()
        self.refresh_spin.setRange(1, 120)
        self.refresh_spin.setSuffix(" Hz")
        self._form_row(form, "Window title", self.title_edit)
        self._form_row(form, "Grid columns", self.columns_spin)
        self._form_row(form, "Trace history", self.history_spin)
        self._form_row(form, "Refresh rate", self.refresh_spin)
        layout.addLayout(form)
        self.editor_layout.addWidget(card)
        self.title_edit.textChanged.connect(self._window_changed)
        self.columns_spin.valueChanged.connect(self._window_changed)
        self.history_spin.valueChanged.connect(self._window_changed)
        self.refresh_spin.valueChanged.connect(self._window_changed)

    def _build_stream_card(self) -> None:
        card, layout = self._card("Mock stream")
        picker_row = QtWidgets.QHBoxLayout()
        self.stream_combo = QtWidgets.QComboBox()
        self.stream_combo.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        picker_row.addWidget(self.stream_combo, 1)
        add_button = QtWidgets.QPushButton("+")
        add_button.setToolTip("Add mock stream")
        remove_button = QtWidgets.QPushButton("−")
        remove_button.setToolTip("Remove stream")
        picker_row.addWidget(add_button)
        picker_row.addWidget(remove_button)
        layout.addLayout(picker_row)
        model_row = QtWidgets.QHBoxLayout()
        model_row.addWidget(QtWidgets.QLabel("Preview signal"))
        self.model_combo = QtWidgets.QComboBox()
        for name in MOCK_MODELS:
            self.model_combo.addItem(mock_model_label(name), name)
        self.model_combo.setToolTip(
            "Mock waveform used for this stream in the preview. Marker events drives "
            "trigger lines aligned with the events in a marker roll."
        )
        model_row.addWidget(self.model_combo, 1)
        layout.addLayout(model_row)
        form = QtWidgets.QFormLayout()
        self.stream_id_edit = QtWidgets.QLineEdit()
        self.stream_identity_edit = QtWidgets.QLineEdit()
        self.stream_type_combo = self._stream_type_combo()
        self.hostname_edit = QtWidgets.QLineEdit()
        self.stream_id_edit.setPlaceholderText("internal_eeg")
        self.stream_identity_edit.setPlaceholderText("Exact LSL name and source_id")
        self.hostname_edit.setPlaceholderText("Optional computer name")
        self.stream_id_edit.setToolTip(
            "Unique internal ID used by this dashboard; it is not an LSL field"
        )
        self.stream_identity_edit.setToolTip(
            "The shared value expected in both the outlet name and source_id"
        )
        self.hostname_edit.setToolTip(
            "Exact outlet computer name; use this only to select among duplicates"
        )
        self._form_row(form, "Monitor ID", self.stream_id_edit)
        self._form_row(form, "LSL name / source ID", self.stream_identity_edit)
        self._form_row(form, "LSL type", self.stream_type_combo)
        self._form_row(form, "LSL hostname", self.hostname_edit)
        layout.addLayout(form)
        match_help = QtWidgets.QLabel(
            "Monitor ID is unique only inside this configuration. The shared LSL "
            "identity is matched against both the outlet name and source ID. The "
            "preview signal is mocked and is not saved."
        )
        match_help.setObjectName("helpText")
        match_help.setWordWrap(True)
        layout.addWidget(match_help)
        self.editor_layout.addWidget(card)
        self.stream_combo.currentIndexChanged.connect(self._load_stream)
        self.model_combo.currentIndexChanged.connect(self._model_changed)
        self.stream_type_combo.currentTextChanged.connect(self._stream_changed)
        add_button.clicked.connect(self._add_stream)
        remove_button.clicked.connect(self._remove_stream)
        for edit in (
            self.stream_id_edit,
            self.stream_identity_edit,
            self.hostname_edit,
        ):
            edit.textChanged.connect(self._stream_changed)

    @staticmethod
    def _stream_type_combo() -> QtWidgets.QComboBox:
        """Return a scrollable, editable picker over the known LSL content types."""

        combo = QtWidgets.QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QtWidgets.QComboBox.InsertPolicy.NoInsert)
        combo.setMaxVisibleItems(12)
        combo.addItem("")
        combo.addItems(list(LSL_STREAM_TYPES))
        combo.setItemData(0, "Match any stream type", QtCore.Qt.ItemDataRole.ToolTipRole)
        combo.lineEdit().setPlaceholderText("Any type — pick one or type your own")
        combo.setToolTip(
            "Exact StreamInfo type to match. LSL accepts any string, so a custom "
            "type can be typed instead of choosing from the list."
        )
        completer = combo.completer()
        if completer is not None:
            completer.setCaseSensitivity(QtCore.Qt.CaseSensitivity.CaseInsensitive)
            completer.setCompletionMode(
                QtWidgets.QCompleter.CompletionMode.PopupCompletion
            )
        return combo

    def _build_channels_card(self) -> None:
        card, layout = self._card("Channels")
        self.channel_table = ReorderableTable(0, 3)
        self.channel_table.setHorizontalHeaderLabels(
            ["LSL index / name", "Display label", "Color"]
        )
        self.channel_table.horizontalHeader().setSectionResizeMode(
            1, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.channel_table.setMinimumHeight(145)
        self.channel_table.setToolTip(
            "Drag a row to change the channel order used by every panel"
        )
        layout.addWidget(self.channel_table)
        order_help = QtWidgets.QLabel(
            "Drag a row to reorder the channels. Panels follow this order unless a "
            "panel is given its own."
        )
        order_help.setObjectName("helpText")
        order_help.setWordWrap(True)
        layout.addWidget(order_help)
        row = QtWidgets.QHBoxLayout()
        add_button = QtWidgets.QPushButton("Add channel")
        remove_button = QtWidgets.QPushButton("Remove selected")
        color_button = QtWidgets.QPushButton("Pick color")
        row.addWidget(add_button)
        row.addWidget(remove_button)
        row.addWidget(color_button)
        layout.addLayout(row)
        self.editor_layout.addWidget(card)
        add_button.clicked.connect(self._add_channel)
        remove_button.clicked.connect(self._remove_channel)
        color_button.clicked.connect(self._pick_channel_color)
        self.channel_table.itemChanged.connect(self._channels_changed)
        self.channel_table.rows_reordered.connect(self._reorder_channels)

    def _build_views_card(self) -> None:
        card, layout = self._card("View panels")
        self.view_list = QtWidgets.QListWidget()
        self.view_list.setMinimumHeight(130)
        layout.addWidget(self.view_list)
        add_row = QtWidgets.QHBoxLayout()
        self.new_view_type = QtWidgets.QComboBox()
        self.new_view_type.addItems(list(VIEW_TYPES))
        add_button = QtWidgets.QPushButton("Add panel")
        add_row.addWidget(self.new_view_type, 1)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)
        order_row = QtWidgets.QHBoxLayout()
        up_button = QtWidgets.QPushButton("Move up")
        down_button = QtWidgets.QPushButton("Move down")
        remove_button = QtWidgets.QPushButton("Remove")
        order_row.addWidget(up_button)
        order_row.addWidget(down_button)
        order_row.addWidget(remove_button)
        layout.addLayout(order_row)

        separator = QtWidgets.QFrame()
        separator.setFrameShape(QtWidgets.QFrame.Shape.HLine)
        layout.addWidget(separator)
        form = QtWidgets.QFormLayout()
        self.view_title_edit = QtWidgets.QLineEdit()
        self.view_type_combo = QtWidgets.QComboBox()
        self.view_type_combo.addItems(list(VIEW_TYPES))
        self._form_row(form, "Title", self.view_title_edit)
        self._form_row(form, "Panel type", self.view_type_combo)
        layout.addLayout(form)
        self._build_view_arguments(layout)
        channel_label = QtWidgets.QLabel("CHANNELS SHOWN IN THIS PANEL")
        channel_label.setObjectName("minorHeading")
        layout.addWidget(channel_label)
        self.view_channels = ReorderableList()
        self.view_channels.setMinimumHeight(100)
        self.view_channels.setToolTip(
            "Check the channels to show, and drag them into the order this panel "
            "draws them in"
        )
        layout.addWidget(self.view_channels)
        view_channel_help = QtWidgets.QLabel(
            "Drag to order this panel's channels, or drag a lane in the preview."
        )
        view_channel_help.setObjectName("helpText")
        view_channel_help.setWordWrap(True)
        layout.addWidget(view_channel_help)
        layout_heading = QtWidgets.QLabel("RESPONSIVE PANEL GEOMETRY")
        layout_heading.setObjectName("minorHeading")
        layout.addWidget(layout_heading)
        layout_help = QtWidgets.QLabel(
            "Drag the panel title and use its lower-right handle, or enter percentages."
        )
        layout_help.setObjectName("helpText")
        layout_help.setWordWrap(True)
        layout.addWidget(layout_help)
        self.custom_layout_check = QtWidgets.QCheckBox("Use custom placement")
        layout.addWidget(self.custom_layout_check)
        geometry_grid = QtWidgets.QGridLayout()
        self.layout_x_spin = self._percentage_spin()
        self.layout_y_spin = self._percentage_spin()
        self.layout_width_spin = self._percentage_spin(minimum=8.0)
        self.layout_height_spin = self._percentage_spin(minimum=8.0)
        geometry_grid.addWidget(QtWidgets.QLabel("Left"), 0, 0)
        geometry_grid.addWidget(self.layout_x_spin, 0, 1)
        geometry_grid.addWidget(QtWidgets.QLabel("Top"), 0, 2)
        geometry_grid.addWidget(self.layout_y_spin, 0, 3)
        geometry_grid.addWidget(QtWidgets.QLabel("Width"), 1, 0)
        geometry_grid.addWidget(self.layout_width_spin, 1, 1)
        geometry_grid.addWidget(QtWidgets.QLabel("Height"), 1, 2)
        geometry_grid.addWidget(self.layout_height_spin, 1, 3)
        layout.addLayout(geometry_grid)
        reset_layout_button = QtWidgets.QPushButton("Reset panel to automatic layout")
        layout.addWidget(reset_layout_button)
        self.editor_layout.addWidget(card)

        self.view_list.currentRowChanged.connect(self._load_view)
        add_button.clicked.connect(self._add_view)
        remove_button.clicked.connect(self._remove_view)
        up_button.clicked.connect(lambda: self._move_view(-1))
        down_button.clicked.connect(lambda: self._move_view(1))
        self.view_title_edit.textChanged.connect(self._view_changed)
        self.view_type_combo.currentTextChanged.connect(self._view_changed)
        self.view_channels.itemChanged.connect(self._view_changed)
        self.view_channels.items_reordered.connect(self._view_changed)
        self.custom_layout_check.toggled.connect(self._layout_mode_changed)
        for spin in (
            self.layout_x_spin,
            self.layout_y_spin,
            self.layout_width_spin,
            self.layout_height_spin,
        ):
            spin.valueChanged.connect(self._layout_values_changed)
        reset_layout_button.clicked.connect(self._reset_panel_layout)

    def _build_view_arguments(self, layout: QtWidgets.QVBoxLayout) -> None:
        """Build the argument rows, of which only the panel type's own are shown."""

        heading = QtWidgets.QLabel("ARGUMENTS FOR THIS PANEL TYPE")
        heading.setObjectName("minorHeading")
        layout.addWidget(heading)
        self.alignment_combo = QtWidgets.QComboBox()
        self.alignment_combo.addItems(["stacked", "overlay"])
        self.alignment_combo.setToolTip(
            "Stacked lanes are normalized per channel; overlay keeps the raw values"
        )
        self.trail_seconds_spin = self._seconds_spin(
            "How much history the 2D trajectory keeps, independent of trace history"
        )
        self.fft_spin = QtWidgets.QSpinBox()
        self.fft_spin.setRange(16, 1048576)
        self.fft_spin.setSingleStep(128)
        self.fft_spin.setToolTip("Samples per spectrum; larger windows resolve finer peaks")
        self.frequency_range_edit = FrequencyRangeEdit()
        self.spectrogram_seconds_spin = self._seconds_spin(
            "How much time the spectrogram scrolls through, independent of trace history"
        )
        self.dynamic_range_spin = QtWidgets.QDoubleSpinBox()
        self.dynamic_range_spin.setRange(6.0, 120.0)
        self.dynamic_range_spin.setDecimals(0)
        self.dynamic_range_spin.setSingleStep(6.0)
        self.dynamic_range_spin.setSuffix(" dB")
        self.dynamic_range_spin.setToolTip(
            "Decibels below the loudest bin that are still colored; smaller values "
            "raise the contrast"
        )
        self.colormap_combo = QtWidgets.QComboBox()
        self.colormap_combo.addItems(list(COLORMAPS))
        self.colormap_combo.setToolTip("Color map used for the spectrogram heat map")
        self.level_seconds_spin = QtWidgets.QDoubleSpinBox()
        self.level_seconds_spin.setRange(0.05, 10.0)
        self.level_seconds_spin.setDecimals(2)
        self.level_seconds_spin.setSingleStep(0.05)
        self.level_seconds_spin.setSuffix(" s")
        self.level_seconds_spin.setToolTip(
            "History each level meter integrates; short windows react faster"
        )
        self.audio_gain_spin = QtWidgets.QDoubleSpinBox()
        self.audio_gain_spin.setRange(0.01, 100.0)
        self.audio_gain_spin.setDecimals(2)
        self.audio_gain_spin.setSingleStep(0.25)
        self.audio_gain_spin.setPrefix("× ")
        self.audio_gain_spin.setToolTip(
            "Applied to the meters and to playback; 1 reads the samples as full scale"
        )
        self.audio_muted_check = QtWidgets.QCheckBox("Start muted")
        self.audio_muted_check.setToolTip(
            "Leave checked so opening the dashboard is silent until Listen is pressed"
        )
        self.marker_seconds_spin = self._seconds_spin(
            "How far back the marker roll reaches, independent of trace history"
        )
        self.marker_limit_spin = QtWidgets.QSpinBox()
        self.marker_limit_spin.setRange(1, 500)
        self.marker_limit_spin.setToolTip(
            "Marker rows drawn at once; older events are only counted in the summary"
        )
        self.status_dot_check = QtWidgets.QCheckBox("Show the stream activity dot")
        self.status_dot_check.setToolTip(
            "Green while samples arrive inside inactive_after_seconds, red otherwise"
        )
        self.view_arguments_form = QtWidgets.QFormLayout()
        self.view_argument_widgets: dict[str, QtWidgets.QWidget] = {
            "alignment": self.alignment_combo,
            "trail_seconds": self.trail_seconds_spin,
            "fft_size": self.fft_spin,
            "frequency_range": self.frequency_range_edit,
            "spectrogram_seconds": self.spectrogram_seconds_spin,
            "dynamic_range_db": self.dynamic_range_spin,
            "colormap": self.colormap_combo,
            "level_seconds": self.level_seconds_spin,
            "audio_gain": self.audio_gain_spin,
            "audio_muted": self.audio_muted_check,
            "marker_seconds": self.marker_seconds_spin,
            "marker_limit": self.marker_limit_spin,
            "status_dot": self.status_dot_check,
        }
        for label, name in (
            ("Trace alignment", "alignment"),
            ("Trail window", "trail_seconds"),
            ("FFT size", "fft_size"),
            ("Frequency limit", "frequency_range"),
            ("Time window", "spectrogram_seconds"),
            ("Dynamic range", "dynamic_range_db"),
            ("Color map", "colormap"),
            ("Meter window", "level_seconds"),
            ("Output gain", "audio_gain"),
            ("Playback", "audio_muted"),
            ("Roll window", "marker_seconds"),
            ("Rows shown", "marker_limit"),
            ("Activity dot", "status_dot"),
        ):
            self._form_row(
                self.view_arguments_form, label, self.view_argument_widgets[name]
            )
        layout.addLayout(self.view_arguments_form)
        self.view_arguments_help = QtWidgets.QLabel()
        self.view_arguments_help.setObjectName("helpText")
        self.view_arguments_help.setWordWrap(True)
        layout.addWidget(self.view_arguments_help)

        self.alignment_combo.currentTextChanged.connect(self._view_changed)
        self.trail_seconds_spin.valueChanged.connect(self._view_changed)
        self.fft_spin.valueChanged.connect(self._view_changed)
        self.frequency_range_edit.changed.connect(self._view_changed)
        self.spectrogram_seconds_spin.valueChanged.connect(self._view_changed)
        self.dynamic_range_spin.valueChanged.connect(self._view_changed)
        self.colormap_combo.currentTextChanged.connect(self._view_changed)
        self.level_seconds_spin.valueChanged.connect(self._view_changed)
        self.audio_gain_spin.valueChanged.connect(self._view_changed)
        self.audio_muted_check.toggled.connect(self._view_changed)
        self.marker_seconds_spin.valueChanged.connect(self._view_changed)
        self.marker_limit_spin.valueChanged.connect(self._view_changed)
        self.status_dot_check.toggled.connect(self._view_changed)

    @staticmethod
    def _seconds_spin(tooltip: str) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(1.0, 3600.0)
        spin.setDecimals(1)
        spin.setSuffix(" s")
        spin.setToolTip(tooltip)
        return spin

    @staticmethod
    def _percentage_spin(minimum: float = 0.0) -> QtWidgets.QDoubleSpinBox:
        spin = QtWidgets.QDoubleSpinBox()
        spin.setRange(minimum, 100.0)
        spin.setDecimals(1)
        spin.setSingleStep(1.0)
        spin.setSuffix(" %")
        return spin

    def _current_stream(self) -> Document | None:
        index = self.stream_combo.currentIndex()
        streams = self.document["streams"]
        return streams[index] if 0 <= index < len(streams) else None

    def _current_view(self) -> Document | None:
        stream = self._current_stream()
        row = self.view_list.currentRow()
        if stream is None or not (0 <= row < len(stream["views"])):
            return None
        return stream["views"][row]

    def _model_for_stream(self, index: int) -> str:
        """Return the preview model of a stream position, defaulting when unset."""

        if 0 <= index < len(self.stream_models):
            return self.stream_models[index]
        return DEFAULT_MOCK_MODEL

    def _load_document_into_controls(self) -> None:
        self._loading = True
        window = self.document.setdefault("window", {})
        self.title_edit.setText(window.get("title", "LSL Monitor"))
        self.columns_spin.setValue(int(window.get("columns", 2)))
        self.history_spin.setValue(float(window.get("history_seconds", 10)))
        self.refresh_spin.setValue(int(window.get("refresh_hz", 20)))
        self.stream_combo.clear()
        for stream in self.document["streams"]:
            self.stream_combo.addItem(stream["id"])
        self.stream_models = [DEFAULT_MOCK_MODEL for _ in self.document["streams"]]
        self._loading = False
        self.stream_combo.setCurrentIndex(0)
        self._load_stream(0)
        self._refresh_preview()

    @QtCore.Slot()
    def _window_changed(self) -> None:
        if self._loading:
            return
        self.document["window"].update(
            {
                "title": self.title_edit.text().strip() or "LSL Monitor",
                "columns": self.columns_spin.value(),
                "history_seconds": self.history_spin.value(),
                "refresh_hz": self.refresh_spin.value(),
            }
        )
        self._schedule_preview()

    @QtCore.Slot(int)
    def _load_stream(self, index: int) -> None:
        streams = self.document["streams"]
        if not (0 <= index < len(streams)):
            return
        stream = streams[index]
        self._loading = True
        self.model_combo.setCurrentIndex(
            max(0, self.model_combo.findData(self._model_for_stream(index)))
        )
        self.stream_id_edit.setText(stream["id"])
        self.stream_identity_edit.setText(
            stream["match"].get(
                "identity",
                stream["match"].get("source_id", stream["match"].get("name", "")),
            )
        )
        self.stream_type_combo.setCurrentText(stream["match"].get("type", ""))
        self.hostname_edit.setText(stream["match"].get("hostname", ""))
        self._populate_channel_table(stream)
        self._rebuild_view_list()
        self._loading = False
        self.view_list.setCurrentRow(0)
        self._load_view(0)

    def _populate_channel_table(self, stream: Document) -> None:
        """Fill the channel table from the document, in document order."""

        self.channel_table.setRowCount(0)
        for channel in stream["channels"]:
            row = self.channel_table.rowCount()
            self.channel_table.insertRow(row)
            values = (
                str(channel_reference(channel)),
                channel.get("label", f"Channel {row + 1}"),
                channel.get("color", DEFAULT_COLORS[row % len(DEFAULT_COLORS)]),
            )
            for column, value in enumerate(values):
                self.channel_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))

    @QtCore.Slot()
    def _stream_changed(self) -> None:
        if self._loading:
            return
        stream = self._current_stream()
        if stream is None:
            return
        stream["id"] = self.stream_id_edit.text().strip()
        identity = self.stream_identity_edit.text().strip()
        match = {
            key: value
            for key, value in (
                ("identity", identity),
                ("type", self.stream_type_combo.currentText().strip()),
                ("hostname", self.hostname_edit.text().strip()),
            )
            if value
        }
        stream["match"] = match
        self.stream_combo.setItemText(self.stream_combo.currentIndex(), stream["id"])
        self._schedule_preview()

    @QtCore.Slot()
    def _model_changed(self) -> None:
        if self._loading:
            return
        index = self.stream_combo.currentIndex()
        if not (0 <= index < len(self.stream_models)):
            return
        self.stream_models[index] = self.model_combo.currentData()
        self._schedule_preview()

    @QtCore.Slot()
    def _add_stream(self) -> None:
        existing = {stream["id"] for stream in self.document["streams"]}
        number = 1
        while f"stream_{number}" in existing:
            number += 1
        stream = {
            "id": f"stream_{number}",
            "match": {"identity": f"stream_{number}", "type": "Signals"},
            "channels": [
                {"index": 0, "label": "Channel 1", "color": DEFAULT_COLORS[0]}
            ],
            "views": [{"type": "alive", "title": "Stream health"}],
        }
        self.document["streams"].append(stream)
        self.stream_models.append(DEFAULT_MOCK_MODEL)
        self.stream_combo.addItem(stream["id"])
        self.stream_combo.setCurrentIndex(self.stream_combo.count() - 1)
        self._schedule_preview()

    @QtCore.Slot()
    def _remove_stream(self) -> None:
        if len(self.document["streams"]) <= 1:
            self._message("At least one stream is required.")
            return
        index = self.stream_combo.currentIndex()
        self.document["streams"].pop(index)
        if 0 <= index < len(self.stream_models):
            self.stream_models.pop(index)
        self.stream_combo.removeItem(index)
        self.stream_combo.setCurrentIndex(min(index, self.stream_combo.count() - 1))
        self._schedule_preview()

    @QtCore.Slot()
    def _channels_changed(self) -> None:
        if self._loading:
            return
        stream = self._current_stream()
        if stream is None:
            return
        channels = []
        for row in range(self.channel_table.rowCount()):
            selector_item = self.channel_table.item(row, 0)
            label_item = self.channel_table.item(row, 1)
            color_item = self.channel_table.item(row, 2)
            selector = selector_item.text().strip() if selector_item else str(row)
            try:
                channel: Document = {"index": max(0, int(selector))}
            except ValueError:
                channel = {"name": selector or f"Ch{row}"}
            label = label_item.text().strip() if label_item else ""
            color = color_item.text().strip() if color_item else ""
            if label:
                channel["label"] = label
            if color:
                channel["color"] = color
            channels.append(channel)
        stream["channels"] = channels
        valid_references = {
            alias
            for channel in channels
            for alias in (
                channel_reference(channel),
                channel.get("label"),
            )
            if alias is not None
        }
        for view in stream["views"]:
            if "channels" in view:
                view["channels"] = [
                    reference
                    for reference in view["channels"]
                    if reference in valid_references
                ]
                if not view["channels"]:
                    view.pop("channels")
        selected_view = self.view_list.currentRow()
        self._load_view(selected_view)
        self._schedule_preview()

    @staticmethod
    def _channel_order(stream: Document) -> dict[int | str, int]:
        """Map every alias of a stream's channels to its position."""

        order: dict[int | str, int] = {}
        for position, channel in enumerate(stream["channels"]):
            order[channel_reference(channel)] = position
            label = channel.get("label")
            if label is not None:
                order.setdefault(label, position)
        return order

    def _sort_view_channels(self, stream: Document) -> None:
        """Re-sort each panel's explicit channel list into the stream order."""

        order = self._channel_order(stream)
        unknown = len(order)
        for view in stream["views"]:
            references = view.get("channels")
            if references:
                view["channels"] = sorted(
                    references, key=lambda reference: order.get(reference, unknown)
                )

    @QtCore.Slot(object)
    def _reorder_channels(self, order: Sequence[int]) -> None:
        """Apply a drag reorder of the channel table to the document."""

        stream = self._current_stream()
        if stream is None:
            return
        channels = stream["channels"]
        if sorted(order) != list(range(len(channels))):
            return
        stream["channels"] = [channels[position] for position in order]
        # Panels keep showing the same channels, restacked into the new order.
        self._sort_view_channels(stream)
        self._loading = True
        self._populate_channel_table(stream)
        self._loading = False
        self._load_view(self.view_list.currentRow())
        self._schedule_preview()

    @QtCore.Slot(object, object)
    def _preview_channels_reordered(
        self, path: tuple[int, int], positions: Sequence[int]
    ) -> None:
        """Store the channel order produced by dragging a lane in the preview."""

        stream_index, view_index = path
        stream = self.document["streams"][stream_index]
        channels = stream["channels"]
        stream["views"][view_index]["channels"] = [
            channel_reference(channels[position])
            for position in positions
            if 0 <= position < len(channels)
        ]
        selected_path = (self.stream_combo.currentIndex(), self.view_list.currentRow())
        if path == selected_path:
            self._load_view(view_index)
        self._schedule_preview()

    @QtCore.Slot()
    def _add_channel(self) -> None:
        existing = []
        for row in range(self.channel_table.rowCount()):
            item = self.channel_table.item(row, 0)
            if item:
                try:
                    existing.append(int(item.text()))
                except ValueError:
                    pass
        index = max(existing, default=-1) + 1
        self._loading = True
        row = self.channel_table.rowCount()
        self.channel_table.insertRow(row)
        values = (
            str(index),
            f"Channel {index + 1}",
            DEFAULT_COLORS[row % len(DEFAULT_COLORS)],
        )
        for column, value in enumerate(values):
            self.channel_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
        self._loading = False
        self._channels_changed()

    @QtCore.Slot()
    def _remove_channel(self) -> None:
        rows = sorted(
            {index.row() for index in self.channel_table.selectedIndexes()}, reverse=True
        )
        if not rows:
            return
        if self.channel_table.rowCount() - len(rows) < 1:
            self._message("Each stream needs at least one channel.")
            return
        self._loading = True
        for row in rows:
            self.channel_table.removeRow(row)
        self._loading = False
        self._channels_changed()

    @QtCore.Slot()
    def _pick_channel_color(self) -> None:
        row = self.channel_table.currentRow()
        if row < 0:
            return
        item = self.channel_table.item(row, 2)
        initial = QtGui.QColor(item.text() if item else DEFAULT_COLORS[0])
        color = QtWidgets.QColorDialog.getColor(initial, self, "Channel color")
        if color.isValid():
            if item is None:
                item = QtWidgets.QTableWidgetItem()
                self.channel_table.setItem(row, 2, item)
            item.setText(color.name())

    def _rebuild_view_list(self, selected: int = 0) -> None:
        stream = self._current_stream()
        self.view_list.clear()
        if stream is None:
            return
        for view in stream["views"]:
            title = view.get("title") or view["type"]
            self.view_list.addItem(f"{title}  ·  {view['type']}")
        if self.view_list.count():
            self.view_list.setCurrentRow(min(selected, self.view_list.count() - 1))

    @QtCore.Slot(int)
    def _load_view(self, row: int) -> None:
        stream = self._current_stream()
        if stream is None or not (0 <= row < len(stream["views"])):
            return
        view = stream["views"][row]
        self._loading = True
        self.view_title_edit.setText(view.get("title", ""))
        self.view_type_combo.setCurrentText(view["type"])
        self._load_view_arguments(view)
        self.view_channels.clear()
        configured = view.get("channels")
        for position in self._view_channel_positions(stream, view):
            channel = stream["channels"][position]
            reference = channel_reference(channel)
            label = channel.get("label", f"Ch{reference}")
            selector_kind = "index" if isinstance(reference, int) else "name"
            item = QtWidgets.QListWidgetItem(
                f"{label}  ({selector_kind} {reference})"
            )
            item.setData(QtCore.Qt.ItemDataRole.UserRole, reference)
            flags = item.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable
            flags |= QtCore.Qt.ItemFlag.ItemIsDragEnabled
            # Drops must land between rows, never replace the row underneath.
            flags &= ~QtCore.Qt.ItemFlag.ItemIsDropEnabled
            item.setFlags(flags)
            checked = configured is None or any(
                candidate in configured
                for candidate in (reference, channel.get("label"))
                if candidate is not None
            )
            item.setCheckState(
                QtCore.Qt.CheckState.Checked
                if checked
                else QtCore.Qt.CheckState.Unchecked
            )
            self.view_channels.addItem(item)
        rectangle = view.get("layout")
        if rectangle is None:
            effective = self._effective_preview_rectangle(
                (self.stream_combo.currentIndex(), row)
            )
            rectangle = effective.as_document() if effective else {
                "x": 0.0,
                "y": 0.0,
                "width": 0.5,
                "height": 0.5,
            }
        self.custom_layout_check.setChecked("layout" in view)
        self.layout_x_spin.setValue(float(rectangle["x"]) * 100.0)
        self.layout_y_spin.setValue(float(rectangle["y"]) * 100.0)
        self.layout_width_spin.setValue(float(rectangle["width"]) * 100.0)
        self.layout_height_spin.setValue(float(rectangle["height"]) * 100.0)
        self._update_view_control_visibility()
        self._loading = False

    @staticmethod
    def _view_argument_names(view_type: str) -> tuple[str, ...]:
        """Return every argument a panel type accepts, shared ones included."""

        return VIEW_ARGUMENTS.get(view_type, ()) + SHARED_VIEW_ARGUMENTS

    def _load_view_arguments(self, view: Document) -> None:
        """Show a panel's arguments, falling back to each argument's default."""

        history = self.history_spin.value()
        self.alignment_combo.setCurrentText(view.get("alignment", "stacked"))
        self.trail_seconds_spin.setValue(float(view.get("trail_seconds", history)))
        self.fft_spin.setValue(int(view.get("fft_size", 1024)))
        self.frequency_range_edit.set_value(view.get("frequency_range"))
        self.spectrogram_seconds_spin.setValue(
            float(view.get("spectrogram_seconds", history))
        )
        self.dynamic_range_spin.setValue(
            float(view.get("dynamic_range_db", DEFAULT_DYNAMIC_RANGE_DB))
        )
        self.colormap_combo.setCurrentText(view.get("colormap", DEFAULT_COLORMAP))
        self.level_seconds_spin.setValue(
            float(view.get("level_seconds", DEFAULT_LEVEL_SECONDS))
        )
        self.audio_gain_spin.setValue(float(view.get("audio_gain", 1.0)))
        self.audio_muted_check.setChecked(bool(view.get("audio_muted", True)))
        self.marker_seconds_spin.setValue(float(view.get("marker_seconds", history)))
        self.marker_limit_spin.setValue(
            int(view.get("marker_limit", DEFAULT_MARKER_LIMIT))
        )
        self.status_dot_check.setChecked(bool(view.get("status_dot", True)))

    def _view_channel_positions(self, stream: Document, view: Document) -> list[int]:
        """Return channel positions in the order this panel draws them.

        Selected channels come first, in their configured order, followed by the
        unselected ones, so the list itself expresses the panel's channel order.
        """

        every = list(range(len(stream["channels"])))
        configured = view.get("channels")
        if not configured:
            return every
        aliases = self._channel_order(stream)
        selected: list[int] = []
        for reference in configured:
            position = aliases.get(reference)
            if position is not None and position not in selected:
                selected.append(position)
        return selected + [
            position for position in every if position not in selected
        ]

    def _update_view_control_visibility(self) -> None:
        view_type = self.view_type_combo.currentText()
        shown = self._view_argument_names(view_type)
        for name, widget in self.view_argument_widgets.items():
            self.view_arguments_form.setRowVisible(widget, name in shown)
        self.view_arguments_help.setText(VIEW_ARGUMENT_HELP.get(view_type, ""))
        self.view_channels.setEnabled(view_type != "alive")
        custom = self.custom_layout_check.isChecked()
        for spin in (
            self.layout_x_spin,
            self.layout_y_spin,
            self.layout_width_spin,
            self.layout_height_spin,
        ):
            spin.setEnabled(custom)

    def _effective_preview_rectangle(
        self, path: tuple[int, int]
    ) -> RelativeRect | None:
        for candidate_path, rectangle in self.preview.current_layouts():
            if candidate_path == path:
                return rectangle
        return None

    def _materialize_preview_layouts(self) -> None:
        for (stream_index, view_index), rectangle in self.preview.current_layouts():
            self.document["streams"][stream_index]["views"][view_index]["layout"] = (
                rectangle.as_document()
            )

    @QtCore.Slot()
    def _fit_to_screen(self) -> None:
        """Proportionally stretch the current layout to every window edge."""

        current_layouts = self.preview.current_layouts()
        fitted = stretched_rectangles(
            [rectangle for _, rectangle in current_layouts]
        )
        for (path, _), rectangle in zip(current_layouts, fitted, strict=True):
            stream_index, view_index = path
            self.document["streams"][stream_index]["views"][view_index]["layout"] = (
                rectangle.as_document()
            )

        self._preview_timer.stop()
        self._refresh_preview()
        current_row = self.view_list.currentRow()
        if current_row >= 0:
            self._load_view(current_row)

    @QtCore.Slot(bool)
    def _layout_mode_changed(self, checked: bool) -> None:
        if self._loading:
            return
        view = self._current_view()
        if view is None:
            return
        if checked:
            self._materialize_preview_layouts()
            rectangle = view.get("layout")
            if rectangle:
                self._loading = True
                self.layout_x_spin.setValue(float(rectangle["x"]) * 100.0)
                self.layout_y_spin.setValue(float(rectangle["y"]) * 100.0)
                self.layout_width_spin.setValue(float(rectangle["width"]) * 100.0)
                self.layout_height_spin.setValue(float(rectangle["height"]) * 100.0)
                self._loading = False
        else:
            view.pop("layout", None)
        self._update_view_control_visibility()
        self._schedule_preview()

    @QtCore.Slot()
    def _layout_values_changed(self) -> None:
        if self._loading or not self.custom_layout_check.isChecked():
            return
        view = self._current_view()
        if view is None:
            return
        width = max(0.08, self.layout_width_spin.value() / 100.0)
        height = max(0.08, self.layout_height_spin.value() / 100.0)
        x = min(self.layout_x_spin.value() / 100.0, 1.0 - width)
        y = min(self.layout_y_spin.value() / 100.0, 1.0 - height)
        rectangle = RelativeRect(x, y, width, height).bounded()
        view["layout"] = rectangle.as_document()
        self._loading = True
        self.layout_x_spin.setValue(rectangle.x * 100.0)
        self.layout_y_spin.setValue(rectangle.y * 100.0)
        self.layout_width_spin.setValue(rectangle.width * 100.0)
        self.layout_height_spin.setValue(rectangle.height * 100.0)
        self._loading = False
        self._schedule_preview()

    @QtCore.Slot()
    def _reset_panel_layout(self) -> None:
        view = self._current_view()
        if view is None:
            return
        view.pop("layout", None)
        self._loading = True
        self.custom_layout_check.setChecked(False)
        self._loading = False
        self._update_view_control_visibility()
        self._schedule_preview()

    @QtCore.Slot(object, object)
    def _preview_geometry_changed(
        self, path: tuple[int, int], rectangle: RelativeRect
    ) -> None:
        self._materialize_preview_layouts()
        stream_index, view_index = path
        self.document["streams"][stream_index]["views"][view_index]["layout"] = (
            rectangle.as_document()
        )
        selected_path = (
            self.stream_combo.currentIndex(),
            self.view_list.currentRow(),
        )
        if path == selected_path:
            self._loading = True
            self.custom_layout_check.setChecked(True)
            self.layout_x_spin.setValue(rectangle.x * 100.0)
            self.layout_y_spin.setValue(rectangle.y * 100.0)
            self.layout_width_spin.setValue(rectangle.width * 100.0)
            self.layout_height_spin.setValue(rectangle.height * 100.0)
            self._loading = False
            self._update_view_control_visibility()
        self.validation_status.setText(
            f"Valid configuration · {len(self.preview.panels)} panels · custom layout"
        )

    @QtCore.Slot()
    def _view_changed(self) -> None:
        if self._loading:
            return
        view = self._current_view()
        if view is None:
            return
        view_type = self.view_type_combo.currentText()
        layout = view.get("layout")
        view.clear()
        view["type"] = view_type
        if layout is not None:
            view["layout"] = layout
        title = self.view_title_edit.text().strip()
        if title:
            view["title"] = title
        view.update(self._view_argument_values(view_type))
        if view_type != "alive":
            selected = [
                self.view_channels.item(index).data(QtCore.Qt.ItemDataRole.UserRole)
                for index in range(self.view_channels.count())
                if self.view_channels.item(index).checkState()
                == QtCore.Qt.CheckState.Checked
            ]
            if selected:
                view["channels"] = selected
        self._update_view_control_visibility()
        row = self.view_list.currentRow()
        self._loading = True
        self.view_list.item(row).setText(f"{title or view_type}  ·  {view_type}")
        self._loading = False
        self._schedule_preview()

    def _view_argument_values(self, view_type: str) -> Document:
        """Collect the arguments a panel type accepts, omitting plain defaults."""

        values: Document = {
            "alignment": self.alignment_combo.currentText(),
            "trail_seconds": self.trail_seconds_spin.value(),
            "fft_size": self.fft_spin.value(),
            "frequency_range": self.frequency_range_edit.value(),
            "spectrogram_seconds": self.spectrogram_seconds_spin.value(),
            "dynamic_range_db": self.dynamic_range_spin.value(),
            "colormap": self.colormap_combo.currentText(),
            "level_seconds": self.level_seconds_spin.value(),
            "audio_gain": self.audio_gain_spin.value(),
            # Panels open muted by default, so only playback is worth saving.
            "audio_muted": False if not self.audio_muted_check.isChecked() else None,
            "marker_seconds": self.marker_seconds_spin.value(),
            "marker_limit": self.marker_limit_spin.value(),
            # The dot is on by default, so only its absence is worth saving.
            "status_dot": False if not self.status_dot_check.isChecked() else None,
        }
        names = self._view_argument_names(view_type)
        return {
            name: list(value) if isinstance(value, tuple) else value
            for name, value in values.items()
            if name in names and value is not None
        }

    @QtCore.Slot()
    def _add_view(self) -> None:
        stream = self._current_stream()
        if stream is None:
            return
        view_type = self.new_view_type.currentText()
        view: Document = {"type": view_type, "title": VIEW_TITLES[view_type]}
        indices = [channel_reference(channel) for channel in stream["channels"]]
        if view_type == "plane_2d":
            if len(indices) < 2:
                self._message("Add at least two channels before creating a 2D plane.")
                return
            view["channels"] = indices[:2]
        elif view_type == "spectrogram":
            # A heat map reads one signal, so it starts on the first channel.
            view["channels"] = indices[:1]
        elif view_type != "alive":
            view["channels"] = indices
        if view_type == "traces":
            view["alignment"] = "stacked"
        if view_type == "psd":
            view["fft_size"] = 1024
        if view_type == "spectrogram":
            # Shorter windows than a PSD uses, because time is an axis here.
            view["fft_size"] = 256
            view["colormap"] = DEFAULT_COLORMAP
        stream["views"].append(view)
        self._loading = True
        self._rebuild_view_list(len(stream["views"]) - 1)
        self._loading = False
        self.view_list.setCurrentRow(len(stream["views"]) - 1)
        self._load_view(len(stream["views"]) - 1)
        self._schedule_preview()

    @QtCore.Slot()
    def _remove_view(self) -> None:
        stream = self._current_stream()
        row = self.view_list.currentRow()
        if stream is None or row < 0:
            return
        if len(stream["views"]) <= 1:
            self._message("Each stream needs at least one view panel.")
            return
        stream["views"].pop(row)
        self._loading = True
        self._rebuild_view_list(max(0, row - 1))
        self._loading = False
        self.view_list.setCurrentRow(max(0, row - 1))
        self._load_view(self.view_list.currentRow())
        self._schedule_preview()

    def _move_view(self, direction: int) -> None:
        stream = self._current_stream()
        row = self.view_list.currentRow()
        destination = row + direction
        if stream is None or not (0 <= destination < len(stream["views"])):
            return
        stream["views"][row], stream["views"][destination] = (
            stream["views"][destination],
            stream["views"][row],
        )
        self._loading = True
        self._rebuild_view_list(destination)
        self._loading = False
        self.view_list.setCurrentRow(destination)
        self._load_view(destination)
        self._schedule_preview()

    def _schedule_preview(self) -> None:
        self._preview_timer.start(100)

    @QtCore.Slot()
    def _refresh_preview(self) -> None:
        error = self.preview.set_document(self.document, self.stream_models)
        if error:
            first_line = error.splitlines()[0]
            self.validation_status.setText(f"Needs attention · {first_line}")
            self.validation_status.setProperty("valid", False)
        else:
            count = len(self.preview.panels)
            self.validation_status.setText(f"Valid configuration · {count} panels")
            self.validation_status.setProperty("valid", True)
        self.validation_status.style().unpolish(self.validation_status)
        self.validation_status.style().polish(self.validation_status)

    @QtCore.Slot()
    def new_file(self) -> None:
        self.document = new_document()
        self.source_path = None
        self._load_document_into_controls()

    @QtCore.Slot()
    def open_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open monitor configuration",
            str(Path.cwd() / "json"),
            "Monitor JSON (*.monitor.json *.json)",
        )
        if not filename:
            return
        try:
            self.document = load_document(filename)
        except ConfigError as error:
            self._message(str(error), error=True)
            return
        self.source_path = Path(filename).resolve()
        self._load_document_into_controls()

    @QtCore.Slot()
    def save_file(self) -> None:
        if self.source_path is None:
            self.save_file_as()
            return
        self._save_to(self.source_path)

    @QtCore.Slot()
    def save_file_as(self) -> None:
        initial = self.source_path or Path.cwd() / "json" / "my-monitor.monitor.json"
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save monitor configuration",
            str(initial),
            "Monitor JSON (*.monitor.json);;JSON (*.json)",
        )
        if filename:
            self._save_to(Path(filename))

    def _save_to(self, path: Path) -> None:
        try:
            self.source_path = save_document(self.document, path)
            self.document = load_document(self.source_path)
        except ConfigError as error:
            self._message(
                f"Fix the highlighted configuration before saving.\n\n{error}",
                error=True,
            )
            return
        self.statusBar().showMessage(f"Saved {self.source_path}", 5000)
        self._refresh_preview()

    def _message(self, text: str, error: bool = False) -> None:
        icon = (
            QtWidgets.QMessageBox.Icon.Critical
            if error
            else QtWidgets.QMessageBox.Icon.Information
        )
        message = QtWidgets.QMessageBox(icon, "LSL Monitor Designer", text, parent=self)
        message.exec()


DESIGNER_STYLESHEET = """
QMainWindow, QWidget {
  background: #081018;
  color: #dce7f3;
  font-family: "Segoe UI";
  font-size: 12px;
}
QToolBar {
  background: #0f1924;
  border: 0;
  border-bottom: 1px solid #26384b;
  padding: 7px;
  spacing: 5px;
}
QToolBar QToolButton, QPushButton {
  background: #1a2a3a;
  border: 1px solid #30475d;
  border-radius: 5px;
  padding: 6px 10px;
  color: #e8f1fa;
}
QToolBar QToolButton:hover, QPushButton:hover { background: #24415a; }
QToolBar QToolButton:pressed, QPushButton:pressed { background: #0e7490; }
QLabel#toolbarHint, QLabel#helpText { color: #8296aa; }
QFrame#editorCard {
  background: #101a25;
  border: 1px solid #243649;
  border-radius: 9px;
}
QLabel#sectionHeading {
  color: #67e8f9;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1px;
}
QLabel#minorHeading {
  color: #8ba2b8;
  font-size: 10px;
  font-weight: 700;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QListWidget, QTableWidget {
  background: #0a131d;
  border: 1px solid #2b4054;
  border-radius: 5px;
  padding: 5px;
  selection-background-color: #0e7490;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QListWidget:focus, QTableWidget:focus { border: 1px solid #22d3ee; }
/* combobox-popup: 0 keeps long popups inside a scrollable list of
   maxVisibleItems rows instead of one tall menu. */
QComboBox { combobox-popup: 0; }
QComboBox QAbstractItemView {
  background: #0a131d;
  border: 1px solid #2b4054;
  color: #dce7f3;
  outline: 0;
  padding: 2px;
  selection-background-color: #0e7490;
}
QComboBox QAbstractItemView::item { min-height: 22px; padding: 2px 6px; }
QHeaderView::section {
  background: #162434;
  color: #aac0d5;
  border: 0;
  padding: 5px;
}
QFrame#previewSurface {
  background: #0b1118;
  border: 1px solid #26384b;
  border-radius: 10px;
}
QFrame#monitorPanel {
  background: #101720;
  border: 1px solid #263445;
  border-radius: 7px;
}
QLabel#panelTitle {
  color: #f0f4f8;
  font-size: 13px;
  font-weight: 600;
  padding: 2px;
}
QLabel#panelStream {
  color: #8ba2b8;
  background: #16202c;
  border: 1px solid #263445;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  padding: 1px 6px;
}
QLabel#previewError {
  color: #fca5a5;
  background: #2a1116;
  border: 1px solid #7f1d1d;
  border-radius: 8px;
  padding: 24px;
}
QLabel#panelResizeGrip {
  color: #ecfeff;
  background: #0e7490;
  border: 1px solid #67e8f9;
  border-radius: 4px;
  font-size: 12px;
  font-weight: 700;
}
QLabel#validationStatus[valid="true"] { color: #4ade80; font-weight: 600; }
QLabel#validationStatus[valid="false"] { color: #fb7185; font-weight: 600; }
QStatusBar { border-top: 1px solid #26384b; }
QSplitter::handle { background: #152333; width: 2px; }
QScrollBar:vertical { background: #0a131d; width: 10px; }
QScrollBar::handle:vertical { background: #30475d; border-radius: 4px; }
"""


def run_designer(initial_path: str | Path | None = None) -> int:
    """Launch the visual layout designer."""

    pg.setConfigOptions(antialias=False)
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    application.setStyle("Fusion")
    application.setStyleSheet(DESIGNER_STYLESHEET)
    document = None
    source_path = None
    if initial_path:
        candidate = Path(initial_path)
        if candidate.exists():
            try:
                document = load_document(candidate)
                source_path = candidate
            except ConfigError as error:
                print(error, file=sys.stderr)
                return 2
    window = DesignerWindow(document, source_path)
    window.show()
    return application.exec()
