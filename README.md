# LSL Monitor

LSL Monitor is a JSON-configured desktop dashboard for checking signal quality
during a Lab Streaming Layer recording. It discovers every configured stream,
reconnects when an outlet disappears, and uses corrected LSL timestamps for the
display timeline and active/inactive decisions.

The process is a **read-only LSL consumer**. It never creates an outlet and never
writes a recording, so it is safe to start and stop at any point during an
experiment.

- [Install](#install)
- [Quick start (no hardware)](#quick-start-no-hardware)
- [Monitor your own streams](#monitor-your-own-streams)
- [Panel types](#panel-types)
- [Configuration reference](#configuration-reference)
- [Visual layout designer](#visual-layout-designer)
- [Troubleshooting](#troubleshooting)
- [Development](#development)

## Install

Python 3.10 or newer is required. From the repository root:

```powershell
uv sync --all-groups
```

This creates `.venv`, installs the application and development tools, and uses
the committed `uv.lock` for reproducible versions. Activating the environment is
optional because every command below runs through `uv`.

## Quick start (no hardware)

Two ways to see the dashboard without any real device.

**1. Live mock experiment.** The repository ships a mock producer for
[json/experiment_monitor_full.json](json/experiment_monitor_full.json), which
publishes all eight outlets that configuration expects: EMG, IMU, cursor
position, three camera-health signals, and two marker streams.

In one terminal:

```powershell
uv run lsl-monitor-demo
```

In a second terminal:

```powershell
uv run lsl-monitor json/experiment_monitor_full.json
```

The traces and 2D cursor animate continuously, and both marker panels receive
named events. By default camera 2 pauses for four seconds during every
16-second cycle: because the monitor marks a stream inactive after two seconds,
that health panel turns red and then returns to green when samples resume — a
live demonstration of the dropout feedback.

| Demo option | Effect |
| --- | --- |
| `--fault-cycle-seconds 0` | Publish continuously, no simulated dropouts |
| `--fault-cycle-seconds 30` | Change the dropout period (default `16`) |
| `--duration 10` | Stop after 10 seconds, for a timed smoke test |

Run both commands from the repository root, and see
[Troubleshooting](#troubleshooting) if the panels stay red. The
equivalent checkout-local launcher is
`uv run python scripts/run_mock_experiment.py`.

**2. Layout designer.** Build and preview a layout against generated mock
signals, with no LSL traffic at all:

```powershell
uv run lsl-monitor --designer
```

See [Visual layout designer](#visual-layout-designer).

## Monitor your own streams

1. Copy [json/example.monitor.json](json/example.monitor.json) to your own
   `*.monitor.json` file.
2. Edit its `streams` so each entry matches one of your LSL outlets — see the
   [configuration reference](#configuration-reference), or build the file
   visually in the [designer](#visual-layout-designer).
3. Check the file before going near the hardware:

   ```powershell
   uv run lsl-monitor my_experiment.monitor.json --validate
   ```

   This validates against the schema and prints the stream count without
   starting the GUI or touching LSL.
4. Run it:

   ```powershell
   uv run lsl-monitor my_experiment.monitor.json
   ```

Order does not matter: start the outlets before or after the dashboard, which
keeps searching and connects when they appear. A stream turns green only after
samples arrive within `inactive_after_seconds` — merely discovering an outlet is
not enough.

### Command reference

```text
lsl-monitor [config] [--validate] [--designer]
```

| Argument | Meaning |
| --- | --- |
| `config` | Path to a monitor JSON file. Defaults to `json/example.monitor.json` |
| `--validate` | Validate and report, then exit without starting the GUI |
| `--designer` | Open the visual designer instead of monitoring; `config` preloads a file to edit |

Exit code `2` means the configuration failed to load or validate; the reason is
printed to stderr.

## Panel types

Set the panel kind with a view's `type`.

| `type` | Shows |
| --- | --- |
| `traces` | Selected channels over time, as vertically normalized stacked lanes or raw overlays |
| `plane_2d` | One channel against another as a fading trajectory; opacity increases toward the latest sample |
| `psd` | A live windowed power spectral density |
| `spectrogram` | One channel's short-time spectrum as a scrolling heat map: time across, frequency up |
| `audio` | A level meter per selected channel, and playback of one of them through the sound card |
| `alive` | A large green/red indicator with the age of the latest sample in LSL time |
| `markers` | A rolling list of named events, newest first |

Trace lanes are individually normalized in `stacked` alignment, which keeps every
selected signal visible even when their amplitudes differ. `overlay` draws them
together in raw units.

`psd` and `spectrogram` panels carry a frequency range under the plot, so a band
can be narrowed while the recording runs; `Full band` gives the axis back to the
stream. A stream carries nothing above half its sample rate, so the boxes stop
there and a wider request is pulled back to it. `frequency_range` sets the band a
panel opens on, and in the designer the two are the same value: changing one
updates the other.

A `spectrogram` reads one signal at a time: it draws the first channel it is
given and names it under the plot, so select that channel with `channels`.
`fft_size` trades time resolution for frequency resolution, and
`dynamic_range_db` is measured down from the loudest bin in view, so lowering it
raises the contrast.

`markers` panels prefer real marker samples (string-format outlets); for a
numeric stream they fall back to reading steps to a non-zero value on the
selected trigger channel, so a hardware trigger line can be read as events.

### Listening to a stream

An `audio` panel meters every channel it is given — RMS and peak in decibels of
full scale, with a peak that holds and decays — and plays one of them, picked in
the panel. Samples are treated as full scale at ±1, so `audio_gain` scales both
the meters and the output; anything past full scale is clipped and the bar turns
red.

Playback opens **muted**: press `Listen`, or set `"audio_muted": false` to start
with the dashboard. The samples are played at the rate they arrive at, resampled
to whatever the sound card accepts, so a real `Audio` outlet sounds like itself.
A low-rate signal such as EMG at 200 Hz is mostly below hearing, and its meter
still works. Whatever cannot be queued in time is dropped rather than delayed,
which keeps what is heard in step with what the panels draw; the panel reports
the count. Metering runs with or without an output device, and a machine with no
sound card says so instead of failing.

Playing a stream reads the same samples the plots use — the monitor still creates
no outlet and writes no recording.

Every panel header carries the stream's activity dot, the panel title (the view
type unless `title` overrides it), and the configured stream `id`, so each plot
names the stream it belongs to even when several streams are monitored side by
side. Panels share the available window on a responsive freeform canvas.

## Configuration reference

The contract is [schemas/lsl-monitor.schema.json](schemas/lsl-monitor.schema.json),
a JSON Schema Draft 2020-12 document. Keep the `$schema` entry at the top of your
file and editors such as VS Code will offer completion and inline validation.

A minimal configuration:

```json
{
  "$schema": "./schemas/lsl-monitor.schema.json",
  "window": { "title": "My monitor", "history_seconds": 10 },
  "streams": [
    {
      "id": "emg",
      "match": { "identity": "EMG", "type": "EMG" },
      "channels": [
        { "index": 0, "label": "Left", "color": "#5eead4" },
        { "index": 1, "label": "Right", "color": "#60a5fa" }
      ],
      "views": [
        { "type": "traces", "alignment": "stacked" },
        { "type": "alive" }
      ]
    }
  ]
}
```

### `window`

All fields are optional.

| Field | Default | Meaning |
| --- | --- | --- |
| `title` | `"LSL Monitor"` | Window caption |
| `history_seconds` | `10` | Length of the visible time axis (max `3600`) |
| `refresh_hz` | `20` | Redraw rate, `1`–`120` |
| `columns` | `2` | Grid columns used for panels that have no explicit `layout`, `1`–`8` |
| `inactive_after_seconds` | `2` | Age at which a stream turns red, `0`–`60` |
| `max_points_per_channel` | `100000` | Per-channel ring-buffer capacity |

`inactive_after_seconds` is evaluated against `pylsl.local_clock` and the inlet's
time-corrected sample timestamp, not wall-clock arrival time.

### `streams[]`

Each stream requires `id`, `match`, `channels`, and `views`.

- **`id`** — an internal dashboard identifier shown in every panel header. It
  must be unique within the file and must start with a letter, but it does not
  need to equal any LSL field.
- **`match`** — one or more LSL outlet fields; at least one is required.
  - `identity`: the shared value expected in **both** the outlet's
    human-readable `name` and its stable producer `source_id`. This is the
    recommended rule and is independent of the dashboard `id`.
  - `type`: the advertised content type, e.g. `EEG`, `Signals`, `Markers`.
  - `hostname`: optional exact LSL host, to disambiguate identical outlets on
    different computers.
  - Legacy rules `name`, `source_id`, and `name_regex` remain supported.

  If an outlet appends its computer name to its identity, the monitor selects the
  closest suffix match automatically. When equally close outlets exist on
  multiple computers, the running monitor asks which full outlet belongs to this
  experiment, so the JSON stays portable between machines.
- **`channels`** — the channels to select, each by *either* zero-based `index`
  *or* exact LSL metadata `name` (not both). Optional `label` overrides the
  displayed name, and `color` is a `#rrggbb` hex string. This array's order is
  the default draw order for every panel of the stream.
- **`views`** — the panels for this stream, at least one.

### `views[]`

`type` is required; everything else is optional. Each option below applies to one
panel type only, except `title`, `channels`, `layout`, and `status_dot`, which
apply everywhere.

| Field | Applies to | Default | Meaning |
| --- | --- | --- | --- |
| `title` | all | the `type` | Panel heading |
| `channels` | all | every selected channel | Subset to draw, by raw index, metadata name, or display label. Also sets this panel's draw order |
| `status_dot` | all | `true` | Show the green/red activity dot in the title row |
| `layout` | all | auto grid | Responsive rectangle, see below |
| `alignment` | `traces` | `"stacked"` | `stacked` (per-lane normalized) or `overlay` (raw units) |
| `fft_size` | `psd`, `spectrogram` | `1024` | FFT window length, `16`–`1048576` |
| `frequency_range` | `psd`, `spectrogram` | full band | `[low, high]` in Hz; `high` must exceed `low` |
| `trail_seconds` | `plane_2d` | `history_seconds` | Trajectory length |
| `spectrogram_seconds` | `spectrogram` | `history_seconds` | Time axis length |
| `dynamic_range_db` | `spectrogram` | `60` | Decibels below the loudest bin that are still colored, `6`–`120` |
| `colormap` | `spectrogram` | `"viridis"` | `viridis`, `magma`, `inferno`, `plasma`, `cividis`, or `turbo` |
| `audio_gain` | `audio` | `1` | Factor applied to the meters and to playback, `0`–`100` |
| `audio_muted` | `audio` | `true` | Open muted; `false` plays as soon as samples arrive |
| `level_seconds` | `audio` | `0.4` | History each level meter integrates, `0.05`–`10` |
| `marker_seconds` | `markers` | `history_seconds` | Rolling window length |
| `marker_limit` | `markers` | `48` | Rows drawn; older events are counted only, `1`–`500` |

`plane_2d` requires exactly two channels, either selected explicitly or by the
stream having exactly two.

### `layout`

`layout` places a panel on a freeform canvas using fractions of the window, so
the composition scales with any monitor size:

```json
{ "x": 0, "y": 0, "width": 0.7, "height": 1 }
```

That gives a panel the left 70% of the window. `x + width` and `y + height` must
not exceed `1`. Views without `layout` are arranged automatically using
`window.columns`.

## Visual layout designer

Open the designer without connecting any LSL hardware:

```powershell
uv run lsl-monitor --designer
```

To edit an existing configuration:

```powershell
uv run lsl-monitor json/example.monitor.json --designer
```

The left side creates mock streams, channel labels and colors, and view panels.
The right side is a live preview using generated mock signals. Drag a panel by
its title and resize it using the lower-right handle. Placement and size are
stored as percentages, so the composition scales with the monitor window.
Numeric percentage controls and a per-panel automatic-layout reset are also
available. The designer supports traces, 2D planes, PSD plots, spectrograms,
audio monitors, marker rolls, and active-state panels. Panel arguments appear
for the type that uses them, and the preview panels themselves stay live: the
frequency range set inside a `psd` or `spectrogram` preview is written back to
the configuration, and audio previews stay muted until asked otherwise.

**Fit to Screen** arranges all panels across the existing window and
proportionally stretches the existing column widths, row heights, positions, and
gaps until the layout reaches every window edge. Nested groups are fitted
recursively, so a short row inside one column can fill that column without
changing the proportions of neighboring columns.

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

**LSL name / source ID** is one shared designer field. Its value is written to
the single JSON `identity` property, which requires the outlet to advertise that
value as both its LSL `name` and `source_id`. The monitor ID remains separate.

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
on `identity` alone.

Use **Save as** to export a schema-valid `.monitor.json`. Mock waveforms are
preview-only and are not written to the configuration: the exported stream match
fields and channel indices are used to connect the same layout to real LSL
outlets later.

## Troubleshooting

**Panels never appear / streams stay red.** The dashboard is discovering but not
receiving. Confirm the producer is actually pushing samples, then check that
`match` agrees with what the outlet advertises — `identity` must appear in both
the outlet `name` and its `source_id`. Widen the rule by dropping `type` to test.

**Same-PC streams are not found on Windows.** The repository-level
[lsl_api.cfg](lsl_api.cfg) keeps LSL on IPv4 and adds the portable loopback
address as a known peer, which makes same-PC demo outlets connect reliably while
retaining normal multicast discovery for streams hosted by other PCs. **Run both
the producer and the monitor from the repository root** so liblsl loads this
configuration.

**The monitor asks which outlet to use.** Two or more outlets are equally close
matches on different computers. Pick the one belonging to this experiment, or add
`"hostname"` to that stream's `match` to make the choice automatic.

**`Channel 'X' was not found`.** A `channels` entry used a metadata `name` the
connected outlet does not advertise; the error lists the labels that are
available. Either fix the spelling or select by `index` instead.

**`Channel index N is outside connected stream`.** The configuration selects a
higher index than the outlet has channels.

**Config errors on startup.** Run with `--validate` for the full list; each line
names the JSON path that failed.

**A stream flickers red.** `inactive_after_seconds` is shorter than the real gap
between samples. Raise it for slow or bursty streams, such as low-rate cameras
and marker outlets.

## Development

Run all configured checks from the repository root:

```powershell
uv run ruff check .
uv run pytest -q
```

The LSL worker, configuration model, and buffer are independent of Qt, so their
behavior can be tested with fake stream objects and without live hardware.
