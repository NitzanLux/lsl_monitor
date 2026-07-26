# LSL Monitor

LSL Monitor is a JSON-configured desktop dashboard for checking signal quality
during a Lab Streaming Layer recording. It discovers every configured stream,
reconnects when an outlet disappears, and uses corrected LSL timestamps for the
display timeline and active/inactive decisions.

The initial views are:

- `traces`: selected channels as vertically normalized, stacked lanes or raw
  overlays.
- `plane_2d`: one selected channel against another as a fading trajectory;
  opacity increases toward the latest sample.
- `psd`: a live windowed power spectral density.
- `alive`: a large green/red indicator with the age of the latest sample in LSL
  time.

Every panel header carries the stream's activity dot, its title (the view type
unless `title` overrides it), and the configured stream `id`, so each plot names
the stream it belongs to even when several streams are monitored side by side.

Panels share the available window on a responsive freeform canvas. Trace lanes
are individually normalized in `stacked` mode, which keeps all selected signals
visible even when their amplitudes differ.

## Visual layout designer

Open the designer without connecting any LSL hardware:

```powershell
uv run lsl-monitor --designer
```

The left side creates mock streams, channel labels and colors, and view panels.
The right side is a live preview using generated mock signals. Drag a panel by
its title and resize it using the lower-right handle. Placement and size are
stored as percentages, so the composition scales with the monitor window.
Numeric percentage controls and a per-panel automatic-layout reset are also
available. The designer supports traces, 2D planes, PSD plots, and active-state
panels.

**Channel order** is set by dragging, at two levels:

- Drag a row in **Channels** to reorder the whole stream. Every panel is
  restacked to follow the new order, and the saved `channels` arrays are
  rewritten to match.
- Drag an entry in **Channels shown in this panel**, or drag a lane directly in
  the live preview, to give one panel its own order without touching the
  stream. Lane dragging applies to `traces` panels in `stacked` alignment,
  where each channel occupies its own row; use the list for the other panel
  types.

The resulting order is what the panel draws, so it is also the top-to-bottom
lane order of a stacked trace panel and the legend order elsewhere.

**Preview signal** switches the mock waveform driving the selected stream, so a
layout can be checked against the kind of data it will show: `Sine mix`,
`EEG rhythms`, `Broadband noise`, `Baseline drift`, `Spike train`, or
`Square wave`. Each stream keeps its own choice. The models are deterministic
functions of LSL time, so the traces scroll smoothly and look the same on every
run.

**LSL type** is a scrollable dropdown of the content types recommended by the XDF
meta-data conventions (`EEG`, `MEG`, `ECoG`, `EMG`, `ECG`, `EOG`, `EDA`, `GSR`,
`NIRS`, `Respiration`, `HeartRate`, `Temperature`, `Gaze`, `EyeTracking`,
`Pupil`, `MoCap`, `Position`, `Orientation`, `Accelerometer`, `Gyroscope`,
`Magnetometer`, `Force`, `Audio`, `VideoRaw`, `VideoCompressed`, `Keyboard`,
`Mouse`, `Markers`, `Signals`, `Control`). LSL itself puts no constraint on the
type an outlet advertises, so the field stays editable: type any other string to
match a custom outlet, or select the empty first entry to drop the rule and match
on `name` or `source_id` alone.

Use **Save as** to export a schema-valid `.monitor.json`. Mock waveforms are
preview-only and are not written to the configuration: the exported stream match
fields and channel indices are used to connect the same layout to real LSL
outlets later.

To open an existing configuration in the designer:

```powershell
uv run lsl-monitor json/example.monitor.json --designer
```

## Install

Python 3.10 or newer is required. From the repository root:

```powershell
uv sync --all-groups
```

This creates `.venv`, installs the application and development tools, and uses
the committed `uv.lock` for reproducible versions. Activating the environment is
optional because the commands below run through `uv`.

## Configure

Copy [json/example.monitor.json](json/example.monitor.json), then edit its
`streams`. The configuration contract is
[schemas/lsl-monitor.schema.json](schemas/lsl-monitor.schema.json), a JSON Schema
Draft 2020-12 document. Editors such as VS Code can use the `$schema` entry for
completion and validation.

Each stream has:

- `id`: a unique dashboard identifier.
- `match`: one or more exact `name`, `type`, or `source_id` fields, plus optional
  `name_regex`. All supplied rules must match.
- `channels`: the relevant channels selected by zero-based `index` or exact LSL
  metadata `name`. `label` and `color` customize display.
- `views`: the panels for the stream. A view can select a subset by raw channel
  index, metadata name, or configured display label. The order of `channels` is
  the order the panel draws them in. Omitting `channels` uses every selected
  channel, in the order they appear in the stream's `channels`.
- `layout`: optional responsive `x`, `y`, `width`, and `height` fractions on a
  freeform canvas. For example, `{"x": 0, "y": 0, "width": 0.7, "height": 1}`
  gives a panel the left 70% of the window. Views without `layout` are arranged
  automatically using `window.columns`.

Useful window settings are `history_seconds`, `refresh_hz`, grid `columns`, and
`inactive_after_seconds`. The last setting is evaluated against `pylsl.local_clock`
and the inlet's time-corrected sample timestamp.

Validate a file without connecting to LSL:

```powershell
uv run lsl-monitor json/example.monitor.json --validate
```

## Run

Start the outlets first or later; the dashboard continues searching and connects
when they appear:

```powershell
uv run lsl-monitor json/example.monitor.json
```

The process is a read-only LSL consumer. It does not create an outlet or write a
recording. A stream is green only after samples arrive within
`inactive_after_seconds`; merely discovering an outlet is not enough.

## Development

Run all configured checks from the repository root:

```powershell
uv run ruff check .
uv run pytest -q
```

The LSL worker, configuration model, and buffer are independent of Qt so their
behavior can be tested with fake stream objects and without live hardware.
