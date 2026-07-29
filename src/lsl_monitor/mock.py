"""Deterministic mock signals used by the visual layout designer."""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from lsl_monitor.config import StreamConfig
from lsl_monitor.model import MarkerEvent, StreamSnapshot

#: A model renders one channel from absolute LSL time, the channel position, and
#: the stream's channel count. The count only matters to models whose channels
#: share one event sequence, such as the marker trigger lines.
MockGenerator = Callable[[np.ndarray, int, int], np.ndarray]

#: A model that depends on nothing but time and its own channel position.
ChannelGenerator = Callable[[np.ndarray, int], np.ndarray]

DEFAULT_MOCK_MODEL = "sine"

MOCK_MARKER_TEXTS = (
    "trial/start",
    "cue/left",
    "stim/on",
    "response",
    "stim/off",
    "cue/right",
    "trial/end",
)
MOCK_MARKER_PERIOD = 1.3
#: Samples per channel a preview snapshot draws at most, whatever window it
#: covers. A long window is thinned to fit rather than cut short.
MOCK_MAX_POINTS = 20_000
# How long a mock trigger line holds its code before returning to zero.
MOCK_MARKER_PULSE = 0.05

# One fixed noise field keeps every model reproducible: a value depends only on
# absolute LSL time and channel position, so a signal keeps its shape while the
# preview window slides over it.
_NOISE_FIELD = np.random.default_rng(20240521).standard_normal((8, 8192))


def _noise(seconds: np.ndarray, position: int, rate: float) -> np.ndarray:
    """Sample the noise field at `rate` values per second, linearly interpolated."""

    row = _NOISE_FIELD[position % _NOISE_FIELD.shape[0]]
    offsets = seconds * rate
    lower = np.floor(offsets).astype(np.int64)
    fraction = offsets - lower
    start = row[lower % row.size]
    end = row[(lower + 1) % row.size]
    return start + (end - start) * fraction


def _sine_model(seconds: np.ndarray, position: int) -> np.ndarray:
    """Slowly modulated sine per channel, spread across nearby frequencies."""

    frequency = 0.8 + position * 1.35
    phase = position * np.pi / 4.0
    carrier = np.sin(2.0 * np.pi * frequency * seconds + phase)
    modulation = 0.22 * np.sin(2.0 * np.pi * 0.13 * seconds + position)
    detail = 0.08 * np.sin(2.0 * np.pi * (frequency * 3.7) * seconds)
    return (1.0 + position * 0.18) * carrier + modulation + detail


def _eeg_model(seconds: np.ndarray, position: int) -> np.ndarray:
    """Waxing alpha over theta and beta, with measurement noise."""

    envelope = 0.55 + 0.45 * np.sin(2.0 * np.pi * 0.11 * seconds + position)
    alpha = envelope * np.sin(2.0 * np.pi * (9.5 + 0.4 * position) * seconds)
    theta = 0.45 * np.sin(2.0 * np.pi * (5.0 + 0.2 * position) * seconds + position)
    beta = 0.12 * np.sin(2.0 * np.pi * (21.0 + position) * seconds)
    return 0.9 * alpha + theta + beta + 0.2 * _noise(seconds, position, 160.0)


def _noise_model(seconds: np.ndarray, position: int) -> np.ndarray:
    """Broadband noise for checking normalization and spectrum floors."""

    return (1.0 + 0.25 * position) * _noise(seconds, position, 180.0)


def _drift_model(seconds: np.ndarray, position: int) -> np.ndarray:
    """Slow baseline wander with a small ripple, useful for stacked lanes."""

    wander = 1.4 * _noise(seconds, position, 0.35)
    ripple = 0.16 * np.sin(2.0 * np.pi * (1.1 + 0.3 * position) * seconds)
    return wander + ripple + 0.05 * _noise(seconds, position, 180.0)


def _spikes_model(seconds: np.ndarray, position: int) -> np.ndarray:
    """Repeating sharp events with fast decay, one rate per channel."""

    period = 0.9 + 0.25 * position
    phase = np.mod(seconds / period + position * 0.3, 1.0)
    return 1.6 * np.exp(-phase / 0.035) - 0.15 + 0.06 * _noise(seconds, position, 180.0)


def _square_model(seconds: np.ndarray, position: int) -> np.ndarray:
    """Alternating levels for reading panel scaling at a glance."""

    frequency = 0.35 + 0.2 * position
    wave = np.sign(np.sin(2.0 * np.pi * frequency * seconds + position))
    return 0.9 * wave + 0.05 * _noise(seconds, position, 180.0)


