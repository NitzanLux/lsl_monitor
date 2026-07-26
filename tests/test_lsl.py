import threading
import time

from lsl_monitor.config import ChannelConfig, StreamConfig
from lsl_monitor.lsl import (
    LSLStreamWorker,
    is_marker_stream,
    read_channel_labels,
    stream_matches,
)


class FakeInfo:
    def __init__(self, name: str, stream_type: str, source_id: str) -> None:
        self._name = name
        self._type = stream_type
        self._source_id = source_id

    def name(self) -> str:
        return self._name

    def type(self) -> str:
        return self._type

    def source_id(self) -> str:
        return self._source_id


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
        self.info = info
        self.max_buflen = max_buflen
        self.first_chunk = threading.Event()
        self.sent = False
        self.closed = False

    def open_stream(self, timeout: float) -> None:
        assert timeout == 1.0

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
        assert wait_time == 0.25
        return [self.info]

    def StreamInlet(self, info: FakeStreamInfo, max_buflen: int) -> FakeInlet:
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

    def StreamInlet(self, info: FakeMarkerInfo, max_buflen: int) -> FakeMarkerInlet:
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
