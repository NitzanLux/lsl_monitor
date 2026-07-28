import threading
import time

import pytest

from lsl_monitor.config import ChannelConfig, ConfigError, StreamConfig
from lsl_monitor.lsl import (
    LSLStreamWorker,
    is_marker_stream,
    read_channel_labels,
    select_stream,
    stream_matches,
)


class FakeInfo:
    def __init__(
        self, name: str, stream_type: str, source_id: str, hostname: str = ""
    ) -> None:
        self._name = name
        self._type = stream_type
        self._source_id = source_id
        self._hostname = hostname

    def name(self) -> str:
        return self._name

    def type(self) -> str:
        return self._type

    def source_id(self) -> str:
        return self._source_id

    def hostname(self) -> str:
        return self._hostname


class BrokenDescription:
    def child(self, _: str) -> "BrokenDescription":
        raise RuntimeError("metadata unavailable")


class InfoWithoutMetadata:
    def desc(self) -> BrokenDescription:
        return BrokenDescription()


def test_stream_matching_combines_exact_and_regex_rules() -> None:
    info = FakeInfo("BioSemi EEG", "EEG", "amp-1")

    assert stream_matches(info, {"type": "EEG", "name_regex": "^Bio"})
    assert not stream_matches(info, {"type": "EMG", "name_regex": "^Bio"})
    assert not stream_matches(info, {"source_id": "amp-2"})


def test_stream_matching_checks_merged_identity_against_both_lsl_fields() -> None:
    matching = FakeInfo("amp-1", "EMG", "amp-1")
    different_source = FakeInfo("amp-1", "EMG", "device-1")

    assert stream_matches(matching, {"identity": "amp-1"})
    assert not stream_matches(different_source, {"identity": "amp-1"})


def test_selector_accepts_machine_suffix_and_prefers_closest_name() -> None:
    streams = [
        FakeInfo("XtrodesEMG-lab-pc", "Signals", "XtrodesEMG-lab-pc", "lab-pc"),
        FakeInfo("UnrelatedEMG", "Signals", "other", "other-pc"),
    ]

    selected = select_stream(
        streams,
        {"name": "XtrodesEMG", "type": "Signals", "source_id": "XtrodesEMG"},
    )

    assert selected is streams[0]


def test_selector_requires_hostname_for_duplicate_closest_streams() -> None:
    streams = [
        FakeInfo("XtrodesEMG-pc-a", "Signals", "XtrodesEMG-pc-a", "pc-a"),
        FakeInfo("XtrodesEMG-pc-b", "Signals", "XtrodesEMG-pc-b", "pc-b"),
    ]
    rules = {"name": "XtrodesEMG", "type": "Signals", "source_id": "XtrodesEMG"}

    with pytest.raises(ConfigError, match="choose one"):
        select_stream(streams, rules)

    assert select_stream(streams, {**rules, "hostname": "pc-b"}) is streams[1]


def test_worker_exposes_duplicate_choices_and_uses_runtime_selection() -> None:
    first = FakeInfo("Device-pc-a", "EEG", "source-pc-a", "pc-a")
    second = FakeInfo("Device-pc-b", "EEG", "source-pc-b", "pc-b")

    class DuplicatePylsl:
        def resolve_streams(self, wait_time: float) -> list[FakeInfo]:
            assert wait_time == 1.0
            return [first, second]

    worker = LSLStreamWorker(
        StreamConfig(
            id="eeg",
            match={"name": "Device", "type": "EEG", "source_id": "source"},
            channels=(ChannelConfig(index=0),),
            views=(),
        ),
        history_seconds=5.0,
        inactive_after=2.0,
        max_points=1000,
        pylsl_module=DuplicatePylsl(),
    )

    assert worker._find_stream() is None
    options = worker.selection_options()
    assert len(options) == 2
    pc_b_choice = next(choice_id for choice_id, label in options if "@ pc-b" in label)
    worker.choose_stream(pc_b_choice)

    assert worker._find_stream() is second
    assert worker.selection_options() == ()


def test_channel_labels_fall_back_when_metadata_is_missing() -> None:
    assert read_channel_labels(InfoWithoutMetadata(), 3) == ["Ch0", "Ch1", "Ch2"]


