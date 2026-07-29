import json
import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6 import QtCore, QtGui, QtWidgets

from lsl_monitor.config import monitor_config_from_document
from lsl_monitor.designer import (
    LSL_STREAM_TYPES,
    DashboardPreview,
    DesignerWindow,
    document_for_path,
    new_document,
    reordered,
    save_document,
)
from lsl_monitor.mock import DEFAULT_MOCK_MODEL, make_mock_snapshot
from lsl_monitor.views import AudioPanel, SpectrogramPanel, TracePanel


def test_starter_document_is_valid_and_mockable() -> None:
    config = monitor_config_from_document(new_document())

    snapshot = make_mock_snapshot(
        config.streams[0],
        now_lsl_time=100.0,
        history_seconds=config.window.history_seconds,
    )

    assert snapshot.active is True
    assert snapshot.samples.shape[0] == 2
    assert snapshot.samples.shape[1] == snapshot.timestamps.size
    assert snapshot.channel_labels == ("Channel 1", "Channel 2")


def test_preview_signal_covers_the_window_a_spectrogram_scrolls_through() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    document = new_document()
    # Four times the trace history: the preview has to mock that much signal.
    document["streams"][0]["views"] = [
        {
            "type": "spectrogram",
            "channels": [0],
            "fft_size": 256,
            "spectrogram_seconds": 40,
        }
    ]
    preview = DashboardPreview()

    assert preview.set_document(document) is None
    application.processEvents()

    panel, _ = preview.panels[0]
    drawn = panel.image.mapRectToView(panel.image.boundingRect())
    assert isinstance(panel, SpectrogramPanel)
    assert drawn.left() == pytest.approx(-40.0, abs=0.5)
    assert drawn.right() == pytest.approx(0.0, abs=0.5)
    preview.close()


def test_saved_document_has_relative_schema_reference(tmp_path: Path) -> None:
    target = tmp_path / "configs" / "experiment.monitor.json"

    save_document(new_document(), target)
    decoded = json.loads(target.read_text(encoding="utf-8"))

    assert not Path(decoded["$schema"]).is_absolute()
    assert document_for_path(decoded, target)["$schema"] == decoded["$schema"]
    assert monitor_config_from_document(decoded, target).streams[0].id == "mock_stream"


def test_dashboard_preview_renders_every_configured_panel() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    preview = DashboardPreview()

    error = preview.set_document(new_document())
    preview.resize(900, 600)
    preview.show()
    application.processEvents()

    assert error is None
    assert len(preview.panels) == 2
    preview.close()


def test_designer_preserves_channels_selected_by_metadata_name() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    document = new_document()
    stream = document["streams"][0]
    stream["channels"] = [
        {"name": "Cz", "label": "Center", "color": "#5eead4"},
        {"name": "Pz", "label": "Posterior", "color": "#60a5fa"},
    ]
    stream["views"][0]["channels"] = ["Cz", "Posterior"]

    window = DesignerWindow(document)
    window.show()
    application.processEvents()

    assert window.channel_table.item(0, 0).text() == "Cz"
    assert len(window.preview.panels) == 2
    window.close()


def test_designer_switches_the_preview_signal_model_per_stream() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()
    application.processEvents()

    window._add_stream()
    window.model_combo.setCurrentIndex(window.model_combo.findData("spikes"))
    window._refresh_preview()
    application.processEvents()

    assert window.stream_models == [DEFAULT_MOCK_MODEL, "spikes"]
    assert window.document["streams"][1]["match"]["identity"] == "stream_1"
    assert window.preview.model_for_stream(1) == "spikes"
    assert window.preview.model_for_stream(0) == DEFAULT_MOCK_MODEL

    window.stream_combo.setCurrentIndex(0)
    assert window.model_combo.currentData() == DEFAULT_MOCK_MODEL
    window.close()


def test_designer_stream_type_is_a_scrollable_editable_picker() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()
    application.processEvents()

    combo = window.stream_type_combo
    assert combo.currentText() == "Signals"
    assert combo.isEditable()
    assert combo.count() == len(LSL_STREAM_TYPES) + 1
    assert combo.itemText(0) == "", "an empty first entry clears the match rule"
    assert combo.maxVisibleItems() < combo.count(), "the popup must scroll"

    combo.setCurrentIndex(combo.findText("EEG"))
    application.processEvents()
    assert window.document["streams"][0]["match"]["type"] == "EEG"

    combo.setCurrentText("MyLabCustomType")
    application.processEvents()
    assert window.document["streams"][0]["match"]["type"] == "MyLabCustomType"

    combo.setCurrentIndex(0)
    application.processEvents()
    assert "type" not in window.document["streams"][0]["match"]
    window.close()


