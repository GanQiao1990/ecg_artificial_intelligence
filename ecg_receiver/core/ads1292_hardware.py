"""
ADS1292R shield hardware constants and physical-unit conversions.

References (ProtoCentral / TI ADS1292R 盾板资料):
- ADS1292R 中文数据手册 ads1292.pdf
- ESP32_ADS1292R_ECG.ino: CONFIG1 DR=500 SPS, CH gain=6, serial 50 Hz (20 ms)
- ProtoCentral ESP32 examples: 57600 baud, ecgFilterout = int16(24-bit >> 8)
- Serial CSV: DATA,<timestamp_ms>,<ecg>,<resp>,<hr>,<status>

Voltage from 24-bit two's-complement code (TI datasheet):
    V_signal (V) = Code × (2 × VREF) / (Gain × 2^24)

Microvolts:
    µV = Code × 1e6 × (2 × VREF) / (Gain × 2^24)

ProtoCentral int16 (24-bit >> 8):
    µV = int16 × 256 × µV_per_LSB = int16 × µV_per_int16_count
    µV_per_int16_count ≈ 12.2  (gain=6, VREF=2.4 V)

Heart rate from R-R interval (samples):
    RR_sec = Δn / F_s
    HR_bpm = 60 / RR_sec = 60 × F_s / Δn

F_s MUST be the effective **serial stream rate** (lines/s), not the internal ADC
rate when firmware decimates (e.g. 500 SPS ADC → 50 Hz UART).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

# ADS1292R CONFIG1 data rates (register DR[2:0])
ADS1292_ADC_RATES_HZ = (125, 250, 500, 1000, 2000, 4000, 8000)

# Effective rates seen on UART for this project / 盾板资料
ADS1292_STREAM_RATES_HZ = (50, 125, 250, 500, 1000)

# Union for UI snap (stream + ADC)
ADS1292_SAMPLE_RATES = ADS1292_STREAM_RATES_HZ

# ProtoCentral full-rate stream; override via Settings if using 50 Hz CSV firmware
DEFAULT_SAMPLE_RATE = 500.0

VREF_VOLTS = 2.4
ADC_BITS = 24
ADC_FULL_SCALE = float(2 ** ADC_BITS)
INT16_SHIFT_BITS = 8  # ProtoCentral: ecgFilterout = (int16_t)(raw24 >> 8)


def adc_counts_to_uv_per_lsb(pga_gain: int = 6, vref: float = VREF_VOLTS) -> float:
    """µV per one 24-bit ADC LSB at given PGA gain."""
    if pga_gain <= 0:
        pga_gain = 6
    volts_per_lsb = (2.0 * vref) / (pga_gain * ADC_FULL_SCALE)
    return volts_per_lsb * 1e6


def int16_shifted_uv_per_count(pga_gain: int = 6, vref: float = VREF_VOLTS) -> float:
    """µV per ProtoCentral int16 serial count (24-bit code >> 8)."""
    return adc_counts_to_uv_per_lsb(pga_gain, vref) * (2 ** INT16_SHIFT_BITS)


def adc_counts_to_microvolts(code: float, pga_gain: int = 6, vref: float = VREF_VOLTS) -> float:
    """Convert signed 24-bit ADS1292R code to microvolts."""
    return float(code) * adc_counts_to_uv_per_lsb(pga_gain, vref)


def adc_counts_to_millivolts(code: float, pga_gain: int = 6, vref: float = VREF_VOLTS) -> float:
    return adc_counts_to_microvolts(code, pga_gain, vref) / 1000.0


def heart_rate_bpm_from_rr_samples(delta_samples: float, sample_rate_hz: float) -> float:
    """HR = 60 / (Δn / F_s)."""
    if delta_samples <= 0 or sample_rate_hz <= 0:
        return 0.0
    rr_sec = delta_samples / sample_rate_hz
    if rr_sec <= 0:
        return 0.0
    return 60.0 / rr_sec


def stream_rate_from_timestamp_ms(median_dt_ms: float) -> Optional[float]:
    """Infer UART stream rate: F_s = 1000 / median(Δt_ms)."""
    if median_dt_ms <= 0:
        return None
    rate = 1000.0 / median_dt_ms
    if 20.0 <= rate <= 2000.0:
        return rate
    return None


def snap_stream_rate(rate: Optional[float]) -> Optional[float]:
    if rate is None or rate <= 0:
        return None
    return float(min(ADS1292_SAMPLE_RATES, key=lambda r: abs(r - rate)))


def expected_hr_error_ratio(configured_hz: float, actual_hz: float) -> float:
    """
    Ratio HR_wrong / HR_true when sample rate is misconfigured.

    Misconfigured high → HR reads high (ratio = F_cfg / F_actual).
    """
    if actual_hz <= 0:
        return 1.0
    return configured_hz / actual_hz


@dataclass(frozen=True)
class FirmwareProfile:
    """Per-firmware serial + scaling defaults from 盾板资料."""

    name: str
    label: str
    baud_rate: int
    stream_rate_hz: float
    pga_gain: int
    # Multiply raw serial ECG integer by this to get µV
    uv_per_count: float
    notes: str
    uses_int16_shift: bool = False


def _build_firmware_profiles() -> Dict[str, FirmwareProfile]:
    uv_lsb = adc_counts_to_uv_per_lsb(6)
    uv_int16 = int16_shifted_uv_per_count(6)
    return {
        "protocentral_500": FirmwareProfile(
            name="protocentral_500",
            label="ProtoCentral ESP32 · 500 Hz · 57600",
            baud_rate=57600,
            stream_rate_hz=500.0,
            pga_gain=6,
            uv_per_count=round(uv_int16, 2),
            notes="ecgFilterout int16 (24-bit>>8), PGA gain 6, VREF 2.4 V",
            uses_int16_shift=True,
        ),
        "esp32_csv_50": FirmwareProfile(
            name="esp32_csv_50",
            label="ESP32 CSV 固件 · 50 Hz 串口 · 115200",
            baud_rate=115200,
            stream_rate_hz=50.0,
            pga_gain=6,
            uv_per_count=uv_lsb,
            notes="ESP32_ADS1292R_ECG.ino DATA_TRANSMISSION_INTERVAL=20 ms",
        ),
        "esp32_csv_500": FirmwareProfile(
            name="esp32_csv_500",
            label="ESP32 CSV 固件 · 500 Hz 全速率 · 115200",
            baud_rate=115200,
            stream_rate_hz=500.0,
            pga_gain=6,
            uv_per_count=uv_lsb,
            notes="每 DRDY 一行 DATA（无 20 ms 节流）",
        ),
        "stm32_peiki": FirmwareProfile(
            name="stm32_peiki",
            label="STM32 PEIKI 盾板 · 500 Hz · 115200",
            baud_rate=115200,
            stream_rate_hz=500.0,
            pga_gain=6,
            uv_per_count=uv_lsb,
            notes="ADS1292R程序.zip CONFIG1 DATA_RATE_500SPS",
        ),
    }


FIRMWARE_PROFILES: Dict[str, FirmwareProfile] = _build_firmware_profiles()


def get_firmware_profile(name: Optional[str]) -> FirmwareProfile:
    if name and name in FIRMWARE_PROFILES:
        return FIRMWARE_PROFILES[name]
    return FIRMWARE_PROFILES["protocentral_500"]


def apply_firmware_profile(name: Optional[str]) -> Dict[str, float]:
    """Return settings dict fragment for baud, stream rate, and amplitude scale."""
    profile = get_firmware_profile(name)
    return {
        "firmware_profile": profile.name,
        "baud_rate": profile.baud_rate,
        "sample_rate_hz": profile.stream_rate_hz,
        "uv_per_count": profile.uv_per_count,
    }