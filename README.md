# ECG AI Clinical Intelligence Platform

Real-time ECG visualization and AI-assisted interpretation for **ESP32 + ADS1292R** medical acquisition hardware.

> **Safety notice:** Decision-support only. Not a substitute for licensed clinical judgment or emergency care.

## Repository

[GitHub](https://github.com/GanQiao1990/ecg_receiver_standalone-)

## Highlights (v3.0)

- **Heart-rate calibration fix** — default sample rate is now **500 Hz** (ADS1292R typical), with auto-estimation from timestamps and packet timing
- **Robust R-peak detection** — 0.42 s refractory period + amplitude NMS to avoid T-wave double counting
- **Firmware HR fusion** — uses `heart_rate` from `DATA,...` CSV when available
- **Bilingual clinical UI** — Chinese-primary labels with structured AI reports
- **Multi-model LLM** — OpenAI-compatible APIs (Gemini, GPT-4o, DeepSeek, Qwen, local)

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