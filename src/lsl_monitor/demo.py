"""Live LSL outlets for demonstrating the full experiment monitor."""

from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DemoStream:
    """Description of one outlet expected by experiment_monitor_full.json."""

    key: str
    name: str
    content_type: str
    source_id: str
    channel_labels: tuple[str, ...]
    sample_rate: float
    markers: bool = False


DEMO_STREAMS = (
    DemoStream(
        "emg",
        "XtrodesEMG",
        "Signals",
        "XtrodesEMG",
        tuple(f"Channel {index}" for index in range(1, 17)),
        200.0,
    ),
    DemoStream(
        "imu",
        "XtrodesIMU",
        "Markers",
        "XtrodesIMU",
        ("Acc X", "Acc Y", "Acc Z", "Gyro X", "Gyro Y", "Gyro Z"),
        100.0,
    ),
    DemoStream(
        "cursor",
        "MotorControlCursor",
        "MotorControlCursor",
        "MotorControlCursor",
        ("raw_x", "raw_y"),
        60.0,
    ),
    DemoStream(
        "camera_0",
        "FemtoBolt_cam0_CL8E751007P",
        "FemtoBolt_cam0_CL8E751007P",
        "FemtoBolt_cam0_CL8E751007P",
        ("Frame",),
        15.0,
    ),
    DemoStream(
        "camera_2",
        "FemtoBolt_cam2_CL8E751008M",
        "Control",
        "FemtoBolt_cam2_CL8E751008M",
        ("Frame",),
        15.0,
    ),
    DemoStream(
        "camera_1",
        "FemtoBolt_cam1_CL8E7510041",
        "FemtoBolt_cam1_CL8E7510041",
        "FemtoBolt_cam1_CL8E7510041",
        ("Frame",),
        15.0,
    ),
    DemoStream(
        "motor_markers",
        "MotorControlMarker",
        "Markers",
        "MotorControlMarker",
        ("Marker",),
        0.0,
        markers=True,
    ),
    DemoStream(
        "camera_events",
        "FemtoBoltEvents",
        "Markers",
        "FemtoBoltEvents",
        ("Event",),
        0.0,
        markers=True,
    ),
)

MOTOR_MARKERS = ("trial_start", "target_left", "go", "hit", "trial_end")
CAMERA_EVENTS = ("recording_start", "frame_sync", "exposure_ok", "frame_sync")


def numeric_sample(key: str, elapsed: float) -> list[float]:
    """Return one deterministic, animated sample for a numeric demo stream."""

    if key == "emg":
        samples = []
        for channel in range(16):
            frequency = 2.0 + channel * 0.65
            carrier = math.sin(2.0 * math.pi * frequency * elapsed + channel * 0.31)
            envelope = 0.55 + 0.45 * math.sin(
                2.0 * math.pi * 0.17 * elapsed + channel * 0.23
            )
            power_line = 0.08 * math.sin(2.0 * math.pi * 50.0 * elapsed)
            samples.append((0.35 + channel * 0.025) * envelope * carrier + power_line)
        return samples
    if key == "imu":
        return [
            0.35 * math.sin(2.0 * math.pi * 0.55 * elapsed),
            0.25 * math.cos(2.0 * math.pi * 0.43 * elapsed),
            1.0 + 0.08 * math.sin(2.0 * math.pi * 1.1 * elapsed),
            22.0 * math.sin(2.0 * math.pi * 0.31 * elapsed),
            18.0 * math.cos(2.0 * math.pi * 0.27 * elapsed),
            12.0 * math.sin(2.0 * math.pi * 0.19 * elapsed),
        ]
    if key == "cursor":
        radius = 0.72 + 0.12 * math.sin(2.0 * math.pi * 0.11 * elapsed)
        angle = 2.0 * math.pi * 0.08 * elapsed
        return [radius * math.cos(angle), radius * math.sin(angle)]
    if key.startswith("camera_"):
        return [elapsed]
    raise ValueError(f"Unknown numeric demo stream {key!r}")


