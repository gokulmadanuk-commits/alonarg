"""Tests for alonarg.audio_capture.

Unit tests exercise the pure DSP helpers with synthetic numpy arrays (no
hardware). The integration test does a real ~1s capture and is skipped cleanly
if no audio devices are present.
"""
from __future__ import annotations

import os
import time

import numpy as np
import pytest

from alonarg.audio_capture import (
    Recorder,
    find_devices,
    mix_tracks,
    read_wav,
    resample_to_16k,
    to_mono,
    write_wav,
)


# ---------------------------------------------------------------------------
# to_mono
# ---------------------------------------------------------------------------
def test_to_mono_averages_stereo():
    stereo = np.array([[0.0, 1.0], [0.5, 0.5], [-1.0, 1.0]], dtype=np.float32)
    mono = to_mono(stereo)
    assert mono.shape == (3,)
    np.testing.assert_allclose(mono, [0.5, 0.5, 0.0], atol=1e-6)
    assert mono.dtype == np.float32


def test_to_mono_1d_passthrough():
    mono_in = np.array([0.1, -0.2, 0.3], dtype=np.float32)
    mono_out = to_mono(mono_in)
    assert mono_out.shape == mono_in.shape
    np.testing.assert_allclose(mono_out, mono_in, atol=1e-6)
    assert mono_out.dtype == np.float32


def test_to_mono_dtype_float32_from_int():
    out = to_mono(np.array([[1, 3], [2, 4]], dtype=np.int16))
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, [2.0, 3.0], atol=1e-6)


# ---------------------------------------------------------------------------
# resample_to_16k
# ---------------------------------------------------------------------------
def test_resample_48k_to_16k_third_length():
    src = np.zeros(48000, dtype=np.float32)
    out = resample_to_16k(src, 48000)
    assert out.dtype == np.float32
    assert abs(len(out) - 16000) <= 1


def test_resample_16k_identity():
    src = np.linspace(-1, 1, 1000).astype(np.float32)
    out = resample_to_16k(src, 16000)
    assert len(out) == len(src)
    np.testing.assert_array_equal(out, src)


def test_resample_8k_to_16k_double_length():
    src = np.zeros(8000, dtype=np.float32)
    out = resample_to_16k(src, 8000)
    assert abs(len(out) - 16000) <= 1


def test_resample_preserves_signal_shape():
    # A 100 Hz tone resampled from 48k -> 16k should remain a 100 Hz tone.
    rate = 48000
    t = np.arange(rate) / rate
    src = np.sin(2 * np.pi * 100 * t).astype(np.float32)
    out = resample_to_16k(src, rate)
    # Peaks should still be near +-1.
    assert out.max() > 0.9
    assert out.min() < -0.9


def test_resample_empty_array():
    out = resample_to_16k(np.zeros(0, dtype=np.float32), 48000)
    assert out.dtype == np.float32
    assert len(out) == 0


# ---------------------------------------------------------------------------
# mix_tracks
# ---------------------------------------------------------------------------
def test_mix_different_lengths_zero_padded():
    a = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    b = np.array([0.1, 0.1], dtype=np.float32)
    out = mix_tracks(a, b)
    assert len(out) == 4
    np.testing.assert_allclose(out, [0.2, 0.3, 0.3, 0.4], atol=1e-6)
    assert out.dtype == np.float32


def test_mix_clips_to_unit_range():
    a = np.full(5, 0.8, dtype=np.float32)
    b = np.full(5, 0.8, dtype=np.float32)
    out = mix_tracks(a, b)
    # 0.8 + 0.8 = 1.6 -> clipped to 1.0, not 1.6.
    np.testing.assert_allclose(out, 1.0, atol=1e-6)
    assert out.max() <= 1.0
    a_neg = np.full(3, -0.9, dtype=np.float32)
    b_neg = np.full(3, -0.9, dtype=np.float32)
    out_neg = mix_tracks(a_neg, b_neg)
    np.testing.assert_allclose(out_neg, -1.0, atol=1e-6)
    assert out_neg.min() >= -1.0


def test_mix_empty_inputs():
    empty = np.zeros(0, dtype=np.float32)
    assert len(mix_tracks(empty, empty)) == 0
    a = np.array([0.5, 0.5], dtype=np.float32)
    out = mix_tracks(a, empty)
    assert len(out) == 2
    np.testing.assert_allclose(out, [0.5, 0.5], atol=1e-6)
    out2 = mix_tracks(empty, a)
    np.testing.assert_allclose(out2, [0.5, 0.5], atol=1e-6)


# ---------------------------------------------------------------------------
# write_wav / read_wav round-trip
# ---------------------------------------------------------------------------
def test_wav_round_trip(tmp_path):
    rate = 16000
    t = np.linspace(0, 1.0, rate, endpoint=False)
    data = (0.5 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    path = tmp_path / "rt.wav"
    write_wav(path, data, rate)
    assert path.exists()

    back, back_rate = read_wav(path)
    assert back_rate == 16000
    assert back.dtype == np.float32
    assert len(back) == len(data)
    # PCM16 quantization tolerance.
    np.testing.assert_allclose(back, data, atol=2e-4)


def test_wav_round_trip_default_rate(tmp_path):
    data = np.array([0.0, 0.25, -0.25, 0.5, -0.5], dtype=np.float32)
    path = tmp_path / "rt2.wav"
    write_wav(path, data)  # default rate == 16000
    back, back_rate = read_wav(path)
    assert back_rate == 16000
    assert len(back) == len(data)
    np.testing.assert_allclose(back, data, atol=2e-4)


# ---------------------------------------------------------------------------
# find_devices (no hardware required: must never raise)
# ---------------------------------------------------------------------------
def test_find_devices_never_raises_and_has_keys():
    devices = find_devices()
    assert isinstance(devices, dict)
    assert set(devices.keys()) == {"mic", "loopback"}


def test_recorder_state_before_start():
    r = Recorder()
    assert r.is_recording is False
    assert r.elapsed_s == 0.0


# ---------------------------------------------------------------------------
# Integration: real ~1s capture.
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_record_real_capture(tmp_path):
    devices = find_devices()
    if not devices.get("mic") and not devices.get("loopback"):
        pytest.skip("No mic and no loopback device available")

    r = Recorder()
    r.start()
    assert r.is_recording is True
    time.sleep(1.2)
    assert r.elapsed_s > 0
    res = r.stop(tmp_path)

    assert r.is_recording is False
    assert os.path.exists(res.mixed_path)
    assert res.sample_rate == 16000

    data, rate = read_wav(res.mixed_path)
    assert rate == 16000
    assert len(data) > 0
    assert res.duration_s > 0


@pytest.mark.integration
def test_recorder_double_start_raises(tmp_path):
    devices = find_devices()
    if not devices.get("mic") and not devices.get("loopback"):
        pytest.skip("No mic and no loopback device available")
    r = Recorder()
    r.start()
    try:
        with pytest.raises(RuntimeError):
            r.start()
    finally:
        r.stop(tmp_path)
