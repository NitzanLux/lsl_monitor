import math

import pytest

from lsl_monitor.demo import DEMO_STREAMS, camera_2_is_active, numeric_sample


def test_demo_streams_match_full_experiment_config() -> None:
    assert len(DEMO_STREAMS) == 8
    assert {stream.key for stream in DEMO_STREAMS} == {
        "emg",
        "imu",
        "cursor",
        "camera_0",
        "camera_1",
        "camera_2",
        "motor_markers",
        "camera_events",
    }
    assert next(stream for stream in DEMO_STREAMS if stream.key == "emg").channel_labels == (
        tuple(f"Channel {index}" for index in range(1, 17))
    )
    assert next(stream for stream in DEMO_STREAMS if stream.key == "imu").channel_labels == (
        "Acc X",
        "Acc Y",
        "Acc Z",
        "Gyro X",
        "Gyro Y",
        "Gyro Z",
    )


@pytest.mark.parametrize(
    ("key", "channel_count"),
    (("emg", 16), ("imu", 6), ("cursor", 2), ("camera_0", 1)),
)
def test_numeric_samples_have_expected_shape_and_finite_values(
    key: str, channel_count: int
) -> None:
    sample = numeric_sample(key, 3.25)

    assert len(sample) == channel_count
    assert all(math.isfinite(value) for value in sample)


def test_camera_fault_cycle_pauses_and_recovers() -> None:
    assert camera_2_is_active(7.9, 16.0)
    assert not camera_2_is_active(8.0, 16.0)
    assert not camera_2_is_active(11.9, 16.0)
    assert camera_2_is_active(12.0, 16.0)
    assert camera_2_is_active(10_000.0, 0.0)
