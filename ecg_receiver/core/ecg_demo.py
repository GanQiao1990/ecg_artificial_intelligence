"""Synthetic ECG waveform for UI testing without hardware."""

from __future__ import annotations

import math
from typing import List

import numpy as np

from .ecg_signal import DEFAULT_SAMPLE_RATE


def generate_medical_demo_chunk(
    bpm: float = 72.0,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
    seconds: float = 0.2,
    amplitude_uv: float = 1200.0,
    phase: float = 0.0,
) -> List[float]:
    """
    Generate one chunk of realistic single-lead ECG in microvolts (µV).

    P–QRS–T morphology at clinical amplitude (~1.2 mV QRS).
    """
    n = max(1, int(sample_rate * seconds))
    t = (np.arange(n, dtype=np.float64) / sample_rate) + phase
    rr = 60.0 / bpm
    signal = np.zeros(n, dtype=np.float64)

    for i in range(n):
        ti = t[i]
        beat_phase = (ti % rr) / rr

        # P wave (~0.08–0.12 mV)
        p_center = 0.12
        p_width = 0.04
        if abs(beat_phase - p_center) < p_width:
            x = (beat_phase - p_center) / p_width
            signal[i] += 0.15 * amplitude_uv * math.exp(-x * x * 4)

        # QRS (~1.0–1.5 mV)
        qrs_center = 0.32
        qrs_width = 0.035
        if abs(beat_phase - qrs_center) < qrs_width:
            x = (beat_phase - qrs_center) / qrs_width
            signal[i] += amplitude_uv * math.exp(-x * x * 6)

        # T wave (~0.2–0.35 mV), suppressed vs R
        t_center = 0.55
        t_width = 0.08
        if abs(beat_phase - t_center) < t_width:
            x = (beat_phase - t_center) / t_width
            signal[i] += 0.28 * amplitude_uv * math.exp(-x * x * 3)

    noise = np.random.randn(n) * amplitude_uv * 0.012
    return (signal + noise).astype(np.float64).tolist()


def fill_demo_buffer(
    total_seconds: float = 12.0,
    bpm: float = 72.0,
    sample_rate: float = DEFAULT_SAMPLE_RATE,
) -> List[float]:
    """Build a full rolling-window demo trace."""
    chunk_sec = 0.25
    samples: List[float] = []
    phase = 0.0
    while len(samples) < int(total_seconds * sample_rate):
        chunk = generate_medical_demo_chunk(
            bpm=bpm,
            sample_rate=sample_rate,
            seconds=chunk_sec,
            phase=phase,
        )
        samples.extend(chunk)
        phase += chunk_sec
    return samples[: int(total_seconds * sample_rate)]