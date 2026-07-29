import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtMultimedia, QtWidgets

from lsl_monitor.audio import AudioOutput
from lsl_monitor.config import ViewConfig
from lsl_monitor.model import MarkerEvent, StreamSnapshot
from lsl_monitor.views import (
    AlivePanel,
    AudioPanel,
    MarkerPanel,
    PlanePanel,
    PsdPanel,
    SpectrogramPanel,
    TracePanel,
    create_panel,
    marker_entries,
    spectrogram_colormap,
    spectrogram_decibels,
)


def make_snapshot(
    markers: tuple[MarkerEvent, ...] = (), now: float = 100.0
) -> StreamSnapshot:
    timestamps = np.linspace(now - 10.0, now, 1001)
    phase = timestamps - (now - 10.0)
    samples = np.vstack((np.sin(2 * np.pi * 5 * phase), np.cos(2 * np.pi * 5 * phase)))
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
        last_sample_lsl_time=now,
        now_lsl_time=now,
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
        SpectrogramPanel(
            "spectrogram", ViewConfig(type="spectrogram", fft_size=256), [0], 10.0
        ),
        AudioPanel(
            "audio",
            ViewConfig(type="audio"),
            [0, 1],
            output=AudioOutput(device=QtMultimedia.QAudioDevice()),
        ),
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
    assert panels[5].image.image is not None
    assert len(panels[6].meters) == 2
    for panel in panels:
        panel.close()


def test_every_panel_names_its_stream_beside_the_title() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    for view_type in (
        "traces",
        "plane_2d",
        "psd",
        "spectrogram",
        "audio",
        "markers",
        "alive",
    ):
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


def test_spectrogram_columns_find_a_tone_and_end_at_the_newest_sample() -> None:
    rate = 500.0
    seconds = np.arange(0.0, 4.0, 1.0 / rate)
    tone = np.sin(2.0 * np.pi * 60.0 * seconds)

    decibels, frequencies, times = spectrogram_decibels(tone, rate, fft_size=256)

    assert decibels.shape == (times.size, frequencies.size)
    assert times.size > 1 and np.all(np.diff(times) > 0)
    assert times[-1] < 0.0, "columns are aged relative to the newest sample"
    # The columns cover the block end to end: the first is centered half a
    # window after its oldest sample and the last half a window before the
    # newest one, so nothing is left over against either edge of a plot.
    half_window = 128.0 / rate
    span = (tone.size - 1) / rate
    assert times[0] == pytest.approx(half_window - span, abs=2.0 / rate)
    assert times[-1] == pytest.approx(-half_window, abs=2.0 / rate)
    loudest = frequencies[np.argmax(decibels, axis=1)]
    assert loudest == pytest.approx(60.0, abs=rate / 256)
    assert frequencies[-1] == pytest.approx(rate / 2.0)


def test_spectrogram_bounds_its_transform_count_and_needs_a_full_window() -> None:
    values = np.random.default_rng(7).standard_normal(200_000)

    decibels, _, times = spectrogram_decibels(values, 44100.0, fft_size=1024)

    assert times.size <= 400, "a fast stream must not pay one transform per sample"
    assert decibels.shape[0] == times.size
    assert spectrogram_decibels(np.zeros(8), 500.0, 256)[0].size == 0
    assert spectrogram_decibels(np.zeros(500), 0.0, 256)[0].size == 0


def test_spectrogram_image_fills_the_time_window_it_is_configured_with() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    view = ViewConfig(type="spectrogram", fft_size=256, spectrogram_seconds=8.0)
    panel = SpectrogramPanel("spectrogram", view, [0], 10.0)

    panel.update_snapshot(make_snapshot())
    application.processEvents()

    drawn = panel.image.mapRectToView(panel.image.boundingRect())
    assert panel.plot.viewRange()[0] == pytest.approx([-8.0, 0.0], abs=0.01)
    assert drawn.left() == pytest.approx(-8.0, abs=0.02)
    assert drawn.right() == pytest.approx(0.0, abs=0.02)
    panel.close()


def test_spectrogram_draws_only_the_history_it_has_while_a_window_fills_up() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # The snapshot carries 10 seconds, a third of the configured window.
    view = ViewConfig(type="spectrogram", fft_size=256, spectrogram_seconds=30.0)
    panel = SpectrogramPanel("spectrogram", view, [0], 10.0)

    panel.update_snapshot(make_snapshot())
    application.processEvents()

    drawn = panel.image.mapRectToView(panel.image.boundingRect())
    assert panel.plot.viewRange()[0] == pytest.approx([-30.0, 0.0], abs=0.01)
    assert drawn.left() == pytest.approx(-10.0, abs=0.02)
    assert drawn.right() == pytest.approx(0.0, abs=0.02)
    panel.close()