def camera_2_is_active(elapsed: float, fault_cycle_seconds: float) -> bool:
    """Pause camera 2 during the middle quarter of each enabled fault cycle."""

    if fault_cycle_seconds <= 0:
        return True
    phase = elapsed % fault_cycle_seconds
    return not (fault_cycle_seconds * 0.5 <= phase < fault_cycle_seconds * 0.75)


def create_outlets(pylsl: Any) -> dict[str, Any]:
    """Create all outlets, including channel labels used by name-based matching."""

    outlets = {}
    for stream in DEMO_STREAMS:
        channel_format = pylsl.cf_string if stream.markers else pylsl.cf_float32
        info = pylsl.StreamInfo(
            stream.name,
            stream.content_type,
            len(stream.channel_labels),
            stream.sample_rate,
            channel_format,
            stream.source_id,
        )
        channels = info.desc().append_child("channels")
        for label in stream.channel_labels:
            channel = channels.append_child("channel")
            channel.append_child_value("label", label)
        outlets[stream.key] = pylsl.StreamOutlet(info)
    return outlets


def run_demo(duration: float, fault_cycle_seconds: float) -> None:
    """Publish the full experiment until interrupted or duration expires."""

    import pylsl

    outlets = create_outlets(pylsl)
    numeric_streams = tuple(stream for stream in DEMO_STREAMS if not stream.markers)
    started = float(pylsl.local_clock())
    next_sample = {stream.key: started for stream in numeric_streams}
    next_motor_marker = started + 0.8
    next_camera_event = started + 1.2
    motor_index = 0
    camera_index = 0
    camera_was_active = True

    print(f"Publishing {len(outlets)} LSL demo streams. Press Ctrl+C to stop.")
    if fault_cycle_seconds > 0:
        print(
            "Camera 2 will pause for "
            f"{fault_cycle_seconds / 4:g}s every {fault_cycle_seconds:g}s "
            "to demonstrate inactive/recovery feedback."
        )

    try:
        while duration <= 0 or float(pylsl.local_clock()) - started < duration:
            now = float(pylsl.local_clock())
            elapsed = now - started
            camera_active = camera_2_is_active(elapsed, fault_cycle_seconds)
            if camera_active != camera_was_active:
                state = "ACTIVE again" if camera_active else "PAUSED (health should turn red)"
                print(f"[{elapsed:6.1f}s] Camera 2 {state}")
                camera_was_active = camera_active

            for stream in numeric_streams:
                due = next_sample[stream.key]
                if stream.key == "camera_2" and not camera_active:
                    next_sample[stream.key] = now + 1.0 / stream.sample_rate
                    continue
                if now >= due:
                    outlets[stream.key].push_sample(
                        numeric_sample(stream.key, due - started), timestamp=due
                    )
                    interval = 1.0 / stream.sample_rate
                    next_sample[stream.key] = max(due + interval, now - interval)

            if now >= next_motor_marker:
                outlets["motor_markers"].push_sample(
                    [MOTOR_MARKERS[motor_index % len(MOTOR_MARKERS)]],
                    timestamp=next_motor_marker,
                )
                motor_index += 1
                next_motor_marker += 1.25

            if now >= next_camera_event:
                outlets["camera_events"].push_sample(
                    [CAMERA_EVENTS[camera_index % len(CAMERA_EVENTS)]],
                    timestamp=next_camera_event,
                )
                camera_index += 1
                next_camera_event += 1.7

            time.sleep(0.001)
    except KeyboardInterrupt:
        pass
    finally:
        print("LSL demo stopped.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Publish mock LSL streams for json/experiment_monitor_full.json"
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="seconds to run; 0 (the default) runs until Ctrl+C",
    )
    parser.add_argument(
        "--fault-cycle-seconds",
        type=float,
        default=16.0,
        help="camera 2 fault-cycle length; 0 disables simulated dropouts (default: 16)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.duration < 0:
        build_parser().error("--duration must be zero or greater")
    if args.fault_cycle_seconds < 0:
        build_parser().error("--fault-cycle-seconds must be zero or greater")
    run_demo(args.duration, args.fault_cycle_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
