"""Configuration loading, schema validation, and channel selection."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class ConfigError(ValueError):
    """Raised when a monitor configuration is invalid."""


@dataclass(frozen=True)
class ChannelConfig:
    """A channel selected by zero-based index or LSL metadata label."""

    index: int | None = None
    name: str | None = None
    label: str | None = None
    color: str | None = None

    @property
    def reference(self) -> int | str:
        if self.index is not None:
            return self.index
        assert self.name is not None
        return self.name


@dataclass(frozen=True)
class PanelLayoutConfig:
    """Responsive panel rectangle stored as window-relative fractions."""

    x: float
    y: float
    width: float
    height: float


#: Rows the markers panel shows before older events are only counted.
DEFAULT_MARKER_LIMIT = 48

#: Decibels below the loudest bin that the spectrogram still colors.
DEFAULT_DYNAMIC_RANGE_DB = 60.0

#: Color map used by a spectrogram that does not name one.
DEFAULT_COLORMAP = "viridis"

#: Color maps a spectrogram can be drawn with.
COLORMAPS = ("viridis", "magma", "inferno", "plasma", "cividis", "turbo")

#: Seconds of history each audio level meter integrates over.
DEFAULT_LEVEL_SECONDS = 0.4


@dataclass(frozen=True)
class ViewConfig:
    """One visual panel attached to a configured stream.

    Beyond `type`, `title`, `channels`, and `layout`, every field is an argument
    of one panel type only, and is ignored by the others. `status_dot` is the
    exception: every panel can show the stream's activity dot.
    """

    type: str
    title: str | None = None
    channels: tuple[int | str, ...] = ()
    status_dot: bool = True
    alignment: str = "stacked"
    fft_size: int = 1024
    frequency_range: tuple[float, float] | None = None
    marker_seconds: float | None = None
    marker_limit: int = DEFAULT_MARKER_LIMIT
    trail_seconds: float | None = None
    spectrogram_seconds: float | None = None
    dynamic_range_db: float = DEFAULT_DYNAMIC_RANGE_DB
    colormap: str = DEFAULT_COLORMAP
    audio_gain: float = 1.0
    audio_muted: bool = True
    level_seconds: float = DEFAULT_LEVEL_SECONDS
    layout: PanelLayoutConfig | None = None

    def marker_window(self, history_seconds: float) -> float:
        """Return the marker roll length, falling back to the trace history."""

        return self.marker_seconds or history_seconds

    def trail_window(self, history_seconds: float) -> float:
        """Return the 2D trajectory length, falling back to the trace history."""

        return self.trail_seconds or history_seconds

    def spectrogram_window(self, history_seconds: float) -> float:
        """Return the spectrogram length, falling back to the trace history."""

        return self.spectrogram_seconds or history_seconds


@dataclass(frozen=True)
class StreamConfig:
    """LSL stream match rules, selected channels, and requested views."""

    id: str
    match: dict[str, str]
    channels: tuple[ChannelConfig, ...]
    views: tuple[ViewConfig, ...]


@dataclass(frozen=True)
class WindowConfig:
    title: str = "LSL Monitor"
    history_seconds: float = 10.0
    refresh_hz: float = 20.0
    columns: int = 2
    inactive_after_seconds: float = 2.0
    max_points_per_channel: int = 100_000


@dataclass(frozen=True)
class MonitorConfig:
    window: WindowConfig
    streams: tuple[StreamConfig, ...]
    source_path: Path = field(compare=False)


def default_schema_path() -> Path:
    """Return the repository/package JSON schema path."""

    repository_schema = (
        Path(__file__).resolve().parents[2] / "schemas" / "lsl-monitor.schema.json"
    )
    if repository_schema.exists():
        return repository_schema
    packaged_schema = Path(__file__).with_name("schemas") / "lsl-monitor.schema.json"
    if packaged_schema.exists():
        return packaged_schema
    raise ConfigError("Could not locate lsl-monitor.schema.json")


def _format_validation_error(error: Any) -> str:
    location = ".".join(str(part) for part in error.absolute_path)
    prefix = f"{location}: " if location else ""
    return f"{prefix}{error.message}"


def validate_document(document: dict[str, Any], schema_path: Path | None = None) -> None:
    """Validate a decoded configuration against the bundled JSON Schema."""

    schema_file = schema_path or default_schema_path()
    with schema_file.open(encoding="utf-8") as handle:
        schema = json.load(handle)
    validator = Draft202012Validator(
        schema, format_checker=Draft202012Validator.FORMAT_CHECKER
    )
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    if errors:
        details = "\n".join(f"- {_format_validation_error(error)}" for error in errors)
        raise ConfigError(f"Invalid monitor configuration:\n{details}")


def load_config(path: str | Path, schema_path: Path | None = None) -> MonitorConfig:
    """Load, validate, and normalize a monitor configuration."""

    source_path = Path(path).resolve()
    try:
        with source_path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"Could not read {source_path}: {error}") from error

    return monitor_config_from_document(document, source_path, schema_path)


def monitor_config_from_document(
    document: dict[str, Any],
    source_path: str | Path = Path("unsaved.monitor.json"),
    schema_path: Path | None = None,
) -> MonitorConfig:
    """Validate and normalize an in-memory configuration document."""

    source_path = Path(source_path).resolve()
    validate_document(document, schema_path)
    window_data = document.get("window", {})
    window = WindowConfig(
        title=window_data.get("title", "LSL Monitor"),
        history_seconds=float(window_data.get("history_seconds", 10.0)),
        refresh_hz=float(window_data.get("refresh_hz", 20.0)),
        columns=int(window_data.get("columns", 2)),
        inactive_after_seconds=float(window_data.get("inactive_after_seconds", 2.0)),
        max_points_per_channel=int(window_data.get("max_points_per_channel", 100_000)),
    )

    streams = []
    for stream_data in document["streams"]:
        channels = tuple(ChannelConfig(**item) for item in stream_data["channels"])
        views = []
        for item in stream_data["views"]:
            frequency_range = item.get("frequency_range")
            marker_seconds = item.get("marker_seconds")
            trail_seconds = item.get("trail_seconds")
            spectrogram_seconds = item.get("spectrogram_seconds")
            layout = item.get("layout")
            views.append(
                ViewConfig(
                    type=item["type"],
                    title=item.get("title"),
                    channels=tuple(item.get("channels", ())),
                    status_dot=bool(item.get("status_dot", True)),
                    alignment=item.get("alignment", "stacked"),
                    fft_size=int(item.get("fft_size", 1024)),
                    frequency_range=(
                        (float(frequency_range[0]), float(frequency_range[1]))
                        if frequency_range
                        else None
                    ),
                    marker_seconds=(
                        float(marker_seconds) if marker_seconds is not None else None
                    ),
                    marker_limit=int(item.get("marker_limit", DEFAULT_MARKER_LIMIT)),
                    trail_seconds=(
                        float(trail_seconds) if trail_seconds is not None else None
                    ),
                    layout=PanelLayoutConfig(**layout) if layout else None,
                )
            )
        streams.append(
            StreamConfig(
                id=stream_data["id"],
                match=dict(stream_data["match"]),
                channels=channels,
                views=tuple(views),
            )
        )
    identifiers = [stream.id for stream in streams]
    if len(identifiers) != len(set(identifiers)):
        raise ConfigError("Stream ids must be unique")
    for stream in streams:
        for view in stream.views:
            positions = configured_view_positions(view.channels, stream.channels)
            if view.type == "plane_2d" and len(positions) != 2:
                raise ConfigError(
                    f"{stream.id} view {view.title or view.type!r}: "
                    "plane_2d requires exactly 2 selected channels"
                )
            if (
                view.frequency_range is not None
                and view.frequency_range[1] <= view.frequency_range[0]
            ):
                raise ConfigError(
                    f"{stream.id} view {view.title or view.type!r}: "
                    "frequency_range upper bound must exceed lower bound"
                )
            if view.layout is not None and (
                view.layout.x + view.layout.width > 1.0 + 1e-9
                or view.layout.y + view.layout.height > 1.0 + 1e-9
            ):
                raise ConfigError(
                    f"{stream.id} view {view.title or view.type!r}: "
                    "layout must remain within the window"
                )
    return MonitorConfig(window=window, streams=tuple(streams), source_path=source_path)


def resolve_channel_indices(
    configured: tuple[ChannelConfig, ...], metadata_labels: list[str], channel_count: int
) -> tuple[list[int], list[str], list[str | None]]:
    """Resolve configured channels against a connected stream.

    Returns raw indices, display labels, and optional colors in configuration order.
    """

    casefolded = {label.casefold(): index for index, label in enumerate(metadata_labels)}
    indices: list[int] = []
    labels: list[str] = []
    colors: list[str | None] = []
    for channel in configured:
        if channel.index is not None:
            index = channel.index
            if index >= channel_count:
                raise ConfigError(
                    f"Channel index {index} is outside connected stream with "
                    f"{channel_count} channels"
                )
        else:
            assert channel.name is not None
            try:
                index = casefolded[channel.name.casefold()]
            except KeyError as error:
                raise ConfigError(
                    f"Channel {channel.name!r} was not found; available labels: "
                    f"{', '.join(metadata_labels) or '(none)'}"
                ) from error
        if index in indices:
            raise ConfigError(f"Channel {index} was selected more than once")
        indices.append(index)
        labels.append(channel.label or metadata_labels[index] or f"Ch{index}")
        colors.append(channel.color)
    return indices, labels, colors


def resolve_view_channels(
    references: tuple[int | str, ...],
    raw_indices: list[int],
    display_labels: list[str],
    configured: tuple[ChannelConfig, ...],
) -> list[int]:
    """Map view channel references to positions in the selected-channel arrays."""

    if not references:
        return list(range(len(raw_indices)))
    result: list[int] = []
    for reference in references:
        position: int | None = None
        if isinstance(reference, int):
            if reference in raw_indices:
                position = raw_indices.index(reference)
        else:
            target = reference.casefold()
            for index, channel in enumerate(configured):
                names = {display_labels[index].casefold()}
                if channel.name:
                    names.add(channel.name.casefold())
                if target in names:
                    position = index
                    break
        if position is None:
            raise ConfigError(f"View references unselected channel {reference!r}")
        if position not in result:
            result.append(position)
    return result


def configured_view_positions(
    references: tuple[int | str, ...], configured: tuple[ChannelConfig, ...]
) -> list[int]:
    """Resolve a view's references before stream metadata is available."""

    if not references:
        return list(range(len(configured)))
    positions: list[int] = []
    for reference in references:
        position = None
        for index, channel in enumerate(configured):
            if isinstance(reference, int) and channel.index == reference:
                position = index
                break
            if isinstance(reference, str):
                names = {value.casefold() for value in (channel.name, channel.label) if value}
                if reference.casefold() in names:
                    position = index
                    break
        if position is None:
            raise ConfigError(f"View references unselected channel {reference!r}")
        if position not in positions:
            positions.append(position)
    return positions