def test_spectrogram_colormap_falls_back_to_the_default_on_an_unknown_name() -> None:
    assert spectrogram_colormap("magma") is not None
    assert spectrogram_colormap("not-a-colormap") is not None


def test_frequency_range_narrows_a_live_panel_and_is_given_back_to_the_stream() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    psd = PsdPanel("psd", ViewConfig(type="psd", fft_size=256), [0, 1])
    spectrogram = SpectrogramPanel(
        "spectrogram", ViewConfig(type="spectrogram", fft_size=256), [0], 10.0
    )
    for panel in (psd, spectrogram):
        panel.update_snapshot(make_snapshot())
    application.processEvents()

    # The 100 Hz test stream reaches 50 Hz, which both panels start out showing.
    assert psd.frequency_control.limits is None
    assert psd.frequency_control.high.value() == pytest.approx(50.0, abs=0.1)
    assert psd.plot.viewRange()[0][1] == pytest.approx(50.0, abs=0.1)
    assert spectrogram.plot.viewRange()[1][1] == pytest.approx(50.0, abs=0.1)

    for panel in (psd, spectrogram):
        panel.frequency_control.high.setValue(12.0)
        panel.update_snapshot(make_snapshot())
    application.processEvents()

    assert psd.frequency_control.limits == (0.0, 12.0)
    assert psd.plot.viewRange()[0] == pytest.approx([0.0, 12.0], abs=0.01)
    assert spectrogram.plot.viewRange()[1] == pytest.approx([0.0, 12.0], abs=0.01)

    for panel in (psd, spectrogram):
        panel.frequency_control.full_button.click()
        panel.update_snapshot(make_snapshot())
    application.processEvents()

    assert psd.frequency_control.limits is None
    assert psd.plot.viewRange()[0][1] == pytest.approx(50.0, abs=0.1)
    assert spectrogram.plot.viewRange()[1][1] == pytest.approx(50.0, abs=0.1)
    for panel in (psd, spectrogram):
        panel.close()


def test_configured_frequency_range_is_the_starting_point_of_the_control() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    view = ViewConfig(type="psd", fft_size=256, frequency_range=(8.0, 30.0))

    panel = PsdPanel("psd", view, [0])
    panel.update_snapshot(make_snapshot())

    assert panel.frequency_control.limits == (8.0, 30.0)
    assert panel.plot.viewRange()[0] == pytest.approx([8.0, 30.0], abs=0.01)
    panel.close()


def test_a_band_wider_than_the_stream_snaps_back_to_what_it_carries() -> None:
    QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    view = ViewConfig(type="psd", fft_size=256, frequency_range=(0.0, 8000.0))

    panel = PsdPanel("psd", view, [0])
    panel.update_snapshot(make_snapshot())

    # The 100 Hz test stream stops at 50 Hz, and the control has to say so
    # instead of leaving 8000 Hz on screen next to an unchanged axis.
    assert panel.frequency_control.limits == pytest.approx((0.0, 50.0), abs=0.1)
    assert panel.frequency_control.high.value() == pytest.approx(50.0, abs=0.1)
    assert panel.frequency_control.high.maximum() == pytest.approx(50.0, abs=0.1)
    assert panel.plot.viewRange()[0][1] == pytest.approx(50.0, abs=0.1)
    panel.close()


def test_audio_panel_meters_every_channel_and_plays_one_of_them() -> None:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    panel = AudioPanel(
        "audio",
        ViewConfig(type="audio", audio_gain=2.0, level_seconds=1.0),
        [0, 1],
        output=AudioOutput(device=QtMultimedia.QAudioDevice()),
    )

    panel.update_snapshot(make_snapshot())
    panel.resize(400, 200)
    panel.show()
    application.processEvents()

    assert [name.text() for name in panel.channel_names] == ["Left", "Right"]
    assert panel.channel_combo.count() == 2
    assert panel.muted is True, "a panel must not make a sound before it is asked to"
    assert panel.played_until == 100.0, "playback starts at the live edge"
    assert panel.gain_label.text() == "+6 dB"
    # A full-scale sine at twice the gain reads as +3 dB RMS and +6 dB peak.
    assert panel.meters[0].level_db == pytest.approx(3.0, abs=0.1)
    assert panel.meters[0].peak_db == pytest.approx(6.0, abs=0.1)
    assert "metering only" in panel.status.text()

    panel.listen_check.setChecked(True)
    panel.update_snapshot(make_snapshot(now=100.5))
    application.processEvents()

    assert panel.played_until == 100.5
    assert panel.output.dropped_samples > 0, "no device means the samples go nowhere"
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
