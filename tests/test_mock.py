import numpy as np
import pytest

from lsl_monitor.config import ChannelConfig, StreamConfig, ViewConfig
from lsl_monitor.mock import (
    DEFAULT_MOCK_MODEL,
    MOCK_MAX_POINTS,
    MOCK_MODEL_LABELS,
    MOCK_MODELS,
    make_mock_snapshot,
)


def sample_stream() -> StreamConfig:
    return StreamConfig(
        id="mock",
        match={"type": "Signals"},
        channels=(
            ChannelConfig(index=0, label="First"),
            ChannelConfig(index=1, label="Second"),
        ),
        views=(ViewConfig(type="traces"),),
    )


def test_every_model_produces_finite_varying_channels() -> None:
    stream = sample_stream()

    for model in MOCK_MODELS:
        snapshot = make_mock_snapshot(stream, 1000.0, 4.0, model=model)

        assert snapshot.samples.shape == (2, snapshot.timestamps.size)
        assert np.isfinite(snapshot.samples).all()
        assert snapshot.samples.std(axis=1).min() > 0.0
        assert MOCK_MODEL_LABELS[model].lower() in snapshot.message


def test_a_long_window_is_thinned_and_reports_the_rate_it_is_drawn_at() -> None:
    snapshot = make_mock_snapshot(sample_stream(), 1000.0, 600.0, nominal_srate=500.0)

    assert snapshot.timestamps.size == MOCK_MAX_POINTS, "the window is covered, not cut"
    assert snapshot.timestamps[0] == pytest.approx(400.0)
    # Spectral panels read the rate off the timestamps, so the reported nominal
    # rate has to be the thinned one.
    measured = 1.0 / float(np.median(np.diff(snapshot.timestamps)))
    assert snapshot.nominal_srate == pytest.approx(MOCK_MAX_POINTS / 600.0)
    assert measured == pytest.approx(snapshot.nominal_srate, rel=1e-3)


def test_models_are_deterministic() -> None:
    stream = sample_stream()

    first = make_mock_snapshot(stream, 1000.0, 4.0, model="drift")
    repeated = make_mock_snapshot(stream, 1000.0, 4.0, model="drift")

    assert np.array_equal(first.samples, repeated.samples)


def test_signals_are_anchored_to_absolute_time_so_the_window_slides() -> None:
    stream = sample_stream()
    first = make_mock_snapshot(stream, 1000.0, 4.0)
    later = make_mock_snapshot(stream, 1002.0, 4.0)

    shared = first.timestamps >= later.timestamps[0]
    resampled = np.interp(first.timestamps[shared], later.timestamps, later.samples[0])

    assert shared.any()
    assert np.allclose(first.samples[0][shared], resampled, atol=1e-3)


def test_models_are_distinguishable_from_the_default() -> None:
    stream = sample_stream()
    reference = make_mock_snapshot(stream, 1000.0, 4.0, model=DEFAULT_MOCK_MODEL)

    for model in MOCK_MODELS:
        if model == DEFAULT_MOCK_MODEL:
            continue
        other = make_mock_snapshot(stream, 1000.0, 4.0, model=model)
        assert not np.allclose(reference.samples, other.samples)


def test_unknown_model_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown mock signal model"):
        make_mock_snapshot(sample_stream(), 1000.0, 4.0, model="triangle")
