"""Audio capture for Alonarg.

Captures the microphone AND Windows system audio (WASAPI loopback) on
*separate* tracks via PyAudioWPatch, resamples both to 16 kHz mono, and writes
``mic.wav``, ``system.wav`` and a mixed ``mixed.wav`` (for playback).

The pure DSP helpers (``to_mono``, ``resample_to_16k``, ``mix_tracks``,
``write_wav``, ``read_wav``) are hardware-free and unit-tested with synthetic
numpy arrays. Device discovery and the :class:`Recorder` touch real hardware and
are deliberately robust: a missing or failing device is logged and skipped, never
fatal.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np
import pyaudiowpatch as pyaudio
import soundfile as sf

from alonarg import config

log = logging.getLogger(__name__)

# Capture format: 16-bit signed PCM. int16 -> float32 in [-1, 1] via /32768.
_SAMPLE_FORMAT = pyaudio.paInt16
_INT16_SCALE = 32768.0
_CHUNK = 1024


# ---------------------------------------------------------------------------
# Pure functions (no hardware) -- unit tested with synthetic arrays.
# ---------------------------------------------------------------------------
def to_mono(samples: np.ndarray) -> np.ndarray:
    """Collapse multi-channel audio to mono.

    For a 2-D array shaped ``(frames, channels)`` the channels are averaged.
    A 1-D array passes through unchanged. The result is always ``float32``.
    """
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim >= 2:
        arr = arr.mean(axis=1)
    return arr.astype(np.float32, copy=False)


def resample_to_16k(samples: np.ndarray, src_rate: int) -> np.ndarray:
    """Resample a mono signal to 16 kHz via linear interpolation.

    Returns ``float32``. When ``src_rate == 16000`` the input is returned as a
    ``float32`` view (identity). Output length is approximately
    ``len(samples) * 16000 / src_rate``.
    """
    arr = np.asarray(samples, dtype=np.float32)
    target_rate = config.SAMPLE_RATE
    if src_rate == target_rate:
        return arr.astype(np.float32, copy=False)
    n_src = arr.shape[0]
    if n_src == 0:
        return arr.astype(np.float32, copy=False)
    n_dst = int(round(n_src * target_rate / src_rate))
    if n_dst <= 0:
        return np.zeros(0, dtype=np.float32)
    # Sample positions in the source timeline for each output sample.
    src_idx = np.arange(n_src, dtype=np.float64)
    dst_idx = np.linspace(0, n_src - 1, n_dst, dtype=np.float64)
    out = np.interp(dst_idx, src_idx, arr)
    return out.astype(np.float32, copy=False)


def mix_tracks(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Sum two mono tracks, zero-padding the shorter to the longer length.

    The summed signal is clipped to ``[-1, 1]``. Empty inputs are handled. The
    result is ``float32``.
    """
    arr_a = np.asarray(a, dtype=np.float32).reshape(-1)
    arr_b = np.asarray(b, dtype=np.float32).reshape(-1)
    n = max(arr_a.shape[0], arr_b.shape[0])
    if n == 0:
        return np.zeros(0, dtype=np.float32)
    out = np.zeros(n, dtype=np.float32)
    out[: arr_a.shape[0]] += arr_a
    out[: arr_b.shape[0]] += arr_b
    np.clip(out, -1.0, 1.0, out=out)
    return out.astype(np.float32, copy=False)


def write_wav(path, samples: np.ndarray, rate: int = config.SAMPLE_RATE) -> None:
    """Write a mono ``float32`` signal as 16-bit PCM WAV via soundfile."""
    arr = np.asarray(samples, dtype=np.float32).reshape(-1)
    sf.write(str(path), arr, int(rate), subtype="PCM_16")


def read_wav(path) -> tuple[np.ndarray, int]:
    """Read a WAV file, returning ``(float32 mono samples, sample_rate)``."""
    data, rate = sf.read(str(path), dtype="float32", always_2d=False)
    return to_mono(data), int(rate)


