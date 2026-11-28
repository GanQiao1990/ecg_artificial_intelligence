# ECG AI Clinical Intelligence Platform

Real-time ECG visualization and AI-assisted interpretation for **ESP32 + ADS1292R** medical acquisition hardware.

> **Safety notice:** Decision-support only. Not a substitute for licensed clinical judgment or emergency care.

## Repository

[GitHub](https://github.com/GanQiao1990/ecg_receiver_standalone-)

## Highlights (v3.0)

- **Heart-rate calibration fix** — default sample rate is now **500 Hz** (ADS1292R typical), with auto-estimation from timestamps and packet timing
- **Robust R-peak detection** — 0.42 s refractory period + amplitude NMS to avoid T-wave double counting
- **Artifact-aware fallback** — periodic motion/tremor spike trains trigger an autocorrelation-based low-frequency cardiac estimate with confidence and artifact-period metadata
- **Firmware HR fusion** — uses `heart_rate` from `DATA,...` CSV when available
- **Bilingual clinical UI** — Chinese-primary labels with structured AI reports
- **Multi-model LLM** — OpenAI-compatible APIs (Gemini, GPT-4o, DeepSeek, Qwen, local)

## Signal Acquisition & Heart-Rate Principles

### Hardware chain

| Stage | Detail |
|-------|--------|
| Front-end IC | Texas Instruments **ADS1292R**: 24-bit ΔΣ ADC, PGA gain 6, VREF 2.4 V |
| ADC rate | CONFIG1 DR field: 125–8000 SPS; typical firmware **500 SPS** |
| UART stream rate | May differ from ADC rate (firmware decimation, e.g. 500 SPS → 50 Hz CSV). **HR math must use the serial stream rate, not the ADC rate.** |
| Scaling | ProtoCentral sends `int16 = 24-bit code >> 8` ≈ **12.2 µV/count**; `µV = Code × 2·VREF / (Gain·2²⁴) × 10⁶` |
| Protocol | `DATA,<timestamp_ms>,<ecg>,<resp>,<hr>,<status>` @ 57600 baud; plain numeric lines also accepted |

### Sample-rate confirmation & auto-adjust

- **Configured**: default 500 Hz (`DEFAULT_SAMPLE_RATE`), overridable per firmware profile.
- **Auto mode** (Web default; `ECG_SAMPLE_RATE_MODE=configured` to disable): `SampleRateTracker` estimates the true stream rate from device `timestamp_ms` deltas and/or packet arrival timing, then snaps to {50, 125, 250, 500, 1000} Hz.
- **HR cross-check** (`correct_sample_rate_from_hr`): when firmware HR and R-peak HR disagree by ~2×/½×, the rate is inferred wrong, corrected ×2 or ÷2, snapped, and peaks re-detected.

### Signal chain (`ecg_signal.py`)

1. Raw counts → µV via profile `uv_per_count`.
2. Baseline removal (~0.6 s moving average).
3. FFT bandpass **0.5–40 Hz** with reflect padding.
4. **Mains detection** (50/60 Hz): spectral peak >6× local median **and** amplitude ≥18 µV (rejects ~12 µV QRS-harmonic lines); hit ⇒ `mains_interference_hz` set, quality capped at Fair.
5. `hf_noise_ratio`: >40 Hz spectral power share for LLM quality context.

### Heart-rate detection & calculation

1. **R peaks** on the filtered trace: baseline-removed, polarity-agnostic envelope, amplitude-ranked NMS with **0.42 s refractory** (suppresses T/P double counts).
2. **HR** = `60 / median(RR)`; RR coefficient of variation → rhythm grade.
3. **Artifact/overcount guards**: harmonic artifact train (envelope short-period ratio 1.7–3.2) or T-wave double-count (broad-peak fraction 0.25–0.75) ⇒ fallback to the **0.5–3 Hz cardiac-component autocorrelation** (`heart_rate_method=slow_component`, detail usually `acf`). Because artifact-corrupted peaks cannot establish RR rhythm, rhythm is reported as **Undetermined** and `rr_variability` is omitted in this fallback.
4. **Fallback confidence**: the normalized autocorrelation peak is exposed as `heart_rate_acf_correlation` and mapped to `heart_rate_confidence` (`high` ≥0.25, `medium` ≥0.10, otherwise `low`); low confidence caps quality at Poor and all corrected traces cap at Fair. `artifact_period_sec` reports the detected periodic artifact interval.
5. **HR zones**: <50 severe brady / 50–60 relative / 60–100 normal / 100–120 mild tachy / 120–150 tachy / >150 severe.
6. **Source fusion**: device `hr` field and computed HR are fused only when they agree; normally a large disagreement trusts firmware as a sample-rate fallback, **except** during artifact correction, where the corrected low-frequency rate wins because firmware HR is derived from the same contaminated signal. `heart_rate_source` records which was used.

The LLM receives all structured metrics plus a filtered waveform snippet and is instructed to adopt the system HR rather than recompute it.

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python launch_modern_gui.py
```

## Workflow

1. Select serial port → **Connect**
2. Verify **sample rate** (default 500 Hz; adjust in Settings if needed)
3. Check **heart-rate source** (device / R-peak / fused)
4. Configure API key → **Setup API**
5. Collect ~10 s of stable trace → **分析心电图 / Analyze**
6. Optional: **Auto Mode**, **Recording**, **Export Report**

## Supported Serial Formats

**CSV (recommended):**

```text
DATA,timestamp_ms,ecg_value,resp_value,heart_rate,status
```

**Numeric (one sample per line):**

```text
1024
1050
```

## Heart Rate Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| ~2× too high | Wrong sample rate (250 vs 500 Hz) or double peak detection | Set Sample Rate to 500 in Settings |
| ~½ too low | Sample rate set too high | Lower Sample Rate to match firmware |
| Unstable | Poor electrode contact | Improve placement; check signal quality |

## Project Layout

- `launch_modern_gui.py` — modern clinical GUI entry
- `ecg_receiver/core/ecg_signal.py` — unified HR / R-peak / sample-rate logic
- `ecg_receiver/core/llm_diagnosis.py` — LLM diagnosis client
- `ecg_receiver/gui_tkinter/` — diagnosis-focused desktop UI

## Verification

```bash
python -m py_compile ecg_receiver/core/ecg_signal.py
python -m py_compile launch_modern_gui.py
```

See **README_CN.md** for the full Chinese user manual.