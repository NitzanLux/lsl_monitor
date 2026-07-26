import numpy as np
import pytest

from lsl_monitor.model import StreamBuffer


def test_buffer_keeps_samples_and_uses_lsl_time_for_activity() -> None:
    buffer = StreamBuffer("eeg", channel_count=2, max_points=3)
    buffer.reset_channels(["Fp1", "Fp2"], [None, "#ffffff"], 250.0, max_points=3)
    buffer.set_connection(True, "Connected")
    buffer.append(
        [[1.0, 10.0], [2.0, 20.0], [3.0, 30.0], [4.0, 40.0]],
        [100.0, 101.0, 102.0, 103.0],
    )

    recent = buffer.snapshot(now_lsl_time=104.0, inactive_after=2.0)
    stale = buffer.snapshot(now_lsl_time=106.1, inactive_after=2.0)

    assert recent.active is True
    assert stale.active is False
    assert recent.age_seconds == 1.0
    np.testing.assert_array_equal(recent.timestamps, [101.0, 102.0, 103.0])
    np.testing.assert_array_equal(recent.samples, [[2.0, 3.0, 4.0], [20.0, 30.0, 40.0]])


def test_measured_rate_follows_recent_arrivals_and_falls_to_zero_when_silent() -> None:
    buffer = StreamBuffer("eeg", channel_count=1, max_points=1000)
    buffer.set_connection(True, "Connected")
    buffer.append(
        [[float(step)] for step in range(400)],
        [100.0 + step * 0.01 for step in range(400)],
    )

    live = buffer.snapshot(now_lsl_time=104.0, inactive_after=2.0)
    silent = buffer.snapshot(now_lsl_time=110.0, inactive_after=2.0)

    assert live.measured_rate_hz(2.0) == pytest.approx(100.0, abs=1.0)
    assert silent.measured_rate_hz(2.0) == 0.0


def test_marker_samples_keep_timestamps_aligned_and_record_text() -> None:
    buffer = StreamBuffer("events", channel_count=1, max_points=10)
    buffer.reset_channels(["Event"], [None], nominal_srate=0.0, max_points=10)
    buffer.set_connection(True, "Connected")
    buffer.append_markers([["cue/left"], [""], ["response"]], [10.0, 10.5, 11.0])

    snapshot = buffer.snapshot(now_lsl_time=11.5, inactive_after=2.0)

    assert [(event.lsl_time, event.text) for event in snapshot.markers] == [
        (10.0, "cue/left"),
        (11.0, "response"),
    ]
    assert snapshot.timestamps.tolist() == [10.0, 10.5, 11.0]
    assert np.isnan(snapshot.samples).all()
    assert snapshot.active is True


def test_disconnected_stream_is_never_active() -> None:
    buffer = StreamBuffer("eeg", channel_count=1, max_points=10)
    buffer.append([[1.0]], [10.0])

    snapshot = buffer.snapshot(now_lsl_time=10.1, inactive_after=2.0)

    assert snapshot.connected is False
    assert snapshot.active is False

