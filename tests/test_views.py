import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtWidgets

from lsl_monitor.config import ViewConfig
from lsl_monitor.model import MarkerEvent, StreamSnapshot
from lsl_monitor.views import (
    AlivePanel,
    MarkerPanel,
    PlanePanel,
    PsdPanel,
    TracePanel,
    create_panel,
    marker_entries,
)


def make_snapshot(markers: tuple[MarkerEvent, ...] = ()) -> StreamSnapshot:
    timestamps = np.linspace(90.0, 100.0, 1001)
    samples = np.vstack(
        (
            np.sin(2 * np.pi * 5 * (timestamps - 90.0)),
            np.cos(2 * np.pi * 5 * (timestamps - 90.0)),
        )
    )
    return StreamSnapshot(
        stream_id="emg",
        connected=True,
        active=True,
        message="Connected",
        timestamps=timestamps,
        samples=samples,
        channel_labels=("Left", "Right"),
        channel_colors=("#5eead4", "#60a5fa"),
        nominal_srate=100.0,
        last_sample_lsl_time=100.0,
        now_lsl_time=100.0,
        markers=markers,
    )


MARKERS = (
    MarkerEvent(lsl_time=94.0, position=0, text="cue/left"),
    MarkerEvent(lsl_time=96.5, position=1, text="response"),
    MarkerEvent(lsl_time=99.0, position=0, text="cue/right"),
    MarkerEvent(lsl_time=80.0, position=0, text="too old"),
)


def test_all_panel_types_render_a_snapshot() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    snapshot = make_snapshot()
    panels = [
        TracePanel("traces", ViewConfig(type="traces"), [0, 1], 10.0),
        PlanePanel("plane", ViewConfig(type="plane_2d"), [0, 1], 10.0),
        PsdPanel("psd", ViewConfig(type="psd", fft_size=256), [0, 1]),
        AlivePanel("alive", ViewConfig(type="alive")),
        MarkerPanel("markers", ViewConfig(type="markers"), [0, 1], 10.0),
    ]

    for panel in panels:
        panel.update_snapshot(snapshot)
        panel.resize(500, 300)
        panel.show()
    application.processEvents()

    assert len(panels[0].curves) == 2
    assert panels[1].curve is not None
    assert len(panels[1].trail_curves) == panels[1].TRAIL_SEGMENTS
    assert (
        panels[1].trail_curves[0].opts["pen"].color().alpha()
        < panels[1].trail_curves[-1].opts["pen"].color().alpha()
    )
    assert len(panels[2].curves[0].xData) > 0
    assert panels[3].indicator.text() == "ACTIVE"
    for panel in panels:
        panel.close()


def test_every_panel_names_its_stream_beside_the_title() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    for view_type in ("traces", "plane_2d", "psd", "markers", "alive"):
        panel = create_panel(
            "Custom title",
            ViewConfig(type=view_type),
            [0, 1],
            10.0,
            stream_id="emg",
        )

        assert panel.title_label.text() == "Custom title"
        assert panel.stream_label.text() == "emg"
        assert panel.stream_label.isVisibleTo(panel)
        panel.close()


def test_panel_hides_the_stream_label_when_no_stream_is_named() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    panel = create_panel("traces", ViewConfig(type="traces"), [0, 1], 10.0)

    assert panel.stream_label.text() == ""
    assert not panel.stream_label.isVisibleTo(panel)
    panel.close()


def test_alive_panel_reports_the_measured_transmission_rate() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = AlivePanel("alive", ViewConfig(type="alive"))

    panel.update_snapshot(make_snapshot())
    application.processEvents()

    assert "100 Hz nominal" in panel.rate.text()
    assert "2 ch" in panel.rate.text()
    assert float(panel.rate.text().split(" samples/s")[0]) == pytest.approx(100.5, abs=1.0)
    panel.close()


def test_marker_roll_orders_newest_first_and_drops_events_beyond_the_window() -> None:
    snapshot = make_snapshot(MARKERS)

    entries = marker_entries(snapshot, [0, 1], seconds=10.0)

    assert [text for _, _, text in entries] == ["cue/right", "response", "cue/left"]
    assert [round(age, 3) for age, _, _ in entries] == [1.0, 3.5, 6.0]
    assert marker_entries(snapshot, [1], seconds=10.0) == [(3.5, 1, "response")]


def test_marker_roll_positions_events_by_age_and_fades_older_text() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = MarkerPanel("markers", ViewConfig(type="markers"), [0, 1], 10.0)

    panel.update_snapshot(make_snapshot(MARKERS))
    panel.resize(400, 500)
    panel.show()
    application.processEvents()

    ages = [item.pos().y() for item in panel.texts]
    assert ages == pytest.approx([1.0, 3.5, 6.0])
    assert panel.texts[0].toPlainText() == "Left · cue/right"
    assert panel.texts[0].color.alpha() > panel.texts[2].color.alpha()
    assert "3 markers in the last 10 s" in panel.summary.text()
    panel.close()


def test_marker_roll_derives_events_from_a_trigger_channel() -> None:
    timestamps = np.linspace(95.0, 100.0, 501)
    trigger = np.where((timestamps % 2.0) < 0.1, 5.0, 0.0)
    snapshot = StreamSnapshot(
        stream_id="triggers",
        connected=True,
        active=True,
        message="Connected",
        timestamps=timestamps,
        samples=np.vstack((trigger, np.sin(timestamps * 40.0))),
        channel_labels=("Trigger", "Continuous"),
        channel_colors=("#f472b6", "#60a5fa"),
        nominal_srate=100.0,
        last_sample_lsl_time=100.0,
        now_lsl_time=100.0,
    )

    entries = marker_entries(snapshot, [0, 1], seconds=5.0)

    assert entries and all(text == "5" for _, position, text in entries if position == 0)
    assert all(position == 0 for _, position, _ in entries), "continuous channel flooded"
