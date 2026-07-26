"""Thread-safe data model used by LSL workers and Qt views."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from threading import RLock

import numpy as np

MAX_MARKER_EVENTS = 2048


@dataclass(frozen=True)
class MarkerEvent:
    """One marker sample: its LSL time, selected channel, and text."""

    lsl_time: float
    position: int
    text: str


@dataclass(frozen=True)
class StreamSnapshot:
    """Immutable snapshot of one stream at a point in LSL time."""

    stream_id: str
    connected: bool
    active: bool
    message: str
    timestamps: np.ndarray
    samples: np.ndarray
    channel_labels: tuple[str, ...]
    channel_colors: tuple[str | None, ...]
    nominal_srate: float
    last_sample_lsl_time: float | None
    now_lsl_time: float
    markers: tuple[MarkerEvent, ...] = ()

    @property
    def age_seconds(self) -> float | None:
        if self.last_sample_lsl_time is None:
            return None
        return max(0.0, self.now_lsl_time - self.last_sample_lsl_time)

    def measured_rate_hz(self, window_seconds: float = 2.0) -> float | None:
        """Samples received per second over the most recent window.

        Returns `None` before any sample arrives and drops toward zero while a
        connected stream is silent, so it reads as a live transmission rate
        rather than the nominal rate advertised by the outlet.
        """

        if self.timestamps.size == 0:
            return None
        window = max(window_seconds, 1e-6)
        start = self.now_lsl_time - window
        elapsed = self.now_lsl_time - max(start, float(self.timestamps[0]))
        if elapsed <= 0.0:
            return None
        return float(np.count_nonzero(self.timestamps >= start)) / elapsed


class StreamBuffer:
    """Bounded, sample-synchronous storage shared across threads."""

    def __init__(self, stream_id: str, channel_count: int, max_points: int) -> None:
        self.stream_id = stream_id
        self._lock = RLock()
        self._timestamps: deque[float] = deque(maxlen=max_points)
        self._channels = [deque(maxlen=max_points) for _ in range(channel_count)]
        self._connected = False
        self._message = "Waiting for stream"
        self._labels = tuple(f"Ch{index}" for index in range(channel_count))
        self._colors: tuple[str | None, ...] = tuple(None for _ in range(channel_count))
        self._nominal_srate = 0.0
        self._last_sample_lsl_time: float | None = None
        self._markers: deque[MarkerEvent] = deque(maxlen=MAX_MARKER_EVENTS)

    def reset_channels(
        self,
        labels: list[str],
        colors: list[str | None],
        nominal_srate: float,
        max_points: int,
    ) -> None:
        """Reset data after a new connection and apply resolved channel metadata."""

        with self._lock:
            self._timestamps = deque(maxlen=max_points)
            self._channels = [deque(maxlen=max_points) for _ in labels]
            self._labels = tuple(labels)
            self._colors = tuple(colors)
            self._nominal_srate = nominal_srate
            self._last_sample_lsl_time = None
            self._markers = deque(maxlen=MAX_MARKER_EVENTS)

    def set_connection(self, connected: bool, message: str) -> None:
        with self._lock:
            self._connected = connected
            self._message = message

    def append(self, samples: list[list[float]], timestamps: list[float]) -> None:
        """Append selected samples, discarding malformed rows safely."""

        with self._lock:
            for sample, timestamp in zip(samples, timestamps, strict=False):
                if len(sample) != len(self._channels):
                    continue
                self._timestamps.append(float(timestamp))
                for channel, value in zip(self._channels, sample, strict=True):
                    channel.append(float(value))
                self._last_sample_lsl_time = float(timestamp)

    def append_markers(self, entries: list[list[str]], timestamps: list[float]) -> None:
        """Append string marker samples, keeping the numeric arrays aligned.

        Marker streams carry no numbers, so each event stores a not-a-number
        placeholder per channel. Timestamps stay sample-synchronous, which keeps
        activity and transmission rate readings identical to numeric streams.
        """

        with self._lock:
            for entry, timestamp in zip(entries, timestamps, strict=False):
                if len(entry) != len(self._channels):
                    continue
                moment = float(timestamp)
                self._timestamps.append(moment)
                for channel in self._channels:
                    channel.append(float("nan"))
                for position, text in enumerate(entry):
                    if text:
                        self._markers.append(MarkerEvent(moment, position, text))
                self._last_sample_lsl_time = moment

    def snapshot(self, now_lsl_time: float, inactive_after: float) -> StreamSnapshot:
        with self._lock:
            last = self._last_sample_lsl_time
            active = self._connected and last is not None and now_lsl_time - last <= inactive_after
            if self._channels and self._timestamps:
                samples = np.asarray([list(channel) for channel in self._channels], dtype=float)
            else:
                samples = np.empty((len(self._channels), 0), dtype=float)
            return StreamSnapshot(
                stream_id=self.stream_id,
                connected=self._connected,
                active=active,
                message=self._message,
                timestamps=np.asarray(self._timestamps, dtype=float),
                samples=samples,
                channel_labels=self._labels,
                channel_colors=self._colors,
                nominal_srate=self._nominal_srate,
                last_sample_lsl_time=last,
                now_lsl_time=now_lsl_time,
                markers=tuple(self._markers),
            )