def _marker_model(seconds: np.ndarray, position: int, channel_count: int) -> np.ndarray:
    """Trigger line that holds one code per mock marker event, then returns to zero.

    The events are shared out over the channels exactly as `make_mock_markers`
    shares them, so a trigger lane, a marker roll, and the markers derived from
    the numbers all describe the same events at the same instants.
    """

    if channel_count <= 0:
        return np.zeros_like(seconds)
    step = np.floor(seconds / MOCK_MARKER_PERIOD)
    codes = np.mod(step, len(MOCK_MARKER_TEXTS)) + 1.0
    mine = np.mod(step, channel_count) == position
    high = mine & (seconds - step * MOCK_MARKER_PERIOD < MOCK_MARKER_PULSE)
    return np.where(high, codes, 0.0)


def _channel_only(model: ChannelGenerator) -> MockGenerator:
    """Adapt a model that does not need to know how many channels there are."""

    def generate(seconds: np.ndarray, position: int, channel_count: int) -> np.ndarray:
        del channel_count
        return model(seconds, position)

    return generate


MOCK_MODELS: dict[str, MockGenerator] = {
    "sine": _channel_only(_sine_model),
    "eeg": _channel_only(_eeg_model),
    "noise": _channel_only(_noise_model),
    "drift": _channel_only(_drift_model),
    "spikes": _channel_only(_spikes_model),
    "square": _channel_only(_square_model),
    "markers": _marker_model,
}

MOCK_MODEL_LABELS: dict[str, str] = {
    "sine": "Sine mix",
    "eeg": "EEG rhythms",
    "noise": "Broadband noise",
    "drift": "Baseline drift",
    "spikes": "Spike train",
    "square": "Square wave",
    "markers": "Marker events",
}


def mock_model_label(model: str) -> str:
    """Return the display name of a mock signal model."""

    return MOCK_MODEL_LABELS.get(model, model)


def make_mock_markers(
    channel_count: int, now_lsl_time: float, marker_seconds: float
) -> tuple[MarkerEvent, ...]:
    """Create evenly spaced mock marker events, cycling over the channels.

    Events are keyed to absolute LSL time, so each one keeps its text and holds
    its place in the roll while the preview window slides over it.
    """

    if channel_count <= 0 or marker_seconds <= 0:
        return ()
    first = int(np.ceil((now_lsl_time - marker_seconds) / MOCK_MARKER_PERIOD))
    last = int(np.floor(now_lsl_time / MOCK_MARKER_PERIOD))
    return tuple(
        MarkerEvent(
            lsl_time=step * MOCK_MARKER_PERIOD,
            position=step % channel_count,
            text=MOCK_MARKER_TEXTS[step % len(MOCK_MARKER_TEXTS)],
        )
        for step in range(first, last + 1)
    )


def make_mock_snapshot(
    stream: StreamConfig,
    now_lsl_time: float,
    history_seconds: float,
    nominal_srate: float = 200.0,
    model: str = DEFAULT_MOCK_MODEL,
    marker_seconds: float | None = None,
) -> StreamSnapshot:
    """Create a lively, deterministic snapshot for a configured mock stream.

    `marker_seconds` extends the mock marker events beyond the sample history
    when a markers panel rolls over a longer window.
    """

    try:
        generator = MOCK_MODELS[model]
    except KeyError as error:
        raise ValueError(f"Unknown mock signal model {model!r}") from error

    point_count = max(32, round(history_seconds * nominal_srate))
    if point_count > MOCK_MAX_POINTS:
        # Thinning changes the rate the samples really arrive at, and spectral
        # panels read that rate off the timestamps, so report it as the nominal
        # one too instead of leaving the two disagreeing.
        point_count = MOCK_MAX_POINTS
        nominal_srate = point_count / history_seconds
    timestamps = np.linspace(
        now_lsl_time - history_seconds,
        now_lsl_time,
        point_count,
        endpoint=True,
    )
    rows = []
    labels = []
    colors = []
    channel_count = len(stream.channels)
    for position, channel in enumerate(stream.channels):
        rows.append(generator(timestamps, position, channel_count))
        labels.append(channel.label or channel.name or f"Ch{channel.index}")
        colors.append(channel.color)

    return StreamSnapshot(
        stream_id=stream.id,
        connected=True,
        active=True,
        message=f"Mock {mock_model_label(model).lower()} · design preview",
        timestamps=timestamps,
        samples=np.asarray(rows, dtype=float),
        channel_labels=tuple(labels),
        channel_colors=tuple(colors),
        nominal_srate=nominal_srate,
        last_sample_lsl_time=now_lsl_time,
        now_lsl_time=now_lsl_time,
        markers=make_mock_markers(
            len(stream.channels),
            now_lsl_time,
            max(history_seconds, marker_seconds or 0.0),
        ),
    )
