"""
Clinical ECG signal processing — sampling-rate calibration, R-peak detection, and HR.

ADS1292R-based firmware often streams at 500 Hz while older code assumed 250 Hz,
which doubles computed heart rate. This module centralises parsing, rate estimation,
and beat detection so UI and AI diagnosis share one source of truth.
"""

from __future__ import annotations

import math
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


MIN_MAINS_AMP_UV = 18.0


def detect_mains_interference(data_uv: np.ndarray, sample_rate: float) -> Optional[float]:
    """
    Return 50.0/60.0 when a narrowband mains spike dominates the spectrum,
    else None. Requires BOTH a sharp spectral peak (vs local median) and an
    absolute amplitude above MIN_MAINS_AMP_UV — QRS harmonic splatter from a
    periodic beat train can produce a narrow ~12 µV line near 60 Hz that is
    not mains interference.
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
        if neigh <= 0 or peak <= 6.0 * neigh:
            continue
        amp_uv = 2.0 * math.sqrt(float(power[i - 1 : i + 2].sum())) / n
        if amp_uv >= MIN_MAINS_AMP_UV:
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


def _normalized_autocorrelation(data: np.ndarray) -> np.ndarray:
    """Biased normalized autocorrelation via FFT (positive lags only)."""
    centered = data.astype(np.float64) - float(np.mean(data))
    n = centered.size
    if n == 0:
        return np.asarray([], dtype=np.float64)
    nfft = 1 << (2 * n - 1).bit_length()
    spec = np.fft.rfft(centered, nfft)
    ac = np.fft.irfft(spec * np.conj(spec), nfft)[:n]
    if ac.size == 0 or ac[0] <= 0:
        return np.asarray([], dtype=np.float64)
    return ac / ac[0]


def _envelope_short_period(
    envelope: np.ndarray,
    sample_rate: float,
    lo_sec: float = 0.12,
    hi_sec: float = 0.75,
    min_corr: float = 0.10,
) -> Optional[float]:
    """
    Dominant autocorrelation period of the |signal| envelope within [lo, hi] s.

    Detects a fast periodic train (e.g. artifact spikes) that the refractory
    NMS may undersample at half or one-third rate. The strongest lag is used:
    jittered artifact trains often produce several side lobes, and choosing the
    earliest side lobe underestimates the artifact period. Returns lag seconds
    or None.
    """
    env = envelope.astype(np.float64)
    if env.size < int(hi_sec * sample_rate) + 2:
        return None
    ac = _normalized_autocorrelation(env)
    if ac.size == 0:
        return None
    lo = int(lo_sec * sample_rate)
    hi = min(int(hi_sec * sample_rate), ac.size - 2)
    candidates = [
        i
        for i in range(lo, hi)
        if ac[i] >= ac[i - 1] and ac[i] > ac[i + 1] and ac[i] > min_corr
    ]
    if not candidates:
        return None
    i = max(candidates, key=lambda lag: float(ac[lag]))
    y0, y1, y2 = ac[i - 1], ac[i], ac[i + 1]
    denom = y0 - 2.0 * y1 + y2
    delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
    return float(i + delta) / sample_rate


def _dominant_acf_period(
    data_uv: np.ndarray,
    sample_rate: float,
    lo_sec: float = 0.45,
    hi_sec: float = 2.0,
    min_corr: float = 0.025,
) -> Tuple[Optional[float], float]:
    """
    Dominant physiological repetition period by autocorrelation.

    Returns (period_sec, normalized_correlation). The strongest peak is found
    first, then the earliest peak within 75% of that strength is selected. This
    avoids both (a) noise-induced very early peaks and (b) later harmonic peaks
    when the true cardiac lag is already prominent.
    """
    n = data_uv.size
    if n < int(hi_sec * sample_rate) + 2:
        return None, 0.0

    ac = _normalized_autocorrelation(data_uv)
    if ac.size == 0:
        return None, 0.0

    lo = max(1, int(lo_sec * sample_rate))
    hi = min(int(hi_sec * sample_rate), ac.size - 2)
    if hi <= lo:
        return None, 0.0

    peaks = [
        i
        for i in range(lo, hi)
        if ac[i] >= ac[i - 1] and ac[i] > ac[i + 1] and ac[i] > min_corr
    ]
    if not peaks:
        return None, 0.0

    best = max(peaks, key=lambda i: float(ac[i]))
    best_corr = float(ac[best])
    eligible = [i for i in peaks if float(ac[i]) >= 0.75 * best_corr]
    chosen = min(eligible) if eligible else best

    y0, y1, y2 = float(ac[chosen - 1]), float(ac[chosen]), float(ac[chosen + 1])
    denom = y0 - 2.0 * y1 + y2
    delta = 0.5 * (y0 - y2) / denom if denom != 0 else 0.0
    period = float(chosen + delta) / sample_rate
    if period <= 0:
        return None, 0.0
    return period, float(ac[chosen])


def _slow_component_rate(
    data_uv: np.ndarray,
    sample_rate: float,
) -> Tuple[Optional[float], Optional[float], List[int], Dict[str, Any]]:
    """
    Cardiac-rate estimate from the 0.5-3 Hz component (P/T-wave repetition rate).

    Robust when sharp high-frequency artifacts dominate the QRS band: the
    artifact spikes live above ~3 Hz while the cardiac cycle repeats at ~1 Hz.
    Autocorrelation is the primary rate estimator because artifact-corrupted
    peak picking can miss cycles and produce unstable rates on adjacent windows.
    Peak-derived RR variability is not treated as clinical rhythm evidence here:
    the low-frequency peaks may be P/T waves or artifact ringing rather than QRS
    complexes. Rhythm therefore remains honestly undetermined in this fallback
    instead of falsely irregular.

    Returns (bpm, rr_cv, peak_indices, estimator_info).
    """
    n = data_uv.size
    if n < int(sample_rate * 3):
        return None, None, [], {"estimator": "none"}

    centered = data_uv.astype(np.float64) - float(np.mean(data_uv))
    spec = np.fft.rfft(centered)
    freqs = np.fft.rfftfreq(n, d=1.0 / sample_rate)
    spec[(freqs < 0.5) | (freqs > 3.0)] = 0.0
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

    peak_bpm: Optional[float] = None
    peak_cv: Optional[float] = None
    median_peak_rr: Optional[float] = None
    valid_count = 0
    if len(peaks) >= 4:
        rr = np.diff(np.asarray(peaks, dtype=np.float64)) / sample_rate
        valid = rr[(rr >= RR_MIN_SEC) & (rr <= RR_MAX_SEC)]
        valid_count = int(valid.size)
        if valid.size >= 3:
            median_peak_rr = float(np.median(valid))
            if median_peak_rr > 0:
                peak_bpm = max(HR_MIN_BPM, min(HR_MAX_BPM, 60.0 / median_peak_rr))
                peak_cv = float(np.std(valid) / np.mean(valid)) if valid.size >= 2 else 0.0

    acf_period, acf_corr = _dominant_acf_period(slow, sample_rate)
    if acf_period:
        acf_bpm = max(HR_MIN_BPM, min(HR_MAX_BPM, 60.0 / acf_period))
        duration_sec = n / sample_rate
        expected_cycles = max(duration_sec / acf_period, 1e-9)
        coverage = len(peaks) / expected_cycles
        rr_ratio = (
            median_peak_rr / acf_period
            if median_peak_rr is not None and acf_period > 0
            else None
        )
        peaks_reliable = bool(
            peak_bpm is not None
            and rr_ratio is not None
            and 0.85 <= rr_ratio <= 1.15
            and coverage >= 0.65
        )
        return (
            acf_bpm,
            None,
            peaks,
            {
                "estimator": "acf",
                "period_sec": float(acf_period),
                "acf_correlation": float(acf_corr),
                "peak_count": len(peaks),
                "peak_coverage": float(coverage),
                "peaks_reliable": peaks_reliable,
                "peak_bpm": peak_bpm,
                "valid_rr_count": valid_count,
            },
        )

    if peak_bpm is not None:
        return peak_bpm, None, peaks, {"estimator": "peaks", "peak_count": len(peaks)}
    return None, None, peaks, {"estimator": "none", "peak_count": len(peaks)}


_QRST_FINDINGS_CN = {
    "wide_qrs": "QRS 增宽（≥120 ms）",
    "borderline_qrs_width": "QRS 时限接近上限（110–120 ms）",
    "st_positive_deviation": "ST 段正向偏移（相对显示极性）",
    "st_negative_deviation": "ST 段负向偏移（相对显示极性）",
    "borderline_st_deviation": "ST 段轻中度偏移（50–100 µV）",
    "t_wave_discordance": "T 波方向与主 QRS 方向相反",
    "low_qrs_amplitude": "QRS 振幅偏低，需同时确认电极接触",
}


def _qrst_unavailable_result(reason: str) -> Dict[str, Any]:
    return {
        "qrst_status": "unavailable",
        "qrst_status_label": "QRST 无法评估",
        "qrst_color_token": "gray",
        "qrst_confidence": "low",
        "qrst_scope": "unavailable",
        "qrst_reason": reason,
        "qrst_findings": [],
        "qrst_findings_cn": [],
        "qrs_duration_ms": None,
        "qrs_amplitude_uv": None,
        "st_deviation_uv": None,
        "t_wave_polarity": "unknown",
        "qrst_beats_used": 0,
    }


def _measure_qrst_beat(
    segment: np.ndarray,
    pre_samples: int,
    sample_rate: float,
    t_search_end_sec: float,
) -> Optional[Dict[str, float]]:
    """Heuristic single-beat QRST measurements relative to the PQ baseline."""
    fs = float(sample_rate)
    if segment.size < pre_samples + int(0.20 * fs):
        return None

    r_search = int(0.02 * fs)
    rlo = max(0, pre_samples - r_search)
    rhi = min(segment.size, pre_samples + r_search + 1)
    rwin = segment[rlo:rhi]
    if rwin.size == 0:
        return None
    r_offset = int(np.argmax(np.abs(rwin)))
    r_pos = rlo + r_offset

    # PQ/PR reference window. It is deliberately earlier than the immediate
    # pre-QRS region so a wide QRS onset does not become the baseline.
    b0 = pre_samples - int(0.13 * fs)
    b1 = pre_samples - int(0.08 * fs)
    if b0 < 0 or b1 <= b0:
        return None
    baseline = float(np.median(segment[b0:b1]))
    x = segment.astype(np.float64) - baseline
    signed_r = float(x[r_pos])
    r_amp = abs(signed_r)
    if r_amp < 20.0:
        return None

    threshold = max(25.0, 0.15 * r_amp)
    qlo = max(0, r_pos - int(0.16 * fs))
    qhi = min(x.size, r_pos + int(0.16 * fs))
    if qhi <= qlo + 3:
        return None
    active = np.abs(x[qlo:qhi]) >= threshold

    # Fill very short baseline crossings inside the QRS (R-to-S transition).
    max_gap = max(1, int(0.008 * fs))
    i = 0
    while i < active.size:
        if active[i]:
            i += 1
            continue
        j = i
        while j < active.size and not active[j]:
            j += 1
        if i > 0 and j < active.size and j - i <= max_gap:
            active[i:j] = True
        i = j

    rp = r_pos - qlo
    if not (0 <= rp < active.size):
        return None
    if not active[rp]:
        active_indices = np.flatnonzero(active)
        if active_indices.size == 0:
            return None
        rp = int(active_indices[np.argmin(np.abs(active_indices - rp))])

    start = rp
    while start > 0 and active[start - 1]:
        start -= 1
    end = rp
    while end < active.size - 1 and active[end + 1]:
        end += 1
    qrs_onset = qlo + start
    qrs_end = qlo + end
    qrs_width_ms = (qrs_end - qrs_onset + 1) / fs * 1000.0
    if qrs_width_ms < 15.0 or qrs_width_ms > 260.0:
        return None

    # ST level at J+80 ms, median over ±5 ms to reduce noise sensitivity.
    st_center = qrs_end + int(0.08 * fs)
    st_half = max(1, int(0.005 * fs))
    st_deviation: Optional[float] = None
    if st_center + st_half < x.size:
        st_deviation = float(
            np.median(x[st_center - st_half : st_center + st_half + 1])
        )

    # T wave: strongest broad deflection after the early ST interval. Light
    # smoothing prevents single-sample noise from determining polarity.
    t0 = max(qrs_end + int(0.04 * fs), r_pos + int(0.07 * fs))
    t1 = min(x.size, qrs_end + int(t_search_end_sec * fs))
    t_value: Optional[float] = None
    t_amplitude: Optional[float] = None
    t_pos: Optional[int] = None
    if t1 > t0 + max(5, int(0.02 * fs)):
        smooth_n = max(1, int(0.006 * fs))
        kernel = np.ones(smooth_n, dtype=np.float64) / smooth_n
        smooth = np.convolve(x, kernel, mode="same")
        twin = smooth[t0:t1]
        ti = int(np.argmax(np.abs(twin)))
        t_value = float(twin[ti])
        t_amplitude = abs(t_value)
        t_pos = t0 + ti

    return {
        "baseline": baseline,
        "signed_r": signed_r,
        "qrs_amplitude_uv": r_amp,
        "qrs_width_ms": float(qrs_width_ms),
        "st_deviation_uv": st_deviation if st_deviation is not None else float("nan"),
        "st_pos": float(st_center),
        "t_pos": float(t_pos) if t_pos is not None else float("nan"),
        "t_value_uv": t_value if t_value is not None else float("nan"),
        "t_amplitude_uv": t_amplitude if t_amplitude is not None else float("nan"),
    }


def _analyze_qrst_morphology(
    filtered_uv: np.ndarray,
    sample_rate: float,
    peaks: List[int],
    rate_method: str,
    quality_label: str,
    quality_token: str,
    rr_cv: Optional[float],
) -> Dict[str, Any]:
    """
    Conservative single-lead QRST morphology screening.

    This is deliberately a screening layer, not a diagnostic 12-lead interpreter.
    It reports robust median measurements and simple warnings only when ordinary
    QRS peak evidence is trustworthy.
    """
    if rate_method != "qrs_peaks":
        return _qrst_unavailable_result("artifact_or_non_qrs_rate_method")
    if quality_label in {"No signal", "Poor"} or quality_token in {"gray", "red"}:
        return _qrst_unavailable_result("insufficient_signal_quality")
    if len(peaks) < 4:
        return _qrst_unavailable_result("insufficient_qrs_beats")

    fs = float(sample_rate)
    pre = int(0.22 * fs)
    post = int(0.48 * fs)
    rr = np.diff(np.asarray(peaks, dtype=np.float64)) / fs
    valid_rr = rr[(rr >= RR_MIN_SEC) & (rr <= RR_MAX_SEC)]
    median_rr = float(np.median(valid_rr)) if valid_rr.size else 0.85
    t_search_end = min(0.36, max(0.16, 0.45 * median_rr))

    measurements: List[Dict[str, float]] = []
    for peak in peaks:
        if peak - pre < 0 or peak + post >= filtered_uv.size:
            continue
        measured = _measure_qrst_beat(
            filtered_uv[peak - pre : peak + post], pre, fs, t_search_end
        )
        if measured is not None:
            measurements.append(measured)

    if len(measurements) < 3:
        return _qrst_unavailable_result("insufficient_complete_beats")

    sign_sum = sum(1 if m["signed_r"] >= 0 else -1 for m in measurements)
    dominant_sign = 1.0 if sign_sum >= 0 else -1.0
    dominant = [
        m for m in measurements if (m["signed_r"] >= 0) == (dominant_sign >= 0)
    ]
    if len(dominant) < 3:
        return _qrst_unavailable_result("inconsistent_qrs_polarity")

    widths = np.asarray([m["qrs_width_ms"] for m in dominant], dtype=np.float64)
    amps = np.asarray([m["qrs_amplitude_uv"] for m in dominant], dtype=np.float64)
    # A fixed J+80 ms ST point is meaningful only if the T wave is still far
    # enough away. At high heart rates the T wave can occupy that point; treating
    # the T upstroke as ST shift produced false positives, so those beats are
    # excluded rather than interpreted.
    st_valid: List[float] = []
    min_st_t_gap_samples = int(0.06 * fs)
    for m in dominant:
        st_val = m["st_deviation_uv"]
        if not np.isfinite(st_val):
            continue
        if not np.isfinite(m["t_pos"]) or not np.isfinite(m["st_pos"]):
            continue
        if m["t_pos"] - m["st_pos"] >= min_st_t_gap_samples:
            st_valid.append(float(st_val))

    t_vals = np.asarray([m["t_value_uv"] for m in dominant], dtype=np.float64)
    t_amps = np.asarray([m["t_amplitude_uv"] for m in dominant], dtype=np.float64)

    width_med = float(np.median(widths))
    amp_med = float(np.median(amps))
    st_med = float(np.median(st_valid)) if len(st_valid) >= 3 else None

    t_votes: List[int] = []
    # At short RR intervals the T wave, next P wave, and terminal QRS recovery
    # overlap. Polarity inversion in that crowded segment is not reliable
    # morphology evidence on a single lead, so it is reported as uncertain.
    if median_rr >= 0.60:
        for m, t_val, t_amp in zip(dominant, t_vals, t_amps):
            if not np.isfinite(t_val) or not np.isfinite(t_amp):
                continue
            min_t_amp = max(40.0, 0.12 * m["qrs_amplitude_uv"])
            if t_amp < min_t_amp:
                continue
            t_votes.append(1 if np.sign(t_val) == np.sign(m["signed_r"]) else -1)
    if len(t_votes) >= 2:
        positive_votes = sum(1 for vote in t_votes if vote > 0)
        negative_votes = len(t_votes) - positive_votes
        required = max(1, math.ceil(0.6 * len(t_votes)))
        if positive_votes >= required:
            t_polarity = "concordant"
        elif negative_votes >= required:
            t_polarity = "discordant"
        else:
            t_polarity = "mixed"
    else:
        t_polarity = "uncertain"

    st_assessed = st_med is not None
    t_assessed = t_polarity in {"concordant", "discordant", "mixed"}
    qrst_scope = "full" if st_assessed and t_assessed else "limited"

    confidence = "high" if quality_token == "green" and len(dominant) >= 5 else "medium"
    if rr_cv is not None and rr_cv >= 0.16 and confidence == "high":
        confidence = "medium"
    if (median_rr < 0.60 or qrst_scope == "limited") and confidence == "high":
        confidence = "medium"

    abnormal: List[str] = []
    borderline: List[str] = []
    if width_med >= 120.0:
        abnormal.append("wide_qrs")
    elif width_med >= 110.0:
        borderline.append("borderline_qrs_width")

    if st_med is not None:
        abs_st = abs(st_med)
        if abs_st >= 100.0:
            abnormal.append(
                "st_positive_deviation" if st_med >= 0 else "st_negative_deviation"
            )
        elif abs_st >= 50.0:
            borderline.append("borderline_st_deviation")

    if t_polarity == "discordant" and width_med < 120.0:
        borderline.append("t_wave_discordance")
    if amp_med < 250.0:
        borderline.append("low_qrs_amplitude")

    if abnormal:
        status = "abnormal"
        status_label = "QRST 形态异常线索"
        color = "red"
    elif borderline:
        status = "borderline"
        status_label = "QRST 形态边界/需复查"
        color = "yellow"
    else:
        status = "normal"
        status_label = (
            "未见明显 QRST 形态异常"
            if qrst_scope == "full"
            else "未见明显异常（可评估部分）"
        )
        color = "green"

    findings = abnormal + borderline
    return {
        "qrst_status": status,
        "qrst_status_label": status_label,
        "qrst_color_token": color,
        "qrst_confidence": confidence,
        "qrst_scope": qrst_scope,
        "qrst_reason": None,
        "qrst_findings": findings,
        "qrst_findings_cn": [_QRST_FINDINGS_CN[c] for c in findings],
        "qrs_duration_ms": round(width_med, 1),
        "qrs_amplitude_uv": round(amp_med, 1),
        "st_deviation_uv": round(st_med, 1) if st_med is not None else None,
        "t_wave_polarity": t_polarity,
        "qrst_beats_used": len(dominant),
    }


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
            "heart_rate_method_detail": None,
            "heart_rate_confidence": None,
            "heart_rate_acf_correlation": None,
            "artifact_period_sec": None,
            "heart_rate_zone": "unknown",
            "heart_rate_zone_label": "未知",
            "heart_rate_abnormal": False,
            "mains_interference_hz": None,
            "hf_noise_ratio": 0.0,
            **_qrst_unavailable_result("no_data"),
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
    rate_method_detail: Optional[str] = None
    heart_rate_confidence: Optional[str] = None
    heart_rate_acf_correlation: Optional[float] = None
    artifact_period_sec: Optional[float] = None
    harmonic_corrected = False

    # Overcount/artifact guard: two artifact signatures invalidate peak-based
    # rate and rhythm evidence —
    #   (a) harmonic: the envelope holds a faster periodic train that refractory
    #       NMS undersamples at about half or one-third rate (motion/tremor spikes);
    #   (b) t_wave_alt: ~half the detected peaks are broad (T-waves counted as
    #       beats because they land just outside the refractory window).
    # In either case the 0.5-3 Hz cardiac component is the trustworthy rate source.
    if computed_bpm and arr.size >= int(sample_rate * 3):
        median_rr = 60.0 / computed_bpm
        envelope = np.abs(_remove_baseline(filtered_uv, sample_rate))
        short_period = _envelope_short_period(envelope, sample_rate)
        harmonic_env = bool(
            short_period and 1.7 <= median_rr / short_period <= 3.2
        )
        if harmonic_env:
            artifact_period_sec = short_period
        broad_frac = _broad_peak_fraction(envelope, peaks, sample_rate)
        t_wave_alt = 0.25 <= broad_frac <= 0.75
        if harmonic_env or t_wave_alt:
            slow_bpm, slow_cv, slow_peaks, slow_info = _slow_component_rate(
                filtered_uv, sample_rate
            )
            if slow_bpm:
                slow_ratio = computed_bpm / slow_bpm
                relative_disagreement = abs(computed_bpm - slow_bpm) / (
                    (computed_bpm + slow_bpm) / 2.0
                )
                overcount_correction = (
                    1.5 <= slow_ratio <= 2.6 and relative_disagreement > 0.25
                )
                artifact_confirmed_agreement = relative_disagreement <= 0.20
            else:
                overcount_correction = False
                artifact_confirmed_agreement = False

            if slow_bpm and (overcount_correction or artifact_confirmed_agreement):
                computed_bpm = slow_bpm
                rr_cv = slow_cv
                peaks = slow_peaks
                rate_method = "slow_component"
                rate_method_detail = str(slow_info.get("estimator") or "unknown")
                heart_rate_acf_correlation = slow_info.get("acf_correlation")
                if heart_rate_acf_correlation is not None:
                    if heart_rate_acf_correlation >= 0.25:
                        heart_rate_confidence = "high"
                    elif heart_rate_acf_correlation >= 0.10:
                        heart_rate_confidence = "medium"
                    else:
                        heart_rate_confidence = "low"
                harmonic_corrected = True
                # Firmware HR is computed from the same artifact-contaminated
                # frontend signal, so a large disagreement must not undo the
                # correction. Fuse only when the two estimates independently agree.
                if device_hr_bpm is not None:
                    avg = (device_hr_bpm + computed_bpm) / 2.0
                    if avg > 0 and abs(device_hr_bpm - computed_bpm) / avg <= 0.12:
                        final_bpm = round((device_hr_bpm + computed_bpm) / 2.0, 1)
                        hr_source = "fused"
                    else:
                        final_bpm = computed_bpm
                        hr_source = "computed"
                else:
                    final_bpm = computed_bpm
                    hr_source = "computed"
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
    if harmonic_corrected and heart_rate_confidence == "low":
        quality_label, quality_token = "Poor", "red"
    elif (harmonic_corrected or mains_hz is not None) and quality_token == "green":
        quality_label, quality_token = "Fair", "yellow"

    qrst_metrics = _analyze_qrst_morphology(
        filtered_uv,
        sample_rate,
        peaks,
        rate_method,
        quality_label,
        quality_token,
        rr_cv,
    )
    hr_zone_key, hr_zone_label, _, hr_abnormal = hr_zone(final_bpm)

    return {
        "heart_rate_bpm": final_bpm,
        "heart_rate_source": hr_source,
        "heart_rate_method": rate_method,
        "heart_rate_method_detail": rate_method_detail,
        "heart_rate_confidence": heart_rate_confidence,
        "heart_rate_acf_correlation": (
            round(heart_rate_acf_correlation, 4)
            if heart_rate_acf_correlation is not None
            else None
        ),
        "artifact_period_sec": (
            round(artifact_period_sec, 4) if artifact_period_sec is not None else None
        ),
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
        **qrst_metrics,
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
        "heart_rate_method_detail": metrics.get("heart_rate_method_detail"),
        "heart_rate_confidence": metrics.get("heart_rate_confidence"),
        "heart_rate_acf_correlation": metrics.get("heart_rate_acf_correlation"),
        "artifact_period_sec": metrics.get("artifact_period_sec"),
        "qrst_status": metrics.get("qrst_status"),
        "qrst_status_label": metrics.get("qrst_status_label"),
        "qrst_confidence": metrics.get("qrst_confidence"),
        "qrst_scope": metrics.get("qrst_scope"),
        "qrst_reason": metrics.get("qrst_reason"),
        "qrst_findings": metrics.get("qrst_findings"),
        "qrst_findings_cn": metrics.get("qrst_findings_cn"),
        "qrs_duration_ms": metrics.get("qrs_duration_ms"),
        "qrs_amplitude_uv": metrics.get("qrs_amplitude_uv"),
        "st_deviation_uv": metrics.get("st_deviation_uv"),
        "t_wave_polarity": metrics.get("t_wave_polarity"),
        "qrst_beats_used": metrics.get("qrst_beats_used"),
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
            + ". Heart rate normally uses firmware agreement or R-peak detection "
            f"at {metrics['sample_rate_hz']:.0f} Hz with {REFRACTORY_SEC:.2f}s refractory period."
            + " QRST output is a conservative single-lead morphology screen, not a "
            + "12-lead diagnostic interpretation."
            + (
                " Peak-based estimate showed harmonic overcount on a periodic artifact "
                "train; rate falls back to the 0.5-3 Hz cardiac component and "
                "conflicting firmware HR is not allowed to override that correction."
                if metrics.get("heart_rate_method") == "slow_component"
                else ""
            )
        ),
    }