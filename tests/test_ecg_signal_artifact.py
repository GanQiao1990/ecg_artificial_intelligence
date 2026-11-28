"""Regression tests for artifact-aware heart-rate estimation.

The synthetic artifact case reproduces the measured failure mode seen in the
2026-11-21/22 serial captures: a narrow periodic impulse train near 3.8-3.9 Hz
dominates the QRS band while the underlying cardiac repetition is near 1 Hz.
No patient measurement data are stored in the test suite.
"""

import numpy as np
import pytest

from ecg_receiver.core.ecg_demo import fill_demo_buffer
from ecg_receiver.core.ecg_signal import compute_ecg_metrics


def _artifact_contaminated_ecg(
    bpm: float = 58.0,
    seconds: float = 10.0,
    sample_rate: float = 500.0,
    artifact_hz: float = 3.85,
    seed: int = 7,
) -> np.ndarray:
    # Scale the normal demo morphology down so the impulse artifact dominates
    # the sharp-edge band, as it did in the real COM3 captures.
    np.random.seed(seed)
    signal = np.asarray(
        fill_demo_buffer(seconds, bpm=bpm, sample_rate=sample_rate),
        dtype=np.float64,
    ) * 0.02

    rng = np.random.default_rng(seed)
    phase = rng.uniform(0.0, 1.0 / artifact_hz)
    artifact_times = np.arange(phase, seconds, 1.0 / artifact_hz)
    artifact_idx = np.round(artifact_times * sample_rate).astype(int)
    artifact_idx = artifact_idx[(artifact_idx >= 0) & (artifact_idx < signal.size)]
    signal[artifact_idx] += 170.0 * (0.9 + 0.2 * rng.random(artifact_idx.size))
    signal += rng.normal(0.0, 3.0, signal.size)
    return signal


@pytest.mark.parametrize("bpm", [50.0, 70.0, 100.0, 140.0])
def test_clean_demo_rates_remain_qrs_peak_based(bpm):
    np.random.seed(100 + int(bpm))
    signal = np.asarray(fill_demo_buffer(12.0, bpm=bpm, sample_rate=500.0))
    metrics = compute_ecg_metrics(signal, 500.0)

    assert metrics["heart_rate_method"] == "qrs_peaks"
    assert metrics["heart_rate_bpm"] == pytest.approx(bpm, abs=1.5)
    assert metrics["rhythm_label"] == "Regular"


def test_periodic_impulse_artifact_uses_autocorrelation_fallback():
    signal = _artifact_contaminated_ecg(bpm=58.0, seconds=10.0)
    metrics = compute_ecg_metrics(signal, 500.0)

    assert metrics["heart_rate_method"] == "slow_component"
    assert metrics["heart_rate_method_detail"] == "acf"
    assert metrics["heart_rate_bpm"] == pytest.approx(58.0, abs=1.5)
    assert metrics["artifact_period_sec"] == pytest.approx(1.0 / 3.85, abs=0.02)
    assert metrics["heart_rate_confidence"] == "high"
    assert metrics["rhythm_label"] == "Undetermined"
    assert metrics["rr_variability"] is None
    assert metrics["signal_quality_label"] in {"Fair", "Poor"}


def test_artifact_fallback_is_stable_across_adjacent_windows():
    signal = _artifact_contaminated_ecg(bpm=58.0, seconds=20.0)
    first = compute_ecg_metrics(signal[:5000], 500.0)
    second = compute_ecg_metrics(signal[5000:10000], 500.0)

    assert first["heart_rate_method"] == "slow_component"
    assert second["heart_rate_method"] == "slow_component"
    assert first["heart_rate_bpm"] == pytest.approx(58.0, abs=1.5)
    assert second["heart_rate_bpm"] == pytest.approx(58.0, abs=1.5)
    assert abs(first["heart_rate_bpm"] - second["heart_rate_bpm"]) < 0.5


def test_conflicting_device_hr_does_not_override_artifact_correction():
    signal = _artifact_contaminated_ecg(bpm=58.0, seconds=10.0, seed=11)
    metrics = compute_ecg_metrics(signal, 500.0, device_hr_bpm=115.0)

    assert metrics["heart_rate_method"] == "slow_component"
    assert metrics["heart_rate_bpm"] == pytest.approx(58.0, abs=1.5)
    assert metrics["heart_rate_source"] == "computed"
    assert metrics["device_hr_bpm"] == pytest.approx(115.0)
