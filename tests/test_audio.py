import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6 import QtMultimedia

from lsl_monitor.audio import (
    SILENCE_DECIBELS,
    AudioOutput,
    decibels,
    level_decibels,
    resampled,
    samples_after,
)


def test_playback_starts_at_the_live_edge_then_follows_new_samples() -> None:
    timestamps = np.linspace(90.0, 100.0, 11)
    values = np.arange(11.0)

    first, mark = samples_after(timestamps, values, None)

    assert first.size == 0, "the stored history must not be dumped into the sound card"
    assert mark == 100.0

    later_timestamps = np.linspace(95.0, 105.0, 11)
    fresh, mark = samples_after(later_timestamps, values, mark)

    assert fresh.tolist() == [6.0, 7.0, 8.0, 9.0, 10.0]
    assert mark == 105.0


def test_no_new_samples_keeps_the_mark_while_a_stream_is_silent() -> None:
    timestamps = np.linspace(90.0, 100.0, 11)

    fresh, mark = samples_after(timestamps, np.zeros(11), 100.0)

    assert fresh.size == 0
    assert mark == 100.0

    empty, unchanged = samples_after(np.empty(0), np.empty(0), 100.0)

    assert empty.size == 0
    assert unchanged == 100.0


def test_levels_report_rms_and_peak_and_ignore_placeholders() -> None:
    rms, peak = level_decibels(np.array([0.5, -0.5, np.nan]))

    assert rms == pytest.approx(-6.02, abs=0.01)
    assert peak == pytest.approx(-6.02, abs=0.01)
    assert level_decibels(np.array([np.nan, np.inf])) == (
        SILENCE_DECIBELS,
        SILENCE_DECIBELS,
    )
    assert decibels(0.0) == SILENCE_DECIBELS
    assert decibels(1.0) == pytest.approx(0.0)


def test_resampling_keeps_the_block_duration_and_its_frequency() -> None:
    seconds = np.linspace(0.0, 1.0, 200, endpoint=False)
    tone = np.sin(2.0 * np.pi * 5.0 * seconds)

    upsampled = resampled(tone, 200.0, 48000.0)

    assert upsampled.size == 48000, "one second of samples stays one second long"
    assert upsampled.max() == pytest.approx(1.0, abs=0.01)
    crossings = np.count_nonzero(np.diff(np.signbit(upsampled)))
    assert crossings == np.count_nonzero(np.diff(np.signbit(tone))), "5 Hz stays 5 Hz"
    assert resampled(tone, 200.0, 200.0).tolist() == tone.tolist()
    assert resampled(np.empty(0), 200.0, 48000.0).size == 0
    assert resampled(tone, 0.0, 48000.0).size == 0


def test_output_without_a_device_drops_samples_instead_of_failing() -> None:
    output = AudioOutput(device=QtMultimedia.QAudioDevice())

    assert output.available is False
    assert output.description == "no audio output device"
    assert output.open(44100.0) is False
    assert output.output_rate == 0
    assert output.write(np.zeros(128), 44100.0) == 128
    assert output.dropped_samples == 128
    assert output.write(np.empty(0), 44100.0) == 0, "an empty block is not a drop"
    output.close()
