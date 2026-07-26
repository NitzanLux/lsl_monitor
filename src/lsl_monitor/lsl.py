"""LSL discovery, connection, reconnection, and timestamp-aware buffering."""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from typing import Any

from lsl_monitor.config import ConfigError, StreamConfig, resolve_channel_indices
from lsl_monitor.model import StreamBuffer, StreamSnapshot


def read_channel_labels(info: Any, channel_count: int) -> list[str]:
    """Read LSL channel labels with deterministic fallbacks."""

    labels: list[str] = []
    try:
        channel = info.desc().child("channels").child("channel")
        for index in range(channel_count):
            label = channel.child_value("label")
            labels.append(label or f"Ch{index}")
            channel = channel.next_sibling()
    except Exception:
        labels = []
    labels.extend(f"Ch{index}" for index in range(len(labels), channel_count))
    return labels


def is_marker_stream(info: Any, string_format: int = 3) -> bool:
    """Return whether a stream carries string markers instead of numbers.

    `string_format` is `pylsl.cf_string`. Streams whose info does not report a
    channel format are treated as numeric.
    """

    try:
        return int(info.channel_format()) == int(string_format)
    except Exception:
        return False


def stream_matches(info: Any, rules: dict[str, str]) -> bool:
    """Return whether StreamInfo matches every configured rule."""

    values = {
        "name": info.name(),
        "type": info.type(),
        "source_id": info.source_id(),
    }
    for key in ("name", "type", "source_id"):
        if key in rules and values[key] != rules[key]:
            return False
    pattern = rules.get("name_regex")
    return pattern is None or re.search(pattern, values["name"]) is not None


class LSLStreamWorker:
    """Background consumer for one configured LSL stream."""

    def __init__(
        self,
        config: StreamConfig,
        history_seconds: float,
        inactive_after: float,
        max_points: int,
        *,
        pylsl_module: Any | None = None,
    ) -> None:
        self.config = config
        self.history_seconds = history_seconds
        self.inactive_after = inactive_after
        self.max_points = max_points
        self._pylsl = pylsl_module
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._raw_indices: list[int] = []
        self._markers = False
        self.buffer = StreamBuffer(config.id, len(config.channels), max_points)

    def _module(self) -> Any:
        if self._pylsl is None:
            import pylsl

            self._pylsl = pylsl
        return self._pylsl

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name=f"lsl-{self.config.id}", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def snapshot(self) -> StreamSnapshot:
        now = float(self._module().local_clock())
        return self.buffer.snapshot(now, self.inactive_after)

    def _find_stream(self) -> Any | None:
        streams = self._module().resolve_streams(wait_time=0.25)
        return next((info for info in streams if stream_matches(info, self.config.match)), None)

    def _connect(self, info: Any) -> Any:
        pylsl = self._module()
        channel_count = int(info.channel_count())
        metadata_labels = read_channel_labels(info, channel_count)
        indices, labels, colors = resolve_channel_indices(
            self.config.channels, metadata_labels, channel_count
        )
        nominal_srate = float(info.nominal_srate())
        expected_points = (
            int(max(1.0, nominal_srate) * self.history_seconds * 1.5)
            if nominal_srate > 0
            else self.max_points
        )
        capacity = min(self.max_points, max(512, expected_points))
        self._raw_indices = indices
        self._markers = is_marker_stream(info, int(getattr(pylsl, "cf_string", 3)))
        self.buffer.reset_channels(labels, colors, nominal_srate, capacity)
        inlet = pylsl.StreamInlet(info, max_buflen=max(1, int(self.history_seconds * 2)))
        inlet.open_stream(timeout=1.0)
        self.buffer.set_connection(True, f"Connected to {info.name()}")
        return inlet

    def _run(self) -> None:
        inlet = None
        correction = 0.0
        correction_due = 0.0
        while not self._stop.is_set():
            try:
                if inlet is None:
                    self.buffer.set_connection(False, "Searching for matching LSL stream")
                    info = self._find_stream()
                    if info is None:
                        continue
                    inlet = self._connect(info)
                    correction_due = 0.0

                now = float(self._module().local_clock())
                if now >= correction_due:
                    correction = float(inlet.time_correction(timeout=0.2))
                    correction_due = now + 5.0
                chunk, timestamps = inlet.pull_chunk(timeout=0.1, max_samples=1024)
                if timestamps:
                    corrected = [float(timestamp) + correction for timestamp in timestamps]
                    if self._markers:
                        entries = [
                            [str(sample[index]).strip() for index in self._raw_indices]
                            for sample in chunk
                        ]
                        self.buffer.append_markers(entries, corrected)
                    else:
                        selected = [
                            [sample[index] for index in self._raw_indices]
                            for sample in chunk
                        ]
                        self.buffer.append(selected, corrected)
            except ConfigError as error:
                self.buffer.set_connection(False, str(error))
                self._stop.wait(1.0)
                inlet = None
            except Exception as error:
                self.buffer.set_connection(False, f"Disconnected: {error}")
                try:
                    if inlet is not None:
                        inlet.close_stream()
                except Exception:
                    pass
                inlet = None
                self._stop.wait(0.5)
        try:
            if inlet is not None:
                inlet.close_stream()
        except Exception:
            pass
        self.buffer.set_connection(False, "Stopped")


class StreamManager:
    """Own and expose all configured stream workers."""

    def __init__(
        self,
        workers: list[LSLStreamWorker],
        clock: Callable[[], float],
    ) -> None:
        self.workers = workers
        self.clock = clock

    def start(self) -> None:
        for worker in self.workers:
            worker.start()

    def stop(self) -> None:
        for worker in self.workers:
            worker.stop()