def test_designer_uses_one_json_identity_for_lsl_name_and_source_id() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()
    application.processEvents()

    window.stream_identity_edit.setText("shared-stream-id")
    application.processEvents()

    match = window.document["streams"][0]["match"]
    assert match["identity"] == "shared-stream-id"
    assert "name" not in match
    assert "source_id" not in match
    window.close()


def test_designer_adds_a_marker_roll_panel_with_its_own_window() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()

    window.new_view_type.setCurrentText("markers")
    window._add_view()
    window.marker_seconds_spin.setValue(45.0)
    window._refresh_preview()
    application.processEvents()

    view = window.document["streams"][0]["views"][-1]
    assert view["type"] == "markers"
    assert view["marker_seconds"] == 45.0
    assert window.marker_seconds_spin.isEnabled()
    assert len(window.preview.panels) == 3
    roll = window.preview.panels[-1][0]
    assert roll.marker_seconds == 45.0
    assert roll.entries, "the mock preview should roll marker events"
    window.close()


def test_designer_adds_a_spectrogram_panel_for_one_channel() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()

    window.new_view_type.setCurrentText("spectrogram")
    window._add_view()
    window.colormap_combo.setCurrentText("magma")
    window.dynamic_range_spin.setValue(42.0)
    window._refresh_preview()
    application.processEvents()

    view = window.document["streams"][0]["views"][-1]
    assert view["type"] == "spectrogram"
    assert view["channels"] == [0], "a heat map starts on the first channel"
    assert view["colormap"] == "magma"
    assert view["dynamic_range_db"] == 42.0
    assert window.colormap_combo.isEnabled()
    panel = window.preview.panels[-1][0]
    assert isinstance(panel, SpectrogramPanel)
    assert panel.image.image is not None, "the mock preview should fill the heat map"
    window.close()


def test_designer_adds_a_muted_audio_panel() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()

    window.new_view_type.setCurrentText("audio")
    window._add_view()
    window.audio_gain_spin.setValue(2.5)
    window._refresh_preview()
    application.processEvents()

    view = window.document["streams"][0]["views"][-1]
    assert view["type"] == "audio"
    assert view["audio_gain"] == 2.5
    assert "audio_muted" not in view, "muted is the default, so it is not written"
    panel = window.preview.panels[-1][0]
    assert isinstance(panel, AudioPanel)
    assert panel.muted is True, "a preview must not play until it is asked to"
    assert len(panel.meters) == 2

    window.audio_muted_check.setChecked(False)
    application.processEvents()
    assert window.document["streams"][0]["views"][-1]["audio_muted"] is False
    window.close()


def test_view_arguments_are_shown_only_for_the_panel_type_that_uses_them() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()
    application.processEvents()

    window.view_type_combo.setCurrentText("spectrogram")
    application.processEvents()
    form = window.view_arguments_form

    assert form.isRowVisible(window.colormap_combo)
    assert form.isRowVisible(window.fft_spin)
    assert not form.isRowVisible(window.audio_gain_spin)

    window.view_type_combo.setCurrentText("audio")
    application.processEvents()

    assert form.isRowVisible(window.audio_gain_spin)
    assert form.isRowVisible(window.level_seconds_spin)
    assert not form.isRowVisible(window.colormap_combo)
    assert form.isRowVisible(window.status_dot_check), "every panel keeps its dot"
    window.close()


def _lane_point(panel: TracePanel, lane: int) -> QtCore.QPointF:
    """Return the viewport position of a stacked trace lane."""

    scene = panel.plot.plotItem.vb.mapViewToScene(QtCore.QPointF(-1.0, float(lane)))
    return QtCore.QPointF(panel.plot.mapFromScene(scene))


def _drag(widget: QtWidgets.QWidget, start: QtCore.QPointF, end: QtCore.QPointF) -> None:
    application = QtWidgets.QApplication.instance()
    left = QtCore.Qt.MouseButton.LeftButton
    none = QtCore.Qt.MouseButton.NoButton
    modifiers = QtCore.Qt.KeyboardModifier.NoModifier
    for kind, position, button, buttons in (
        (QtCore.QEvent.Type.MouseButtonPress, start, left, left),
        (QtCore.QEvent.Type.MouseMove, end, none, left),
        (QtCore.QEvent.Type.MouseButtonRelease, end, left, none),
    ):
        global_position = QtCore.QPointF(widget.mapToGlobal(position.toPoint()))
        application.sendEvent(
            widget,
            QtGui.QMouseEvent(
                kind, position, global_position, button, buttons, modifiers
            ),
        )