class FakeStreamInfo(FakeInfo):
    def channel_count(self) -> int:
        return 3

    def nominal_srate(self) -> float:
        return 100.0

    def desc(self) -> BrokenDescription:
        return BrokenDescription()


class FakeInlet:
    def __init__(self, info: FakeStreamInfo, max_buflen: int) -> None:
        self.stream_info = info
        self.max_buflen = max_buflen
        self.first_chunk = threading.Event()
        self.sent = False
        self.closed = False

    def open_stream(self, timeout: float) -> None:
        assert timeout == 1.0

    def info(self, timeout: float) -> FakeStreamInfo:
        assert timeout == 1.0
        return self.stream_info

    def time_correction(self, timeout: float) -> float:
        assert timeout == 0.2
        return 0.5

    def pull_chunk(self, timeout: float, max_samples: int) -> tuple[list, list]:
        assert max_samples == 1024
        if not self.sent:
            self.sent = True
            self.first_chunk.set()
            return [[1.0, 2.0, 3.0]], [10.0]
        time.sleep(min(timeout, 0.01))
        return [], []

    def close_stream(self) -> None:
        self.closed = True


class FakePylsl:
    def __init__(self) -> None:
        self.info = FakeStreamInfo("Device", "EEG", "source-1")
        self.inlet: FakeInlet | None = None

    def resolve_streams(self, wait_time: float) -> list[FakeStreamInfo]:
        assert wait_time == 1.0
        return [self.info]

    def StreamInlet(
        self, info: FakeStreamInfo, max_buflen: int, recover: bool
    ) -> FakeInlet:
        assert recover is False
        self.inlet = FakeInlet(info, max_buflen)
        return self.inlet

    def local_clock(self) -> float:
        return 12.0


def test_worker_selects_channels_and_corrects_lsl_timestamps() -> None:
    module = FakePylsl()
    config = StreamConfig(
        id="eeg",
        match={"type": "EEG"},
        channels=(
            ChannelConfig(index=2, label="Third"),
            ChannelConfig(index=0, label="First"),
        ),
        views=(),
    )
    worker = LSLStreamWorker(
        config,
        history_seconds=5.0,
        inactive_after=3.0,
        max_points=1000,
        pylsl_module=module,
    )

    worker.start()
    deadline = time.monotonic() + 1.0
    while (
        module.inlet is None or not module.inlet.first_chunk.is_set()
    ) and time.monotonic() < deadline:
        time.sleep(0.005)
    snapshot = worker.snapshot()
    worker.stop()

    assert snapshot.active is True
    assert snapshot.channel_labels == ("Third", "First")
    assert snapshot.timestamps.tolist() == [10.5]
    assert snapshot.samples.tolist() == [[3.0], [1.0]]
    assert module.inlet is not None and module.inlet.closed is True


class FakeMarkerInfo(FakeStreamInfo):
    def channel_count(self) -> int:
        return 1

    def nominal_srate(self) -> float:
        return 0.0

    def channel_format(self) -> int:
        return 3


class FakeMarkerInlet(FakeInlet):
    def pull_chunk(self, timeout: float, max_samples: int) -> tuple[list, list]:
        if not self.sent:
            self.sent = True
            self.first_chunk.set()
            return [["cue/left"]], [10.0]
        time.sleep(min(timeout, 0.01))
        return [], []


class FakeMarkerPylsl(FakePylsl):
    cf_string = 3

    def __init__(self) -> None:
        super().__init__()
        self.info = FakeMarkerInfo("Events", "Markers", "source-2")

    def StreamInlet(
        self, info: FakeMarkerInfo, max_buflen: int, recover: bool
    ) -> FakeMarkerInlet:
        assert recover is False
        self.inlet = FakeMarkerInlet(info, max_buflen)
        return self.inlet


def test_string_channel_format_is_detected_as_a_marker_stream() -> None:
    assert is_marker_stream(FakeMarkerInfo("Events", "Markers", "source-2")) is True
    assert is_marker_stream(FakeStreamInfo("Device", "EEG", "source-1")) is False


