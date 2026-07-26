import json
from pathlib import Path

import pytest

from lsl_monitor.config import (
    ChannelConfig,
    ConfigError,
    configured_view_positions,
    load_config,
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