def test_reordered_moves_one_entry_and_ignores_impossible_moves() -> None:
    assert reordered([0, 1, 2, 3], source=0, target=2) == [1, 2, 0, 3]
    assert reordered([0, 1, 2, 3], source=3, target=1) == [0, 3, 1, 2]
    assert reordered([0, 1, 2], source=1, target=1) == [0, 1, 2]
    assert reordered([0, 1, 2], source=1, target=9) == [0, 1, 2]


def test_dragging_a_channel_row_reorders_the_stream_and_its_panels() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()
    application.processEvents()

    window.channel_table.rows_reordered.emit([1, 0])
    window._refresh_preview()
    application.processEvents()

    stream = window.document["streams"][0]
    assert [channel["label"] for channel in stream["channels"]] == [
        "Channel 2",
        "Channel 1",
    ]
    assert window.channel_table.item(0, 1).text() == "Channel 2"
    assert stream["views"][0]["channels"] == [1, 0], "panels follow the channel order"
    assert window.preview.panels[0][0].positions == [0, 1]
    labels = [
        window.view_channels.item(row).text()
        for row in range(window.view_channels.count())
    ]
    assert labels[0].startswith("Channel 2")
    window.close()


def test_channel_table_drop_row_maps_a_pointer_position_to_a_move() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.resize(1400, 900)
    window.show()
    application.processEvents()

    table = window.channel_table
    second = table.visualRect(table.model().index(1, 0))

    assert table._drop_row(second.center() + QtCore.QPoint(0, 2), source=0) == 1
    assert table._drop_row(second.center() - QtCore.QPoint(0, 2), source=0) == 0
    assert table._drop_row(QtCore.QPoint(5, table.viewport().height() - 1), source=0) == 1
    window.close()


def test_dragging_a_preview_lane_reorders_that_panels_channels() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.resize(1400, 900)
    window.show()
    application.processEvents()

    panel = window.preview.panels[0][0]
    assert isinstance(panel, TracePanel)
    assert panel.reorderable, "stacked designer traces accept lane drags"

    _drag(panel.plot.viewport(), _lane_point(panel, 0), _lane_point(panel, 1))
    application.processEvents()

    assert panel.positions == [1, 0]
    assert window.document["streams"][0]["views"][0]["channels"] == [1, 0]
    assert [channel["label"] for channel in window.document["streams"][0]["channels"]] == [
        "Channel 1",
        "Channel 2",
    ], "the stream order is untouched by a per-panel reorder"

    window._refresh_preview()
    application.processEvents()
    assert window.preview.panels[0][0].positions == [1, 0]
    window.close()


def test_overlay_traces_do_not_accept_lane_drags() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    document = new_document()
    document["streams"][0]["views"][0]["alignment"] = "overlay"
    window = DesignerWindow(document)
    window.show()
    application.processEvents()

    assert window.preview.panels[0][0].reorderable is False
    window.close()


def test_reordering_the_panel_channel_list_reorders_the_view() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()
    application.processEvents()

    # An internal move is a take followed by an insert; the drop handler then
    # reports it once the list is whole again.
    window.view_channels.insertItem(1, window.view_channels.takeItem(0))
    window.view_channels.items_reordered.emit()
    window._refresh_preview()
    application.processEvents()

    assert window.document["streams"][0]["views"][0]["channels"] == [1, 0]
    assert window.preview.panels[0][0].positions == [1, 0]
    window.close()


def test_designer_materializes_all_panels_when_one_is_dragged() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window = DesignerWindow(new_document())
    window.show()
    application.processEvents()

    host = window.preview.canvas._hosts[0]
    starting_geometry = host.geometry()
    host._operation = "move"
    host._press_global = QtCore.QPoint(0, 0)
    host._press_geometry = starting_geometry
    host._apply_pointer_delta(QtCore.QPoint(30, 20))
    application.processEvents()

    views = window.document["streams"][0]["views"]
    assert views[0]["layout"]["x"] > 0
    assert "layout" in views[1]
    assert window.custom_layout_check.isChecked()
    window.close()


def test_fit_to_screen_button_evenly_fills_the_existing_window() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    document = new_document()
    views = document["streams"][0]["views"]
    views[0]["layout"] = {"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.3}
    views[1]["layout"] = {"x": 0.3, "y": 0.2, "width": 0.3, "height": 0.3}
    window = DesignerWindow(document)
    window.show()
    application.processEvents()

    window.fit_to_screen_button.click()
    application.processEvents()

    layouts = [view["layout"] for view in window.document["streams"][0]["views"]]
    assert layouts[0] == {"x": 0.0, "y": 0.0, "width": 0.4, "height": 1.0}
    assert layouts[1] == {"x": 0.4, "y": 0.0, "width": 0.6, "height": 1.0}
    assert window.custom_layout_check.isChecked()
    window.close()
