#!/usr/bin/env python3
"""Sanity-check ADS1292R physical formulas against 盾板资料 constants."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ecg_receiver.core.ads1292_hardware import (
    FIRMWARE_PROFILES,
    VREF_VOLTS,
    adc_counts_to_uv_per_lsb,
    heart_rate_bpm_from_rr_samples,
    int16_shifted_uv_per_count,
)


def main() -> int:
    uv_lsb = adc_counts_to_uv_per_lsb(6, VREF_VOLTS)
    uv_int16 = int16_shifted_uv_per_count(6, VREF_VOLTS)

    # TI datasheet check: gain=6, VREF=2.4 → ~0.04768 µV/LSB
    assert 0.047 < uv_lsb < 0.048, f"unexpected µV/LSB: {uv_lsb}"

    # ProtoCentral int16 >>8 → ~12.2 µV/count (盾板资料)
    assert 12.0 < uv_int16 < 12.5, f"unexpected µV/int16: {uv_int16}"

    # HR: 72 BPM @ 500 Hz → Δn = 500 * 60/72 ≈ 416.67 samples
    hr = heart_rate_bpm_from_rr_samples(500.0 * 60.0 / 72.0, 500.0)
    assert 71.5 < hr < 72.5, f"unexpected HR: {hr}"

    proto = FIRMWARE_PROFILES["protocentral_500"]
    assert abs(proto.uv_per_count - round(uv_int16, 2)) < 0.05

    esp50 = FIRMWARE_PROFILES["esp32_csv_50"]
    assert esp50.stream_rate_hz == 50.0
    assert esp50.baud_rate == 115200

    print("ADS1292R formula verification: OK")
    print(f"  µV/LSB (gain=6):     {uv_lsb:.6f}")
    print(f"  µV/int16 (>>8):      {uv_int16:.3f}")
    print(f"  HR test 72 BPM:      {hr:.2f}")
    print(f"  ProtoCentral scale:  {proto.uv_per_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())