# ---------------------------------------------------------------------------
# Device discovery.
# ---------------------------------------------------------------------------
def find_devices() -> dict:
    """Discover the default mic and the default WASAPI loopback device.

    Returns ``{"mic": <device info dict | None>, "loopback": <device info dict |
    None>}``. Never raises -- any part that cannot be resolved is ``None``.
    """
    result: dict = {"mic": None, "loopback": None}
    p = None
    try:
        p = pyaudio.PyAudio()
        try:
            mic = p.get_default_input_device_info()
            if mic and int(mic.get("maxInputChannels", 0)) > 0:
                result["mic"] = mic
        except Exception as exc:  # noqa: BLE001
            log.warning("No default input (mic) device: %s", exc)
        try:
            result["loopback"] = p.get_default_wasapi_loopback()
        except Exception as exc:  # noqa: BLE001
            log.warning("No default WASAPI loopback device: %s", exc)
    except Exception as exc:  # noqa: BLE001
        log.warning("Could not initialize PyAudio for device discovery: %s", exc)
    finally:
        if p is not None:
            try:
                p.terminate()
            except Exception:  # noqa: BLE001
                pass
    return result


# ---------------------------------------------------------------------------
# Recorder.
# ---------------------------------------------------------------------------
class _Track:
    """One capture track: a callback-driven PyAudio stream and its frames.

    We use PyAudio's callback mode rather than a blocking ``read()`` loop:
    PyAudio delivers frames on its own internal thread, and ``stop_stream()`` /
    ``close()`` safely tear that thread down. This avoids the access violation
    that occurs when a stream is closed from the main thread while a blocking
    ``read()`` is in flight on a reader thread (common with WASAPI loopback when
    no audio is playing).
    """

    def __init__(self, name: str, device_info: dict, sample_format=_SAMPLE_FORMAT):
        self.name = name
        self.device_info = device_info
        self.rate = int(round(float(device_info["defaultSampleRate"])))
        self.channels = max(1, int(device_info.get("maxInputChannels", 1)))
        self.device_index = int(device_info["index"])
        self.sample_format = sample_format
        self.stream = None
        self._frames: list[bytes] = []
        self._lock = threading.Lock()

    def _callback(self, in_data, frame_count, time_info, status):
        if in_data:
            with self._lock:
                self._frames.append(in_data)
        return (None, pyaudio.paContinue)

    def open(self, p: pyaudio.PyAudio) -> None:
        self.stream = p.open(
            format=self.sample_format,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=self.device_index,
            frames_per_buffer=_CHUNK,
            stream_callback=self._callback,
        )

    def start(self) -> None:
        if self.stream is not None and not self.stream.is_active():
            self.stream.start_stream()

    def close(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop_stream()
            except Exception:  # noqa: BLE001
                pass
            try:
                self.stream.close()
            except Exception:  # noqa: BLE001
                pass
            self.stream = None

    def to_float32_mono_16k(self) -> np.ndarray:
        """Convert accumulated raw frames -> mono float32 resampled to 16 kHz."""
        with self._lock:
            raw = b"".join(self._frames)
        if not raw:
            return np.zeros(0, dtype=np.float32)
        ints = np.frombuffer(raw, dtype="<i2").astype(np.float32) / _INT16_SCALE
        if self.channels > 1:
            usable = (ints.shape[0] // self.channels) * self.channels
            ints = ints[:usable].reshape(-1, self.channels)
        mono = to_mono(ints)
        return resample_to_16k(mono, self.rate)

    @property
    def has_data(self) -> bool:
        with self._lock:
            return any(len(f) for f in self._frames)


class Recorder:
    """Records mic + system loopback on separate callback-driven streams."""

    def __init__(self, sample_rate: int = config.SAMPLE_RATE):
        self.sample_rate = sample_rate
        self._pa: pyaudio.PyAudio | None = None
        self._tracks: dict[str, _Track] = {}
        self._recording = False
        self._start_time: float | None = None
        self._stop_time: float | None = None

    # -- properties -------------------------------------------------------
    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def elapsed_s(self) -> float:
        if not self._recording or self._start_time is None:
            return 0.0
        return max(0.0, time.monotonic() - self._start_time)

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self._recording:
            raise RuntimeError("Recorder is already recording")

        self._tracks = {}
        self._stop_time = None

        devices = find_devices()
        specs = [("mic", devices.get("mic")), ("system", devices.get("loopback"))]

        try:
            self._pa = pyaudio.PyAudio()
        except Exception as exc:  # noqa: BLE001
            log.error("Could not initialize PyAudio: %s", exc)
            self._pa = None

        if self._pa is not None:
            for name, info in specs:
                if not info:
                    log.warning("No %s device available; skipping that track.", name)
                    continue
                track = _Track(name, info)
                try:
                    track.open(self._pa)
                except Exception as exc:  # noqa: BLE001
                    log.warning("Failed to open %s stream (%s); skipping.", name, exc)
                    track.close()
                    continue
                self._tracks[name] = track

        # Start the callback streams for whatever opened successfully.
        for track in self._tracks.values():
            track.start()

        self._start_time = time.monotonic()
        self._recording = True
        if not self._tracks:
            log.warning("Recording started with NO available tracks (silent output).")

    def stop(self, out_dir) -> "RecordingResult":
        from alonarg.types import RecordingResult

        if not self._recording:
            raise RuntimeError("Recorder is not recording")

        self._stop_time = time.monotonic()
        elapsed = max(0.0, self._stop_time - (self._start_time or self._stop_time))
        self._recording = False

        # Stop and close each callback stream (PyAudio tears down its callback
        # thread synchronously inside stop_stream/close).
        for track in self._tracks.values():
            track.close()

        if self._pa is not None:
            try:
                self._pa.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._pa = None

        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        mic_track = self._tracks.get("mic")
        sys_track = self._tracks.get("system")

        mic16k = self._finalize_track(mic_track, out_path / "mic.wav")
        sys16k = self._finalize_track(sys_track, out_path / "system.wav")
        mic_path = str(out_path / "mic.wav") if mic16k is not None else None
        system_path = str(out_path / "system.wav") if sys16k is not None else None

        mic_arr = mic16k if mic16k is not None else np.zeros(0, dtype=np.float32)
        sys_arr = sys16k if sys16k is not None else np.zeros(0, dtype=np.float32)
        mixed = mix_tracks(mic_arr, sys_arr)
        if mixed.shape[0] == 0:
            # Neither track produced data -> write a short silent file so the
            # mixed_path always exists and is playable.
            mixed = np.zeros(int(self.sample_rate * 0.1), dtype=np.float32)
        mixed_path = out_path / "mixed.wav"
        write_wav(mixed_path, mixed, config.SAMPLE_RATE)

        # Duration = longest captured track, falling back to wall-clock elapsed.
        track_durations = [
            arr.shape[0] / config.SAMPLE_RATE
            for arr in (mic16k, sys16k)
            if arr is not None and arr.shape[0] > 0
        ]
        duration_s = max(track_durations) if track_durations else elapsed

        return RecordingResult(
            mixed_path=str(mixed_path),
            duration_s=float(duration_s),
            sample_rate=config.SAMPLE_RATE,
            mic_path=mic_path,
            system_path=system_path,
        )

    # -- helpers ----------------------------------------------------------
    def _finalize_track(self, track: "_Track | None", path: Path) -> "np.ndarray | None":
        """Convert/write a captured track. Returns the 16 kHz mono samples, or
        None if the track is absent or captured nothing."""
        if track is None or not track.has_data:
            return None
        try:
            samples = track.to_float32_mono_16k()
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to process %s track: %s", track.name, exc)
            return None
        if samples.shape[0] == 0:
            return None
        try:
            write_wav(path, samples, config.SAMPLE_RATE)
        except Exception as exc:  # noqa: BLE001
            log.warning("Failed to write %s: %s", path, exc)
            return None
        return samples
