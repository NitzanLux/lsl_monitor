"""LSL discovery, connection, reconnection, and timestamp-aware buffering."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable
from difflib import SequenceMatcher
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
        "hostname": stream_hostname(info),
    }
    for key in ("name", "type", "source_id", "hostname"):
        if key in rules and values[key] != rules[key]:
            return False
    identity = rules.get("identity")
    if identity is not None and (
        values["name"] != identity or values["source_id"] != identity
    ):
        return False
    pattern = rules.get("name_regex")
    return pattern is None or re.search(pattern, values["name"]) is not None


def stream_hostname(info: Any) -> str:
    """Return an outlet hostname, tolerating incomplete legacy metadata."""

    try:
        return str(info.hostname())
    except Exception:
        return ""


def full_stream_name(info: Any) -> str:
    """Return the complete outlet identity shown in panel title rows."""

    name = str(info.name())
    hostname = stream_hostname(info)
    return f"{name} @ {hostname}" if hostname else name


def _normalized_identity(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def _identity_similarity(expected: str, actual: str) -> float:
    expected_key = _normalized_identity(expected)
    actual_key = _normalized_identity(actual)
    if expected_key == actual_key:
        return 1.0
    if expected_key and actual_key.startswith(expected_key):
        return 0.95
    return SequenceMatcher(None, expected_key, actual_key).ratio()


def select_stream(streams: list[Any], rules: dict[str, str]) -> Any | None:
    """Select the closest outlet, requiring hostname disambiguation for a tie."""

    closest = closest_streams(streams, rules)
    if not closest:
        return None
    if len(closest) > 1:
        names = ", ".join(full_stream_name(info) for info in closest)
        raise ConfigError(
            f"Multiple equally close LSL streams match ({names}); "
            "choose one for this run"
        )
    return closest[0]


def closest_streams(streams: list[Any], rules: dict[str, str]) -> list[Any]:
    """Return all outlets tied for the best exact or suffix match."""

    exact = [info for info in streams if stream_matches(info, rules)]
    if exact:
        return exact

    candidates: list[tuple[float, Any]] = []
    for info in streams:
        if "type" in rules and info.type() != rules["type"]:
            continue
        if "hostname" in rules and stream_hostname(info) != rules["hostname"]:
            continue
        pattern = rules.get("name_regex")
        if pattern is not None and re.search(pattern, info.name()) is None:
            continue
        similarities = [
            _identity_similarity(rules[key], str(getattr(info, key)()))
            for key in ("name", "source_id")
            if key in rules
        ]
        if "identity" in rules:
            similarities.extend(
                _identity_similarity(rules["identity"], str(getattr(info, key)()))
                for key in ("name", "source_id")
            )
        if similarities and min(similarities) < 0.6:
            continue
        candidates.append((sum(similarities), info))

    if not candidates:
        return []
    candidates.sort(key=lambda item: item[0], reverse=True)
    best_score = candidates[0][0]
    return [info for score, info in candidates if abs(score - best_score) < 1e-9]


def _stream_uid(info: Any) -> str:
    try:
        uid = str(info.uid())
    except Exception:
        uid = ""
    if uid:
        return uid
    return "\x1f".join(
        (str(info.name()), str(info.type()), str(info.source_id()), stream_hostname(info))
    )


def _stream_identity(info: Any) -> tuple[str, str, str, str]:
    return (
        str(info.name()),
        str(info.type()),
        str(info.source_id()),
        stream_hostname(info),
    )


def _choice_label(info: Any) -> str:
    source_id = str(info.source_id())
    label = full_stream_name(info)
    if source_id:
        label += f" · source_id={source_id}"
    uid = _stream_uid(info)
    return f"{label} · uid={uid[:8]}" if uid else label


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
        self.display_name = config.id
        self._selection_lock = threading.Lock()
        self._selection_candidates: dict[str, tuple[str, tuple[str, str, str, str]]] = {}
        self._preferred_uid: str | None = None
        self._preferred_identity: tuple[str, str, str, str] | None = None
        self._failed_streams: dict[str, tuple[int, float]] = {}
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

    def selection_options(self) -> tuple[tuple[str, str], ...]:
        """Return runtime choices needed to disambiguate this configured stream."""

        with self._selection_lock:
            return tuple(
                (choice_id, label)
                for choice_id, (label, _) in self._selection_candidates.items()
            )

    def choose_stream(self, choice_id: str) -> None:
        """Attach this worker to a choice made in the runtime GUI."""

        with self._selection_lock:
            choice = self._selection_candidates.get(choice_id)
            if choice is None:
                return
            _, identity = choice
            self._preferred_uid = choice_id
            self._preferred_identity = identity
            self._selection_candidates = {}

    def _find_stream(self) -> Any | None:
        # liblsl documents that waits below 0.5 seconds may return only a
        # subset. A full second is important when several outlets start
        # together, as in the experiment demo.
        streams = self._module().resolve_streams(wait_time=1.0)
        retry_now = time.monotonic()
        streams = [
            info
            for info in streams
            if self._failed_streams.get(_stream_uid(info), (0, 0.0))[1] <= retry_now
        ]
        closest = closest_streams(streams, self.config.match)
        with self._selection_lock:
            preferred_uid = self._preferred_uid
            preferred_identity = self._preferred_identity
        for info in closest:
            if _stream_uid(info) == preferred_uid or _stream_identity(info) == preferred_identity:
                return info
        if len(closest) == 1:
            with self._selection_lock:
                self._selection_candidates = {}
            return closest[0]
        if len(closest) > 1:
            with self._selection_lock:
                self._selection_candidates = {
                    _stream_uid(info): (_choice_label(info), _stream_identity(info))
                    for info in closest
                }
            self.buffer.set_connection(False, "Choose which matching LSL stream to attach")
        return None

    def _record_failed_stream(self, info: Any) -> None:
        """Back off a dead outlet UID while allowing a replacement UID immediately."""

        uid = _stream_uid(info)
        failures, _ = self._failed_streams.get(uid, (0, 0.0))
        failures += 1
        retry_delay = min(30.0, float(2 ** min(failures, 5)))
        self._failed_streams[uid] = (failures, time.monotonic() + retry_delay)

    def _record_stream_success(self, info: Any) -> None:
        self._failed_streams.pop(_stream_uid(info), None)

    def _connect(self, info: Any) -> Any:
        pylsl = self._module()
        inlet = pylsl.StreamInlet(
            info,
            max_buflen=max(1, int(self.history_seconds * 2)),
            recover=False,
        )
        try:
            inlet.open_stream(timeout=1.0)
            # Resolver results contain only basic fields. Channel labels live in
            # the full description returned by an opened inlet.
            full_info = inlet.info(timeout=1.0)
            channel_count = int(full_info.channel_count())
            metadata_labels = read_channel_labels(full_info, channel_count)
            indices, labels, colors = resolve_channel_indices(
                self.config.channels, metadata_labels, channel_count
            )
            nominal_srate = float(full_info.nominal_srate())
            expected_points = (
                int(max(1.0, nominal_srate) * self.history_seconds * 1.5)
                if nominal_srate > 0
                else self.max_points
            )
            capacity = min(self.max_points, max(512, expected_points))
            self._raw_indices = indices
            self._markers = is_marker_stream(
                full_info, int(getattr(pylsl, "cf_string", 3))
            )
            self.display_name = full_stream_name(full_info)
            self.buffer.reset_channels(labels, colors, nominal_srate, capacity)
            self.buffer.set_connection(True, f"Connected to {full_info.name()}")
            return inlet
        except Exception:
            try:
                inlet.close_stream()
            except Exception:
                pass
            raise

    def _run(self) -> None:
        inlet = None
        info = None
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
                    try:
                        correction = float(inlet.time_correction(timeout=0.2))
                    except Exception:
                        # Clock synchronization can need longer than data startup,
                        # especially for several same-host inlets. Samples remain
                        # usable in the local clock domain while we retry.
                        correction_due = now + 1.0
                    else:
                        correction_due = now + 5.0
                chunk, timestamps = inlet.pull_chunk(timeout=0.1, max_samples=1024)
                if timestamps:
                    self._record_stream_success(info)
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
                if info is not None:
                    self._record_failed_stream(info)
                try:
                    if inlet is not None:
                        inlet.close_stream()
                except Exception:
                    pass
                inlet = None
                info = None
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