def test_worker_stores_string_markers_with_corrected_timestamps() -> None:
    module = FakeMarkerPylsl()
    config = StreamConfig(
        id="events",
        match={"type": "Markers"},
        channels=(ChannelConfig(index=0, label="Event"),),
        views=(),
    )
    worker = LSLStreamWorker(
        config,
        history_seconds=5.0,
        inactive_after=3.0,
        max_points=1000,
        pylsl_module=module,
    )

    worker.start()
    deadline = time.monotonic() + 1.0
    while (
        module.inlet is None or not module.inlet.first_chunk.is_set()
    ) and time.monotonic() < deadline:
        time.sleep(0.005)
    snapshot = worker.snapshot()
    worker.stop()

    assert [(event.lsl_time, event.text) for event in snapshot.markers] == [
        (10.5, "cue/left")
    ]
    assert snapshot.timestamps.tolist() == [10.5]
    assert snapshot.active is True


class LostInlet(FakeInlet):
    def pull_chunk(self, timeout: float, max_samples: int) -> tuple[list, list]:
        self.first_chunk.set()
        raise RuntimeError("Input stream error")


class ReconnectingPylsl(FakePylsl):
    def __init__(self) -> None:
        super().__init__()
        self.inlets: list[FakeInlet] = []

    def StreamInlet(
        self, info: FakeStreamInfo, max_buflen: int, recover: bool
    ) -> FakeInlet:
        assert recover is False
        inlet: FakeInlet
        if not self.inlets:
            inlet = LostInlet(info, max_buflen)
            self.info = FakeStreamInfo("Device replacement", "EEG", "source-2")
        else:
            inlet = FakeInlet(info, max_buflen)
        self.inlets.append(inlet)
        self.inlet = inlet
        return inlet


def test_worker_rediscovers_after_outlet_transmission_is_lost() -> None:
    module = ReconnectingPylsl()
    worker = LSLStreamWorker(
        StreamConfig(
            id="eeg",
            match={"type": "EEG"},
            channels=(ChannelConfig(index=0),),
            views=(),
        ),
        history_seconds=5.0,
        inactive_after=3.0,
        max_points=1000,
        pylsl_module=module,
    )

    worker.start()
    deadline = time.monotonic() + 2.0
    while (
        len(module.inlets) < 2 or not module.inlets[-1].first_chunk.is_set()
    ) and time.monotonic() < deadline:
        time.sleep(0.005)
    snapshot = worker.snapshot()
    worker.stop()

    assert len(module.inlets) >= 2
    assert module.inlets[0].closed is True
    assert snapshot.active is True


def test_worker_backs_off_same_dead_uid_but_retries_later() -> None:
    module = FakePylsl()
    worker = LSLStreamWorker(
        StreamConfig(
            id="eeg",
            match={"type": "EEG"},
            channels=(ChannelConfig(index=0),),
            views=(),
        ),
        history_seconds=5.0,
        inactive_after=3.0,
        max_points=1000,
        pylsl_module=module,
    )

    worker._record_failed_stream(module.info)
    assert worker._find_stream() is None

    uid = next(iter(worker._failed_streams))
    failures, _ = worker._failed_streams[uid]
    worker._failed_streams[uid] = (failures, 0.0)
    assert worker._find_stream() is module.info


class CorrectionTimeoutInlet(FakeInlet):
    def time_correction(self, timeout: float) -> float:
        assert timeout == 0.2
        raise RuntimeError("the operation failed due to a timeout")


class CorrectionTimeoutPylsl(FakePylsl):
    def StreamInlet(
        self, info: FakeStreamInfo, max_buflen: int, recover: bool
    ) -> CorrectionTimeoutInlet:
        assert recover is False
        self.inlet = CorrectionTimeoutInlet(info, max_buflen)
        return self.inlet


def test_time_correction_timeout_does_not_disconnect_a_healthy_stream() -> None:
    module = CorrectionTimeoutPylsl()
    worker = LSLStreamWorker(
        StreamConfig(
            id="eeg",
            match={"type": "EEG"},
            channels=(ChannelConfig(index=0),),
            views=(),
        ),
        history_seconds=5.0,
        inactive_after=3.0,
        max_points=1000,
        pylsl_module=module,
    )

    worker.start()
    deadline = time.monotonic() + 1.0
    while (
        module.inlet is None or not module.inlet.first_chunk.is_set()
    ) and time.monotonic() < deadline:
        time.sleep(0.005)
    snapshot = worker.snapshot()
    worker.stop()

    assert snapshot.active is True
    assert snapshot.timestamps.tolist() == [10.0]
