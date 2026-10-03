"""
Audio format handling for the live path.

The browser does not hand us what Deepgram wants. `getUserMedia` gives Float32
samples at whatever rate the device runs — commonly 48000, sometimes 44100,
occasionally 16000 — and often more than one channel. Deepgram is told exactly
one encoding and one sample rate for the life of the socket. Something has to
convert, and it has to do it *across* chunk boundaries.

Why a stateful converter rather than a function per chunk:

  - **Odd byte counts.** A 16-bit frame is two bytes. A WebSocket chunk can end
    mid-frame, so the trailing byte must be carried into the next chunk or every
    subsequent sample is byte-swapped into noise.
  - **Resampler phase.** Resampling each chunk independently restarts the
    interpolation at sample 0, which drops or repeats a fraction of a sample at
    every seam. Over a minute of speech that is audible clicking and measurable
    drift against `t_audio_in`.
  - **Channel folding.** A stereo chunk can also end mid-frame-group, so the
    carry has to be in whole interleaved frames, not bytes.

Linear interpolation is the resampler. It is not the best anti-aliasing
available, but it is honest about what it does, has no dependency beyond numpy
(already present for VAD), and is well within what a 16 kHz speech model needs.
Downsampling 48k → 16k does alias energy above 8 kHz; speech models are trained
on band-limited audio and Deepgram's own guidance is to send the device rate
when in doubt, which is why `target_rate` is configurable and the server
declares whatever it actually sends.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# 16-bit signed PCM: two bytes per sample per channel.
BYTES_PER_SAMPLE = 2
INT16_MAX = 32767


def float32_to_pcm16(samples: np.ndarray) -> bytes:
    """
    Clamp and convert Float32 [-1, 1] to little-endian int16.

    Clipping before the cast matters: numpy wraps on overflow, so a sample of
    1.2 becomes a large negative number — a loud click rather than mild
    distortion.
    """
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * INT16_MAX).astype("<i2").tobytes()


def pcm16_to_float32(data: bytes) -> np.ndarray:
    """Inverse of `float32_to_pcm16`, for tests and analysis."""
    return np.frombuffer(data, dtype="<i2").astype(np.float32) / INT16_MAX


def downmix_to_mono(frames: np.ndarray, channels: int) -> np.ndarray:
    """
    Average interleaved channels down to mono.

    Averaging rather than taking channel 0: a laptop with two mics often has
    the speaker louder on one of them, and dropping a channel throws away half
    the signal-to-noise for no benefit.
    """
    if channels <= 1:
        return frames
    usable = (frames.size // channels) * channels
    if usable == 0:
        return frames[:0]
    return frames[:usable].reshape(-1, channels).mean(axis=1)


@dataclass
class PcmStreamConverter:
    """
    Converts a stream of browser audio chunks into the PCM the server declares.

    One instance per session. Feed it chunks in order; it returns bytes ready to
    forward, holding back whatever it cannot yet convert cleanly.

    `source_rate` is what the browser reports, `target_rate` what we tell
    Deepgram. They are allowed to be equal, in which case resampling is skipped
    entirely rather than run as a no-op interpolation.
    """

    source_rate: int
    target_rate: int
    channels: int = 1
    # Carry for a chunk that ended mid-frame (raw int16 input path).
    _byte_carry: bytes = field(default=b"", init=False)
    # Last source sample of the previous chunk, so interpolation continues
    # across the seam instead of restarting.
    _last_sample: float | None = field(default=None, init=False)
    # Fractional read position kept between chunks; this is the resampler phase.
    _phase: float = field(default=0.0, init=False)
    _frames_in: int = field(default=0, init=False)
    _frames_out: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        if self.source_rate <= 0 or self.target_rate <= 0:
            raise ValueError("sample rates must be positive")
        if self.channels < 1:
            raise ValueError("channels must be at least 1")

    @property
    def ratio(self) -> float:
        return self.source_rate / self.target_rate

    @property
    def frames_in(self) -> int:
        return self._frames_in

    @property
    def frames_out(self) -> int:
        return self._frames_out

    def push_float32(self, samples: np.ndarray) -> bytes:
        """Convert a Float32 chunk (as the browser's AudioWorklet produces)."""
        mono = downmix_to_mono(np.asarray(samples, dtype=np.float32), self.channels)
        self._frames_in += int(mono.size)
        return float32_to_pcm16(self._resample(mono))

    def push_pcm16(self, data: bytes) -> bytes:
        """
        Convert a raw little-endian int16 chunk.

        This is the path used when the client already converted to PCM, which is
        what the production client does — it keeps the heavy per-sample work off
        the server's event loop.
        """
        buffer = self._byte_carry + data
        usable_len = len(buffer) - (len(buffer) % (BYTES_PER_SAMPLE * self.channels))
        self._byte_carry = buffer[usable_len:]
        if usable_len == 0:
            return b""
        interleaved = np.frombuffer(buffer[:usable_len], dtype="<i2")
        mono = downmix_to_mono(interleaved.astype(np.float32), self.channels)
        self._frames_in += int(mono.size)
        resampled = self._resample(mono / INT16_MAX)
        return float32_to_pcm16(resampled)

    def _resample(self, mono: np.ndarray) -> np.ndarray:
        if mono.size == 0:
            return mono
        if self.source_rate == self.target_rate:
            self._frames_out += int(mono.size)
            return mono

        # Prepend the previous chunk's last sample so the first output sample of
        # this chunk interpolates from real audio rather than from silence.
        if self._last_sample is None:
            source = mono
            offset = 0.0
        else:
            source = np.concatenate(([self._last_sample], mono))
            offset = 1.0

        # Output positions land on the source grid at `ratio` spacing, starting
        # from the phase left over by the previous chunk.
        start = self._phase + offset
        count = int(np.floor((source.size - 1 - start) / self.ratio)) + 1
        if count <= 0:
            self._last_sample = float(mono[-1])
            self._phase = start - offset - mono.size
            return mono[:0]

        positions = start + np.arange(count) * self.ratio
        lower = np.floor(positions).astype(np.int64)
        frac = positions - lower
        upper = np.minimum(lower + 1, source.size - 1)
        output = source[lower] * (1.0 - frac) + source[upper] * frac

        # Carry the phase: where the next output sample would have fallen,
        # expressed relative to the start of the next chunk.
        next_position = start + count * self.ratio
        self._phase = next_position - offset - mono.size
        self._last_sample = float(mono[-1])
        self._frames_out += int(output.size)
        return output

    def flush(self) -> bytes:
        """Drop any partial frame. Called when the stream ends."""
        self._byte_carry = b""
        return b""

    def describe(self) -> dict:
        """What was actually done to the audio — for the log, not for guessing."""
        return {
            "source_rate": self.source_rate,
            "target_rate": self.target_rate,
            "channels": self.channels,
            "resampled": self.source_rate != self.target_rate,
            "method": "linear-interpolation"
            if self.source_rate != self.target_rate
            else "none",
            "frames_in": self._frames_in,
            "frames_out": self._frames_out,
        }


def pcm16_duration_ms(data: bytes, sample_rate: int, channels: int = 1) -> float:
    """Duration of a PCM16 buffer in milliseconds."""
    frames = len(data) / (BYTES_PER_SAMPLE * max(1, channels))
    return (frames / sample_rate) * 1000.0


def silence_pcm16(duration_ms: float, sample_rate: int) -> bytes:
    """Silence of a given length — test fixtures and the text lane's no-op."""
    frames = int((duration_ms / 1000.0) * sample_rate)
    return b"\x00\x00" * max(0, frames)
