"""Audio playback and level metering for monitored LSL channels.

The dashboard polls immutable snapshots, so listening to a stream means taking
the samples that arrived since the previous poll and queueing them for the sound
card. Everything here is plain NumPy except `AudioOutput`, which owns one
`QAudioSink` and tolerates a machine with no usable output device, so a
configuration containing an audio panel still runs where nothing can be played.
"""

from __future__ import annotations

import numpy as np
from PySide6 import QtCore, QtMultimedia

#: Level reported for digital silence, and the floor of every decibel reading.
SILENCE_DECIBELS = -90.0

#: Output rates tried in order when a device rejects the stream's own rate.
FALLBACK_OUTPUT_RATES = (48000, 44100, 32000, 22050, 16000, 8000)

#: Relative change in the input rate that forces the sink to be reopened.
RATE_TOLERANCE = 0.02


def samples_after(
    timestamps: np.ndarray, values: np.ndarray, after: float | None
) -> tuple[np.ndarray, float | None]:
    """Return the values newer than `after`, plus the newest timestamp seen.

    An `after` of `None` selects nothing and reports the live edge: playback
    starts with the next samples to arrive instead of dumping the whole history
    buffer into the sound card.
    """

    if timestamps.size == 0 or timestamps.size != values.size:
        return np.empty(0, dtype=float), after
    newest = float(timestamps[-1])
    if after is None:
        return np.empty(0, dtype=float), newest
    fresh = np.asarray(values[timestamps > after], dtype=float)
    return fresh, max(newest, after)


def decibels(amplitude: float) -> float:
    """Return an amplitude as decibels of full scale, floored at silence."""

    if not np.isfinite(amplitude) or amplitude <= 0.0:
        return SILENCE_DECIBELS
    return max(SILENCE_DECIBELS, 20.0 * float(np.log10(amplitude)))


def level_decibels(values: np.ndarray) -> tuple[float, float]:
    """Return the RMS and peak level of a block, in decibels of full scale.

    Non-finite entries are ignored, so a marker stream's not-a-number
    placeholders read as silence rather than corrupting the meter.
    """

    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    if finite.size == 0:
        return SILENCE_DECIBELS, SILENCE_DECIBELS
    rms = float(np.sqrt(np.mean(np.square(finite))))
    peak = float(np.max(np.abs(finite)))
    return decibels(rms), decibels(peak)


def resampled(values: np.ndarray, source_rate: float, target_rate: float) -> np.ndarray:
    """Linearly resample one block, preserving the real time it covers.

    Playback has to consume samples exactly as fast as they arrive, so the block
    keeps its duration and only its sample count changes.
    """

    block = np.asarray(values, dtype=float)
    if block.size == 0 or source_rate <= 0.0 or target_rate <= 0.0:
        return np.empty(0, dtype=float)
    if block.size == 1 or abs(target_rate - source_rate) <= 1e-6:
        return block
    count = max(1, int(round(block.size * target_rate / source_rate)))
    positions = np.linspace(0.0, block.size - 1, count)
    return np.interp(positions, np.arange(block.size), block)


class AudioOutput:
    """A mono sink that resamples to a rate the output device accepts.

    The sink is created on the first block written and released by `close`, so a
    muted panel never claims the sound card.
    """

    def __init__(self, device: QtMultimedia.QAudioDevice | None = None) -> None:
        self._device = (
            QtMultimedia.QMediaDevices.defaultAudioOutput() if device is None else device
        )
        self._sink: QtMultimedia.QAudioSink | None = None
        self._buffer: QtCore.QIODevice | None = None
        self._format: QtMultimedia.QAudioFormat | None = None
        self._input_rate = 0.0
        self.dropped_samples = 0

    @property
    def available(self) -> bool:
        """Whether an output device exists to play through."""

        return not self._device.isNull()

    @property
    def description(self) -> str:
        """Name of the output device, or why there is nothing to play through."""

        return self._device.description() if self.available else "no audio output device"

    @property
    def output_rate(self) -> int:
        """Rate the device is currently being fed at, or `0` while closed."""

        return int(self._format.sampleRate()) if self._format is not None else 0

    @property
    def playing(self) -> bool:
        return self._buffer is not None

    def _supported_format(self, rate: int) -> QtMultimedia.QAudioFormat | None:
        """Return a mono format at `rate`, preferring floats over 16-bit words."""

        for sample_format in (
            QtMultimedia.QAudioFormat.SampleFormat.Float,
            QtMultimedia.QAudioFormat.SampleFormat.Int16,
        ):
            candidate = QtMultimedia.QAudioFormat()
            candidate.setChannelCount(1)
            candidate.setSampleRate(rate)
            candidate.setSampleFormat(sample_format)
            if self._device.isFormatSupported(candidate):
                return candidate
        return None

    def _choose_format(self, input_rate: float) -> QtMultimedia.QAudioFormat | None:
        """Prefer the stream's own rate, which needs no resampling at all."""

        candidates = (int(round(input_rate)), *FALLBACK_OUTPUT_RATES)
        for rate in candidates:
            if rate < 1:
                continue
            supported = self._supported_format(rate)
            if supported is not None:
                return supported
        return None

    def open(self, input_rate: float) -> bool:
        """Start the sink for `input_rate`, keeping an already suitable one."""

        if not self.available or input_rate <= 0.0:
            return False
        if self._buffer is not None and abs(
            self._input_rate - input_rate
        ) <= RATE_TOLERANCE * input_rate:
            return True
        self.close()
        chosen = self._choose_format(input_rate)
        if chosen is None:
            return False
        sink = QtMultimedia.QAudioSink(self._device, chosen)
        buffer = sink.start()
        if buffer is None:
            sink.stop()
            return False
        self._sink = sink
        self._buffer = buffer
        self._format = chosen
        self._input_rate = float(input_rate)
        return True

    def close(self) -> None:
        """Release the device, discarding whatever is still queued."""

        if self._sink is not None:
            self._sink.stop()
        self._sink = None
        self._buffer = None
        self._format = None
        self._input_rate = 0.0

    def _encoded(self, block: np.ndarray) -> bytes:
        assert self._format is not None
        if self._format.sampleFormat() == QtMultimedia.QAudioFormat.SampleFormat.Float:
            return block.astype(np.float32).tobytes()
        return (block * 32767.0).astype(np.int16).tobytes()

    def write(self, values: np.ndarray, input_rate: float) -> int:
        """Queue one block of samples, returning how many could not be played.

        Values outside full scale are clipped, and anything the device buffer has
        no room for is dropped rather than delayed, which keeps what is heard in
        step with what the panels draw.
        """

        block = np.asarray(values, dtype=float)
        if block.size == 0:
            return 0
        if not self.open(input_rate):
            self.dropped_samples += block.size
            return block.size
        assert self._sink is not None and self._buffer is not None
        payload = self._encoded(np.clip(np.nan_to_num(block), -1.0, 1.0))
        width = max(1, self._format.bytesPerFrame() if self._format else 1)
        allowed = (max(0, self._sink.bytesFree()) // width) * width
        written = max(0, self._buffer.write(payload[:allowed]))
        dropped = max(0, (len(payload) - written) // width)
        self.dropped_samples += dropped
        return dropped
