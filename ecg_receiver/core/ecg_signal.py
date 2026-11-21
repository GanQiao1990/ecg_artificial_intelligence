"""
Clinical ECG signal processing — sampling-rate calibration, R-peak detection, and HR.

ADS1292R-based firmware often streams at 500 Hz while older code assumed 250 Hz,
which doubles computed heart rate. This module centralises parsing, rate estimation,
and beat detection so UI and AI diagnosis share one source of truth.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

import numpy as np

from .ads1292_hardware import (
    ADS1292_SAMPLE_RATES,
    DEFAULT_SAMPLE_RATE,
    FIRMWARE_PROFILES,
    adc_counts_to_microvolts,
    expected_hr_error_ratio,
    snap_stream_rate,
    stream_rate_from_timestamp_ms,
)

# Re-export snap for callers
snap_sample_rate = snap_stream_rate

HR_MIN_BPM = 30.0
HR_MAX_BPM = 220.0
RR_MIN_SEC = 60.0 / HR_MAX_BPM
RR_MAX_SEC = 60.0 / HR_MIN_BPM
REFRACTORY_SEC = 0.42  # suppress T/P waves; allows up to ~143 BPM





def correct_sample_rate_from_hr(
    sample_rate: float,
    computed_bpm: Optional[float],
    device_bpm: Optional[float],
) -> Tuple[float, Optional[str]]:
    """
    When firmware HR and R-peak HR disagree by ~2× or ~½×, infer sample-rate error.

    Returns (corrected_rate, reason_or_none).
    """
    if not computed_bpm or not device_bpm or device_bpm <= 0:
        return sample_rate, None

    ratio = computed_bpm / device_bpm
    if 1.75 <= ratio <= 2.25:
        corrected = snap_sample_rate(sample_rate / 2.0) or sample_rate / 2.0
        return corrected, "hr_ratio_2x_high"
    if 0.40 <= ratio <= 0.60:
        corrected = snap_sample_rate(sample_rate * 2.0) or sample_rate * 2.0
        return corrected, "hr_ratio_2x_low"
    return sample_rate, None


@dataclass
class ParsedECGLine:
    """One decoded serial line."""

    ecg_value: Optional[float] = None
    device_hr_bpm: Optional[float] = None
    resp_value: Optional[float] = None
    device_timestamp_ms: Optional[float] = None
    status: Optional[str] = None
    raw_line: str = ""


@dataclass
class SampleRateTracker:
    """Estimate effective sample rate from arrival times and optional device timestamps."""

    configured_rate: float = DEFAULT_SAMPLE_RATE
    _arrival_times: Deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _timestamp_ms: Deque[float] = field(default_factory=lambda: deque(maxlen=4000))
    _sample_count: int = 0
    estimated_rate: Optional[float] = None
    timestamp_rate: Optional[float] = None

    def on_sample(self, device_timestamp_ms: Optional[float] = None) -> None:
        now = time.time()
        self._arrival_times.append(now)
        self._sample_count += 1
        if device_timestamp_ms is not None:
            self._timestamp_ms.append(float(device_timestamp_ms))
        self._refresh_estimates()

    def _refresh_estimates(self) -> None:
        self.estimated_rate = self._estimate_from_arrivals()
        self.timestamp_rate = self._estimate_from_timestamps()

    def _estimate_from_arrivals(self) -> Optional[float]:
        if len(self._arrival_times) < 200:
            return None
        t0 = self._arrival_times[0]
        t1 = self._arrival_times[-1]
        dt = t1 - t0
        if dt < 2.0:
            return None
        rate = (len(self._arrival_times) - 1) / dt
        if 50.0 <= rate <= 2000.0:
            return float(rate)
        return None

    def _estimate_from_timestamps(self) -> Optional[float]:
        if len(self._timestamp_ms) < 100:
            return None
        diffs = np.diff(np.asarray(self._timestamp_ms, dtype=np.float64))
        valid = diffs[(diffs > 0.5) & (diffs < 50.0)]
        if valid.size < 20:
            return None
        median_dt_ms = float(np.median(valid))
        if median_dt_ms <= 0:
            return None
        rate = stream_rate_from_timestamp_ms(median_dt_ms)
        if rate is not None:
            snapped = snap_stream_rate(rate)
            return snapped if snapped else float(rate)
        return None

    def effective_rate(self, prefer_auto: bool = False) -> float:
        """
        Best available sample rate for time-based metrics.

        Default uses the user-configured rate (Settings → Display) because
        auto-estimates from serial throughput can disagree with firmware
        timestamps and cause ~2× heart-rate errors.

        Set prefer_auto=True to trust timestamp/arrival estimates when stable.
        """
        if prefer_auto:
            for candidate in (self.timestamp_rate, self.estimated_rate):
                snapped = snap_sample_rate(candidate)
                if snapped:
                    return snapped
        snapped_cfg = snap_sample_rate(self.configured_rate)
        return snapped_cfg if snapped_cfg else float(DEFAULT_SAMPLE_RATE)

    def set_configured_rate(self, rate: float) -> None:
        snapped = snap_sample_rate(rate)
        if snapped:
            self.configured_rate = snapped


def parse_ecg_line(line: str) -> ParsedECGLine:
    """Parse supported serial formats into structured fields."""
    parsed = ParsedECGLine(raw_line=line)
    text = (line or "").strip()
    if not text:
        return parsed

    if text.startswith("DATA,"):
        parts = text.split(",")
        if len(parts) >= 3:
            try:
                parsed.device_timestamp_ms = float(parts[1])
                parsed.ecg_value = float(parts[2])
            except (ValueError, IndexError):
                return parsed
            if len(parts) >= 4:
                try:
                    parsed.resp_value = float(parts[3])
                except ValueError:
                    pass
            if len(parts) >= 5:
                try:
                    hr = float(parts[4])
                    if HR_MIN_BPM <= hr <= HR_MAX_BPM:
                        parsed.device_hr_bpm = hr
                except ValueError:
                    pass
            if len(parts) >= 6:
                parsed.status = parts[5].strip()
        return parsed

    cleaned = text.replace(",", " ").split()
    if len(cleaned) == 1:
        token = cleaned[0]
        if token.replace("-", "").replace(".", "").isdigit():
            try:
                parsed.ecg_value = float(token)
            except ValueError:
                pass
    return parsed


def _remove_baseline(data: np.ndarray, sample_rate: float) -> np.ndarray:
    window = max(3, int(sample_rate * 0.6))
    if window % 2 == 0:
        window += 1
    kernel = np.ones(window, dtype=np.float64) / window
    baseline = np.convolve(data.astype(np.float64), kernel, mode="same")
    return data.astype(np.float64) - baseline


def filter_ecg_band(
    data_uv: np.ndarray,
    sample_rate: float,
    low_hz: float = 0.5,
    high_hz: float = 40.0,
    notch_hz: Optional[float] = None,
) -> np.ndarray:
    """
    Clean ECG band via FFT mask: bandpass [low_hz, high_hz] plus an optional
    narrow mains notch. Reflect-padded to avoid FFT wrap-around edge artifacts.
    """
    n = data_uv.size
    if n < 64:
        return data_uv.astype(np.float64).copy()

    pad = min(n // 4, int(sample_rate))
    if pad >= 8:
        ext = np.concatenate(
            [data_uv[pad:0:-1], data_uv, data_uv[-2 : -pad - 2 : -1]]
        )
    else:
        ext = data_uv.astype(np.float64)
        pad = 0

    centered = ext.astype(np.float64) - float(np.mean(ext))
    spec = np.fft.rfft(centered)
    freqs = np.fft.rfftfreq(ext.size, d=1.0 / sample_rate)
    spec[(freqs < low_hz) | (freqs > high_hz)] = 0.0
    if notch_hz:
        spec[np.abs(freqs - notch_hz) <= 1.0] = 0.0
    out = np.fft.irfft(spec, ext.size)
    return out[pad : pad + n] if pad else out


def detect_mains_interference(data_uv: np.ndarray, sample_rate: float) -> Optional[float]:
    """
    Return 50.0/60.0 when a narrowband mains spike dominates the spectrum,
    else None. Compares each mains bin against neighbouring spectral median.
    """
    n = data_uv.size
    if n < int(sample_rate * 4):
        return None
    centered = data_uv.astype(np.float64) - float(np.mean(data_uv))
    power = np.abs(np.fft.rfft(centered)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    df = float(freqs[1] - freqs[0]) if freqs.size > 1 else 1.0
    if df <= 0:
        return None
    for hz in (50.0, 60.0):
        i = int(round(hz / df))
        if i < 9 or i + 9 >= power.size:
            continue
        peak = float(power[i - 1 : i + 2].max())
        neigh = float(
            np.median(np.concatenate([power[i - 8 : i - 3], power[i + 4 : i + 9]]))
        )
        if neigh > 0 and peak > 6.0 * neigh:
            return hz
    return None


def hf_noise_ratio(data_uv: np.ndarray, sample_rate: float, cutoff_hz: float = 40.0) -> float:
    """Fraction of spectral power above cutoff_hz (EMG/mains/hf artifact share)."""
    n = data_uv.size
    if n < 64:
        return 0.0
    centered = data_uv.astype(np.float64) - float(np.mean(data_uv))
    power = np.abs(np.fft.rfft(centered)) ** 2
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    band = (freqs > 0.5) & (freqs <= sample_rate / 2.0)
    total = float(power[band].sum())
    if total <= 0:
        return 0.0
    return float(power[freqs > cutoff_hz].sum() / total)


def detect_r_peaks(data: np.ndarray, sample_rate: float) -> List[int]:
    """
    R-peak detection with baseline removal, polarity handling, and refractory NMS.

    Uses amplitude-ranked non-maximum suppression so T-waves and noise spikes
    inside the refractory window do not create a second beat count.
    """
    if data.size < max(20, int(sample_rate * 0.8)):
        return []

    sample_rate = max(float(sample_rate), 1.0)
    filtered = _remove_baseline(data, sample_rate)

    # Polarity-agnostic envelope emphasises QRS regardless of inversion
    envelope = np.abs(filtered)
    if float(np.max(envelope)) <= 0:
        return []

    noise_floor = float(np.percentile(envelope, 60))
    peak_level = float(np.percentile(envelope, 92))
    threshold = noise_floor + 0.35 * (peak_level - noise_floor)
    refractory = max(1, int(REFRACTORY_SEC * sample_rate))

    candidates: List[Tuple[int, float]] = []
    for idx in range(1, envelope.size - 1):
        if (
            envelope[idx] >= threshold
            and envelope[idx] >= envelope[idx - 1]
            and envelope[idx] > envelope[idx + 1]
        ):
            candidates.append((idx, float(envelope[idx])))

    if not candidates:
        return []

    candidates.sort(key=lambda item: item[1], reverse=True)
    suppressed = np.zeros(envelope.size, dtype=bool)
    peaks: List[int] = []

    for idx, _amp in candidates:
        if suppressed[idx]:
            continue
        peaks.append(idx)
        lo = max(0, idx - refractory)
        hi = min(envelope.size, idx + refractory + 1)
        suppressed[lo:hi] = True

    peaks.sort()
    return peaks


def _envelope_short_period(
    envelope: np.ndarray,
    sample_rate: float,
    lo_sec: float = 0.12,
    hi_sec: float = 0.75,
    min_corr: float = 0.10,
) -> Optional[float]:
    """
    First major autocorrelation peak of the |signal| envelope within [lo, hi] s.

    Detects a fast periodic train (e.g. artifact spikes) that the refractory
    NMS may be undersampling at ~half rate. Returns lag in seconds or None.
    """
    env = envelope.astype(np.float64) - float(np.mean(envelope))
    if env.size < int(hi_sec * sample_rate) + 2:
        return None
    ac = np.correlate(env, env, "full")[env.size - 1 :]
    if ac.size == 0 or ac[0] <= 0:
        return None
    ac = ac / ac[0]
    lo = int(lo_sec * sample_rate)
    hi = min(int(hi_sec * sample_rate), ac.size - 2)
    for i in range(lo, hi):
        if ac[i] >= ac[i - 1] and ac[i] > ac[i + 1] and ac[i] > min_corr:
            y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
            denom = y0 - 2.0 * y1 + y2
            delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
            return float(i + delta) / sample_rate
    return None


def _slow_component_rate(
    data_uv: np.ndarray,
    sample_rate: float,
) -> Tuple[Optional[float], Optional[float], List[int]]:
    """
    Cardiac-rate estimate from the <3 Hz component (P/T-wave repetition rate).

    Robust when sharp high-frequency artifacts dominate the QRS band: the
    artifact spikes live above ~3 Hz while the cardiac cycle repeats at ~1 Hz.
    Returns (bpm, rr_cv, peak_indices) or (None, None, []).
    """
    n = data_uv.size
    if n < int(sample_rate * 3):
        return None, None, []

    centered = data_uv.astype(np.float64) - float(np.mean(data_uv))
    spec = np.fft.rfft(centered)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    spec[freqs > 3.0] = 0.0
    slow = np.fft.irfft(spec, n)

    # Light smoothing suppresses residual ripple from the brick-wall cutoff
    w = max(3, int(sample_rate * 0.12))
    if w % 2 == 0:
        w += 1
    slow = np.convolve(slow, np.ones(w, dtype=np.float64) / w, mode="same")

    p55 = float(np.percentile(slow, 55))
    p95 = float(np.percentile(slow, 95))
    threshold = p55 + 0.35 * (p95 - p55)
    min_dist = int(0.5 * sample_rate)

    candidates = [
        i
        for i in range(1, n - 1)
        if slow[i] >= threshold and slow[i] >= slow[i - 1] and slow[i] > slow[i + 1]
    ]
    candidates.sort(key=lambda i: float(slow[i]), reverse=True)

    blocked = np.zeros(n, dtype=bool)
    peaks: List[int] = []
    for i in candidates:
        if blocked[i]:
            continue
        peaks.append(i)
        blocked[max(0, i - min_dist) : i + min_dist + 1] = True
    peaks.sort()

    if len(peaks) < 4:
        return None, None, peaks

    rr = np.diff(np.asarray(peaks, dtype=np.float64)) / sample_rate
    valid = rr[(rr >= RR_MIN_SEC) & (rr <= RR_MAX_SEC)]
    if valid.size < 3:
        return None, None, peaks

    median_rr = float(np.median(valid))
    if median_rr <= 0:
        return None, None, peaks

    bpm = max(HR_MIN_BPM, min(HR_MAX_BPM, 60.0 / median_rr))
    cv = float(np.std(valid) / np.mean(valid)) if valid.size >= 2 else 0.0
    return bpm, cv, peaks


def _broad_peak_fraction(
    envelope: np.ndarray,
    peaks: List[int],
    sample_rate: float,
    width_sec: float = 0.08,
) -> float:
    """
    Fraction of detected peaks whose envelope excursion is wider than width_sec.

    QRS complexes are narrow (~20-60 ms); T-waves mistaken for beats are broad
    (~90-150 ms). A roughly half-broad population indicates T-wave
    double-counting rather than a uniformly wide-QRS rhythm.
    """
    if len(peaks) < 4:
        return 0.0
    broad = 0
    for p in peaks:
        half = 0.5 * float(envelope[p])
        if half <= 0:
            continue
        lo = p
        while lo > 0 and envelope[lo] >= half:
            lo -= 1
        hi = p
        while hi < envelope.size - 1 and envelope[hi] >= half:
            hi += 1
        if (hi - lo) / sample_rate > width_sec:
            broad += 1
    return broad / len(peaks)


def heart_rate_from_peaks(peaks: List[int], sample_rate: float) -> Tuple[Optional[float], Optional[float]]:
    """Return (bpm, rr_coefficient_of_variation) from peak indices."""
    if len(peaks) < 2:
        return None, None

    sample_rate = max(float(sample_rate), 1.0)
    rr = np.diff(np.asarray(peaks, dtype=np.float64)) / sample_rate
    valid = rr[(rr >= RR_MIN_SEC) & (rr <= RR_MAX_SEC)]
    if valid.size == 0:
        return None, None

    median_rr = float(np.median(valid))
    if median_rr <= 0:
        return None, None

    # HR_bpm = 60 / RR_sec,  RR_sec = Δn / F_s  (see ads1292_hardware.py)
    bpm = max(HR_MIN_BPM, min(HR_MAX_BPM, 60.0 / median_rr))
    cv = float(np.std(valid) / np.mean(valid)) if valid.size >= 2 else 0.0
    return bpm, cv


def resolve_heart_rate(
    computed_bpm: Optional[float],
    device_bpm: Optional[float],
    peaks: List[int],
) -> Tuple[Optional[float], str]:
    """
    Choose the most trustworthy heart rate.

    Prefer firmware BPM when stable; otherwise use R-peak estimate.
    If both exist and disagree by >25%, trust device when enough beats detected.
    """
    if device_bpm is not None and computed_bpm is None:
        return device_bpm, "device"

    if computed_bpm is not None and device_bpm is None:
        return computed_bpm, "computed"

    if device_bpm is not None and computed_bpm is not None:
        avg = (device_bpm + computed_bpm) / 2.0
        if avg > 0 and abs(device_bpm - computed_bpm) / avg <= 0.12:
            return round((device_bpm + computed_bpm) / 2.0, 1), "fused"
        # Large disagreement often indicates sample-rate mismatch — trust firmware
        if avg > 0 and abs(device_bpm - computed_bpm) / avg > 0.25:
            return device_bpm, "device"
        if len(peaks) >= 4:
            return computed_bpm, "computed"
        return device_bpm, "device"

    return None, "unknown"


def rhythm_from_cv(cv: Optional[float]) -> Tuple[str, str]:
    """Return (label, severity_token) for UI coloring."""
    if cv is None:
        return "Undetermined", "gray"
    if cv < 0.08:
        return "Regular", "green"
    if cv < 0.16:
        return "Mild irregularity", "yellow"
    return "Irregular", "red"


def hr_zone(bpm: Optional[float]) -> Tuple[str, str, str, bool]:
    """
    Clinical resting-HR zone for an adult tracing.
    Returns (zone_key, chinese_label, severity_token, is_abnormal).
    """
    if bpm is None:
        return "unknown", "未知", "gray", False
    if bpm < 50.0:
        return "bradycardia", "严重心动过缓", "red", True
    if bpm < 60.0:
        return "relative_bradycardia", "心动过缓(偏低)", "yellow", True
    if bpm <= 100.0:
        return "normal", "正常范围", "green", False
    if bpm <= 120.0:
        return "mild_tachycardia", "窦性心动过速可能", "yellow", True
    if bpm <= 150.0:
        return "tachycardia", "心动过速", "red", True
    return "severe_tachycardia", "严重心动过速", "red", True


def compute_ecg_metrics(
    data: np.ndarray,
    sample_rate: float,
    device_hr_bpm: Optional[float] = None,
    auto_correct_rate: bool = True,
    uv_per_count: float = 1.0,
) -> Dict[str, Any]:
    """Full clinical snapshot for plotter and AI preprocessing."""
    arr = np.asarray(data, dtype=np.float64).flatten()
    sample_rate = max(float(sample_rate), 1.0)
    scale = max(float(uv_per_count), 1e-12)
    arr_uv = arr * scale
    rate_correction_note: Optional[str] = None

    # Clean ECG band: 0.5-40 Hz bandpass with automatic 50/60 Hz mains notch.
    # Peak detection and rate estimation run on this filtered trace so
    # baseline wander, EMG and mains hum do not corrupt beat picking.
    mains_hz = detect_mains_interference(arr_uv, sample_rate)
    filtered_uv = filter_ecg_band(arr_uv, sample_rate, notch_hz=mains_hz)
    hf_ratio = hf_noise_ratio(arr_uv, sample_rate)

    if arr.size == 0:
        return {
            "heart_rate_bpm": None,
            "heart_rate_source": "unknown",
            "rhythm_label": "Awaiting data",
            "rhythm_color_token": "gray",
            "signal_quality_label": "Awaiting data",
            "signal_quality_color_token": "gray",
            "peak_to_peak": 0.0,
            "mean": 0.0,
            "std": 0.0,
            "noise_proxy": 0.0,
            "sample_count": 0,
            "duration_sec": 0.0,
            "rr_variability": None,
            "qrs_count": 0,
            "sample_rate_hz": sample_rate,
            "computed_hr_bpm": None,
            "device_hr_bpm": device_hr_bpm,
            "heart_rate_method": "none",
            "heart_rate_zone": "unknown",
            "heart_rate_zone_label": "未知",
            "heart_rate_abnormal": False,
            "mains_interference_hz": None,
            "hf_noise_ratio": 0.0,
        }

    # R-peak detection runs on the filtered (clean-band) trace; amplitude QC
    # still uses the raw µV signal.
    peaks = detect_r_peaks(filtered_uv, sample_rate)
    computed_bpm, rr_cv = heart_rate_from_peaks(peaks, sample_rate)

    if auto_correct_rate and device_hr_bpm is not None:
        corrected_rate, reason = correct_sample_rate_from_hr(
            sample_rate, computed_bpm, device_hr_bpm
        )
        if reason and corrected_rate != sample_rate:
            sample_rate = corrected_rate
            peaks = detect_r_peaks(filtered_uv, sample_rate)
            computed_bpm, rr_cv = heart_rate_from_peaks(peaks, sample_rate)
            rate_correction_note = reason

    final_bpm, hr_source = resolve_heart_rate(computed_bpm, device_hr_bpm, peaks)
    rhythm_label, rhythm_token = rhythm_from_cv(rr_cv)
    rate_method = "qrs_peaks"
    harmonic_corrected = False

    # Overcount guard: two artifact signatures both make the peak detector
    # report ~2x the true rate —
    #   (a) harmonic: the envelope holds a faster periodic train (~half the
    #       detected RR) that refractory NMS undersamples (motion/tremor spikes);
    #   (b) t_wave_alt: ~half the detected peaks are broad (T-waves counted as
    #       beats because they land just outside the refractory window).
    # In either case the <3 Hz cardiac component gives the trustworthy rate.
    if computed_bpm and arr.size >= int(sample_rate * 3):
        median_rr = 60.0 / computed_bpm
        envelope = np.abs(_remove_baseline(filtered_uv, sample_rate))
        short_period = _envelope_short_period(envelope, sample_rate)
        harmonic_env = bool(
            short_period and 1.7 <= median_rr / short_period <= 2.5
        )
        broad_frac = _broad_peak_fraction(envelope, peaks, sample_rate)
        t_wave_alt = 0.25 <= broad_frac <= 0.75
        if harmonic_env or t_wave_alt:
            slow_bpm, slow_cv, slow_peaks = _slow_component_rate(filtered_uv, sample_rate)
            if (
                slow_bpm
                and 1.5 <= computed_bpm / slow_bpm <= 2.6
                and abs(computed_bpm - slow_bpm) / ((computed_bpm + slow_bpm) / 2.0) > 0.25
            ):
                computed_bpm = slow_bpm
                rr_cv = slow_cv
                peaks = slow_peaks
                rate_method = "slow_component"
                harmonic_corrected = True
                final_bpm, hr_source = resolve_heart_rate(
                    computed_bpm, device_hr_bpm, peaks
                )
                rhythm_label, rhythm_token = rhythm_from_cv(rr_cv)

    peak_to_peak = float(np.ptp(arr_uv))
    mean = float(np.mean(arr_uv))
    std = float(np.std(arr_uv))
    noise_proxy = float(np.std(np.diff(arr_uv))) if arr_uv.size > 1 else 0.0
    duration_sec = float(arr.size / sample_rate)

    drift_window = max(1, arr_uv.size // 5)
    baseline_drift = abs(
        float(np.median(arr_uv[:drift_window]) - np.median(arr_uv[-drift_window:]))
    )
    snr_proxy = peak_to_peak / max(noise_proxy, 1.0)

    if peak_to_peak < 50.0:
        quality_label, quality_token = "No signal", "gray"
    elif snr_proxy >= 10 and baseline_drift <= peak_to_peak * 0.25:
        quality_label, quality_token = "Excellent", "green"
    elif snr_proxy >= 6 and baseline_drift <= peak_to_peak * 0.4:
        quality_label, quality_token = "Good", "green"
    elif snr_proxy >= 3:
        quality_label, quality_token = "Fair", "yellow"
    else:
        quality_label, quality_token = "Poor", "red"

    # A harmonic-overcount correction means the QRS band is dominated by a
    # periodic artifact train — the tracing cannot be called clean. Strong
    # mains interference likewise caps the grade at Fair.
    if (harmonic_corrected or mains_hz is not None) and quality_token == "green":
        quality_label, quality_token = "Fair", "yellow"

    hr_zone_key, hr_zone_label, _, hr_abnormal = hr_zone(final_bpm)

    return {
        "heart_rate_bpm": final_bpm,
        "heart_rate_source": hr_source,
        "heart_rate_method": rate_method,
        "heart_rate_zone": hr_zone_key,
        "heart_rate_zone_label": hr_zone_label,
        "heart_rate_abnormal": hr_abnormal,
        "mains_interference_hz": mains_hz,
        "hf_noise_ratio": round(hf_ratio, 4),
        "rhythm_label": rhythm_label,
        "rhythm_color_token": rhythm_token,
        "signal_quality_label": quality_label,
        "signal_quality_color_token": quality_token,
        "peak_to_peak": peak_to_peak,
        "mean": mean,
        "std": std,
        "noise_proxy": noise_proxy,
        "sample_count": int(arr.size),
        "duration_sec": duration_sec,
        "rr_variability": rr_cv,
        "qrs_count": len(peaks),
        "sample_rate_hz": sample_rate,
        "computed_hr_bpm": computed_bpm,
        "device_hr_bpm": device_hr_bpm,
        "sample_rate_correction": rate_correction_note,
    }


def preprocess_for_llm(
    data: np.ndarray,
    sample_rate: float,
    device_hr_bpm: Optional[float] = None,
    uv_per_count: float = 1.0,
) -> Dict[str, Any]:
    """Structured summary passed to the LLM — uses calibrated metrics."""
    metrics = compute_ecg_metrics(
        data, sample_rate, device_hr_bpm, uv_per_count=uv_per_count
    )
    arr = np.asarray(data, dtype=np.float64).flatten()
    arr_uv = arr * max(float(uv_per_count), 1e-12)

    # Waveform snippet from the clean ECG band (0.5-40 Hz + mains notch)
    snippet_uv = filter_ecg_band(
        arr_uv,
        metrics["sample_rate_hz"],
        notch_hz=metrics.get("mains_interference_hz"),
    )
    snippet_len = min(int(metrics["sample_rate_hz"] * 5), snippet_uv.size)
    snippet = snippet_uv[-snippet_len:] if snippet_len else snippet_uv
    decimation = max(1, snippet_len // 250)

    return {
        "sample_rate_hz": round(metrics["sample_rate_hz"], 1),
        "raw_sample_count": metrics["sample_count"],
        "duration_sec": round(metrics["duration_sec"], 2),
        "heart_rate_bpm": metrics["heart_rate_bpm"],
        "heart_rate_source": metrics["heart_rate_source"],
        "device_hr_bpm": metrics["device_hr_bpm"],
        "computed_hr_bpm": metrics["computed_hr_bpm"],
        "heart_rate_method": metrics.get("heart_rate_method", "qrs_peaks"),
        "heart_rate_zone": metrics.get("heart_rate_zone", "unknown"),
        "heart_rate_zone_label": metrics.get("heart_rate_zone_label", "未知"),
        "heart_rate_abnormal": metrics.get("heart_rate_abnormal", False),
        "mains_interference_hz": metrics.get("mains_interference_hz"),
        "hf_noise_ratio": metrics.get("hf_noise_ratio", 0.0),
        "qrs_count": metrics["qrs_count"],
        "rhythm_regularity": metrics["rhythm_label"],
        "rr_variability_cv": metrics["rr_variability"],
        "mean_signal": round(metrics["mean"], 2),
        "std_signal": round(metrics["std"], 2),
        "peak_to_peak": round(metrics["peak_to_peak"], 2),
        "signal_quality": metrics["signal_quality_label"],
        "waveform_snippet": [round(float(v), 1) for v in snippet[::decimation].tolist()],
        "clinical_note": (
            "Signal band-limited to 0.5-40 Hz"
            + (
                f" with {metrics['mains_interference_hz']:.0f} Hz mains notch"
                if metrics.get("mains_interference_hz")
                else ""
            )
            + ". Heart rate uses firmware value when available; otherwise R-peak detection "
            f"at {metrics['sample_rate_hz']:.0f} Hz with {REFRACTORY_SEC:.2f}s refractory period."
            + (
                " Peak-based estimate showed harmonic overcount on a periodic artifact "
                "train; rate falls back to the <3 Hz cardiac component."
                if metrics.get("heart_rate_method") == "slow_component"
                else ""
            )
        ),
    }