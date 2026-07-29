import json
from pathlib import Path

import pytest

from lsl_monitor.config import (
    DEFAULT_COLORMAP,
    DEFAULT_DYNAMIC_RANGE_DB,
    DEFAULT_LEVEL_SECONDS,
    ChannelConfig,
    ConfigError,
    configured_view_positions,
    load_config,
    monitor_config_from_document,
    resolve_channel_indices,
)

ROOT = Path(__file__).resolve().parents[1]


def test_example_configuration_is_valid() -> None:
    config = load_config(ROOT / "json" / "example.monitor.json")

    assert config.window.history_seconds == 10
    assert [stream.id for stream in config.streams] == ["emg", "markers"]
    assert [view.type for view in config.streams[0].views] == [
        "traces",
        "plane_2d",
        "psd",
        "alive",
    ]


def test_schema_rejects_channel_with_index_and_name(tmp_path: Path) -> None:
    document = {
        "streams": [
            {
                "id": "bad",
                "match": {"type": "EEG"},
                "channels": [{"index": 0, "name": "Fp1"}],
                "views": [{"type": "alive"}],
            }
        ]
    }
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid under any"):
        load_config(path)


def test_semantic_validation_rejects_duplicate_ids(tmp_path: Path) -> None:
    stream = {
        "id": "same",
        "match": {"type": "EEG"},
        "channels": [{"index": 0}],
        "views": [{"type": "alive"}],
    }
    path = tmp_path / "duplicate.json"
    path.write_text(json.dumps({"streams": [stream, stream]}), encoding="utf-8")

    with pytest.raises(ConfigError, match="unique"):
        load_config(path)


def test_resolve_channels_by_index_and_metadata_name() -> None:
    channels = (
        ChannelConfig(name="Cz", label="Center", color="#112233"),
        ChannelConfig(index=0),
    )

    indices, labels, colors = resolve_channel_indices(channels, ["Fp1", "Cz"], 2)

    assert indices == [1, 0]
    assert labels == ["Center", "Fp1"]
    assert colors == ["#112233", None]


def test_configured_view_references_accept_label_name_and_index() -> None:
    channels = (
        ChannelConfig(index=3, label="Left"),
        ChannelConfig(name="Cz", label="Center"),
    )

    assert configured_view_positions(("Left", "Cz"), channels) == [0, 1]
    assert configured_view_positions((3,), channels) == [0]
    with pytest.raises(ConfigError, match="unselected"):
        configured_view_positions((2,), channels)


def _audio_document(views: list[dict]) -> dict:
    return {
        "streams": [
            {
                "id": "microphone",
                "match": {"identity": "Mic", "type": "Audio"},
                "channels": [{"index": 0, "label": "Left"}, {"index": 1}],
                "views": views,
            }
        ]
    }


def test_spectrogram_and_audio_arguments_are_loaded() -> None:
    document = _audio_document(
        [
            {
                "type": "spectrogram",
                "channels": [0],
                "fft_size": 512,
                "frequency_range": [20, 8000],
                "spectrogram_seconds": 6,
                "dynamic_range_db": 48,
                "colormap": "magma",
            },
            {"type": "audio", "audio_gain": 4, "audio_muted": False, "level_seconds": 0.1},
        ]
    )

    spectrogram, audio = monitor_config_from_document(document).streams[0].views

    assert spectrogram.fft_size == 512
    assert spectrogram.frequency_range == (20.0, 8000.0)
    assert spectrogram.spectrogram_window(10.0) == 6.0
    assert spectrogram.dynamic_range_db == 48.0
    assert spectrogram.colormap == "magma"
    assert audio.audio_gain == 4.0
    assert audio.audio_muted is False
    assert audio.level_seconds == 0.1


def test_audio_and_spectrogram_defaults_are_quiet_and_readable() -> None:
    document = _audio_document([{"type": "spectrogram"}, {"type": "audio"}])

    spectrogram, audio = monitor_config_from_document(document).streams[0].views

    assert spectrogram.spectrogram_window(10.0) == 10.0, "defaults to the trace history"
    assert spectrogram.dynamic_range_db == DEFAULT_DYNAMIC_RANGE_DB
    assert spectrogram.colormap == DEFAULT_COLORMAP
    assert audio.audio_muted is True, "a dashboard opens silent"
    assert audio.audio_gain == 1.0
    assert audio.level_seconds == DEFAULT_LEVEL_SECONDS


def test_a_stream_keeps_the_longest_sample_history_its_panels_read() -> None:
    document = _audio_document(
        [
            {"type": "traces"},
            {"type": "spectrogram", "channels": [0], "spectrogram_seconds": 45},
            {"type": "plane_2d", "channels": [0, 1], "trail_seconds": 20},
            # Marker events are stored on their own, so a long roll asks for no
            # extra samples.
            {"type": "markers", "marker_seconds": 600},
        ]
    )

    stream = monitor_config_from_document(document).streams[0]

    assert stream.sample_seconds(10.0) == 45.0
    assert stream.sample_seconds(90.0) == 90.0, "the trace history is never shortened"


def test_schema_rejects_an_unknown_colormap() -> None:
    document = _audio_document([{"type": "spectrogram", "colormap": "rainbow"}])

    with pytest.raises(ConfigError, match="colormap"):
        monitor_config_from_document(document)


def test_responsive_panel_layout_must_stay_inside_window(tmp_path: Path) -> None:
    document = {
        "streams": [
            {
                "id": "signals",
                "match": {"type": "Signals"},
                "channels": [{"index": 0}],
                "views": [
                    {
                        "type": "traces",
                        "layout": {
                            "x": 0.75,
                            "y": 0.0,
                            "width": 0.5,
                            "height": 1.0,
                        },
                    }
                ],
            }
        ]
    }
    path = tmp_path / "outside.monitor.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(ConfigError, match="remain within"):
        load_config(path)
