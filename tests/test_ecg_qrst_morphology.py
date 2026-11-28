"""Regression tests for conservative single-lead QRST morphology screening."""

import numpy as np
import pytest

from ecg_receiver.core.ecg_demo import fill_demo_buffer
from ecg_receiver.core.ecg_signal import compute_ecg_metrics


def _simple_qrst_ecg(
    seconds: float = 12.0,
    bpm: float = 70.0,
    sample_rate: float = 500.0,
    amplitude_uv: float = 1000.0,
    qrs_sigma_sec: float = 0.018,
    st_shift_uv: float = 0.0,
    t_amplitude_uv: float = 280.0,
    seed: int = 1,
) -> np.ndarray:
    n = int(seconds * sample_rate)
    t = np.arange(n, dtype=np.float64) / sample_rate
    rr = 60.0 / bpm
    signal = np.zeros(n, dtype=np.float64)
    for beat in np.arange(0.2, seconds, rr):
        signal += amplitude_uv * np.exp(-((t - beat) / qrs_sigma_sec) ** 2)
        signal += 0.12 * amplitude_uv * np.exp(-((t - (beat - 0.16)) / 0.025) ** 2)
        if st_shift_uv:
            signal += st_shift_uv * np.exp(-((t - (beat + 0.10)) / 0.07) ** 2)
        signal += t_amplitude_uv * np.exp(-((t - (beat + 0.23)) / 0.055) ** 2)
    rng = np.random.default_rng(seed)
    signal += rng.normal(0.0, amplitude_uv * 0.008, n)
    return signal


def test_normal_qrst_morphology_is_normal():
    metrics = compute_ecg_metrics(_simple_qrst_ecg(), 500.0)

    assert metrics["qrst_status"] == "normal"
    assert metrics["qrst_confidence"] == "high"
    assert metrics["qrst_scope"] == "full"
    assert metrics["qrs_duration_ms"] < 110.0
    assert metrics["t_wave_polarity"] == "concordant"
    assert metrics["qrst_findings"] == []


def test_wide_qrs_is_flagged_as_abnormal():
    metrics = compute_ecg_metrics(
        _simple_qrst_ecg(qrs_sigma_sec=0.055), 500.0
    )

    assert metrics["qrst_status"] == "abnormal"
    assert "wide_qrs" in metrics["qrst_findings"]
    assert metrics["qrs_duration_ms"] >= 120.0


def test_st_shift_is_flagged_as_abnormal():
    metrics = compute_ecg_metrics(_simple_qrst_ecg(st_shift_uv=150.0), 500.0)

    assert metrics["qrst_status"] == "abnormal"
    assert "st_positive_deviation" in metrics["qrst_findings"]
    assert metrics["st_deviation_uv"] >= 100.0


def test_discordant_t_wave_is_borderline_not_diagnostic():
    metrics = compute_ecg_metrics(_simple_qrst_ecg(t_amplitude_uv=-280.0), 500.0)

    assert metrics["qrst_status"] == "borderline"
    assert "t_wave_discordance" in metrics["qrst_findings"]
    assert metrics["t_wave_polarity"] == "discordant"


@pytest.mark.parametrize("bpm", [120.0, 140.0])
def test_tachycardic_clean_demo_does_not_false_positive_t_wave(bpm):
    np.random.seed(100 + int(bpm))
    signal = np.asarray(fill_demo_buffer(12.0, bpm=bpm, sample_rate=500.0))
    metrics = compute_ecg_metrics(signal, 500.0)

    assert metrics["heart_rate_method"] == "qrs_peaks"
    assert metrics["qrst_status"] in {"normal", "unavailable"}
    assert "t_wave_discordance" not in metrics["qrst_findings"]
    assert metrics["t_wave_polarity"] in {"uncertain", "concordant", "mixed"}
    assert metrics["qrst_scope"] in {"limited", "unavailable"}
