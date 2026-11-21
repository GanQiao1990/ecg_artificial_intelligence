# DEBUG ledger — res 错误信息处理形式 fix (2026-11-20)

## Task
User: "帮我修我们的res错误信息处理形式" — fix the error-message handling format for the
diagnosis API response path, tested against a real measurement:
`ecg_measurement (1).csv` (23-year-old woman, 正常心脏/normal heart).

## Assumptions (pre-execution)
- "res" = the `resp`/`response` object in `LLMDiagnosisClient.diagnose_heart_condition`
  and the `{error: ...}` JSON returned by `/api/diagnosis/run` & `/api/setup`.
- Current behaviour: raw Python exception strings (`HTTPError: 401 Client Error...`,
  `KeyError: 'choices'`, `JSONDecodeError`, `ConnectionError`) bubble to the UI
  unchanged → unreadable for clinical staff.
- UI language: web app is English-labeled but AI reports are Chinese-primary;
  error text → Chinese primary (consistent with `primary_diagnosis`).

## Evidence consulted
- `ecg_receiver/core/llm_diagnosis.py` `diagnose_heart_condition` L164-175:
  no try/except; `requests.post`, `raise_for_status`, `resp.json()`,
  `result["choices"][0]` all propagate raw exceptions.
- `web_app.py` L384-388: `run_diagnosis` returns `{"error": str(e)}` 500.
  L315-325 `/api/setup` same raw `str(e)`; L331-338 serial connect has no message.
- `templates/index.html` L743-764: frontend renders `d.error` verbatim; L695-699
  renders `d.message`.
- `gui_tkinter/main_window_modern.py` L82-84: `error_callback(str(e))` — fixing
  `str(exception)` fixes the Tkinter path for free.
- `ecg_receiver/diagnosis.py` = legacy GeminiECGDiagnosisClient, imported only by
  dead `gui/main_window.py` path (`from ecg_diagnosis import ...` broken import).
- Test CSV fetched from Windows host via ego-lan → `data/ecg_measurement_woman23_normal.csv`:
  UTF-8 BOM, 5000 samples @500 Hz (10 s), `ecg_mV` column, COM3 capture,
  device-exported HR 110.29 bpm / "Irregular" / Excellent quality.

## Decision
Introduce `DiagnosisAPIError` (code + Chinese user_message + technical detail) in
`llm_diagnosis.py`; classify: timeout / connect / auth(401,403) / not_found(404)
/ rate_limited(429) / server(5xx) / http(other) / bad_response(non-JSON or
missing choices) / empty_response. `str(e)` = user_message so Tkinter GUI
inherits. Flask routes return `{error, error_code, detail, severity:"unknown"}`
with mapped HTTP status. Frontend shows code badge + detail line.

## Measured results (2026-11-21)
Mock OpenAI-compatible server (`/tmp/test_ecg_errors.py`), all branches pass:
- 401/403 → `auth_failed` "API 密钥无效或已过期…"; provider `error.message` preserved in `detail`
- 404 → `not_found`; 429 → `rate_limited`; 5xx → `server_error`; other → `http_error`
- 200 + non-JSON body → `bad_response` (non-JSON); 200 missing `choices` → `bad_response`;
  200 empty content → `empty_response`
- unreachable host → `connect_failed`; slow server → `timeout`
- happy path still parses diagnosis JSON correctly (severity=normal 窦性心律)

Live Flask run (`web_app.py --port 5055`):
- `/api/diagnosis/run` with empty buffer → HTTP 400 `{"error":"心电数据不足…","error_code":"bad_input"}`
- demo data + placeholder `.env.example` key against real api.gptnb.ai → HTTP 401
  `{"error":"API 密钥无效或已过期…","error_code":"auth_failed","detail":"HTTP 401 — (request id: 2026…)"}`
  (previously the UI showed raw `401 Client Error: Unauthorized for url: https://…`)

Real CSV check — `data/ecg_measurement_woman23_normal.csv` (23F, 正常心脏):
- 5000 samples @500 Hz = 10.0 s, `ecg_mV` column, UTF-8 BOM
- `preprocess_for_llm` → HR 108.5 bpm (computed; file's own column reads 110.29 — consistent),
  17 QRS, "Irregular" (CV 0.19), Excellent quality, 0.50 mV p-p

## Adversarial review
1. `str(DiagnosisAPIError)` = Chinese user message → Tkinter `error_callback(str(e))`
   inherits clean text automatically. `detail` retained for logs.
2. `requests.Timeout`/`ConnectionError`/`RequestException` top-level aliases verified live.
3. `resp.json()` failure → `ValueError` (JSONDecodeError subclasses it) → covered.
4. Backward compat: `error`, `severity:"unknown"`, `message` keys kept where old
   frontend reads them; new fields (`error_code`, `detail`) are additive.
5. Legacy `ecg_receiver/diagnosis.py` left untouched — only referenced by dead
   `gui/main_window.py` (`from ecg_diagnosis import …` broken import).
6. Open: `.env.example` auto-loads placeholder `your-api-key-here` as a real key —
   users hit `auth_failed` instead of "API 未配置". Pre-existing behaviour,
   message now explains the actual cause.

## .env write-rules (added 2026-11-21)
- `.env` (gitignored working file) and `.env.example` (committed template) now
  carry the same Chinese comment block: load order, KEY=VALUE format,
  restart requirement, variable priority chains (verified against
  `app_config.get_api_config`), URL auto-completion rules (verified against
  `LLMDiagnosisClient.__init__`), and model presets.
- `.env` ships with `OPENAI_API_KEY=` empty → verified `get_api_config()`
  returns `''` → UI honestly shows "API not configured" instead of the
  placeholder-auth_failed trap documented above.

## Heart-rate miscalculation investigation (2026-11-21, user: "心率计算有问题，正常人心跳应该在70左右")

### Evidence gathered
- Recomputed peaks on the woman-23 CSV: 17 peaks/10 s, median RR 0.553 s → 108.5 bpm
  (file's own column: 110.29, source=`computed` — same algorithm, NOT independent firmware).
- Waveform morphology: ~38 identical narrow biphasic spikes (~10 ms, ~0.17-0.20 mV
  excursion) quasi-uniformly spaced ~0.26 s, riding on a slower ~1 Hz wave.
  Slew events every ~0.26 s; detected "beats" are every-other spike → harmonic.
- FFT: strong 3.7-3.8 Hz line (artifact train) + 0.6-1.1 Hz cluster (cardiac).
- ACF: dominant long-lag peak 1.048 s (57 bpm); lowpass<2 Hz peaks median RR 0.98 s → 61 bpm.
- QT-consistency check: spike→T-drop 0.27 s fits QTc≈0.37 s ONLY at ~110 bpm —
  but that assumes spikes are QRS; rejected because 4 identical spikes/cycle is
  not physiological and no P/QRS/T structure exists at the 0.52 s scale.
- Conclusion: recording is artifact-dominated (tremor/electrode/motion class);
  true cardiac rate ≈ 51-61 bpm depending on estimator — consistent with user's
  "~70" expectation, NOT 110. The 110 was a harmonic of the 3.8 Hz artifact train.

### Root causes found in detect_r_peaks
1. |baseline-removed| envelope + amplitude-ranked NMS cannot distinguish QRS from
   artifact spikes → samples the 3.8 Hz train at ~half rate → ~2x HR.
2. T-wave double-count: T-wave lands just outside the 0.42 s refractory at
   ~50-70 bpm → counted as a beat → ~2x HR on clean synthetic ECGs (measured:
   synth50→100, synth60→120, synth70→140 pre-fix).
3. No quality penalty: artifact-dominated trace reported "Excellent".

### Fix implemented (ecg_signal.py)
- `filter_ecg_band`: FFT bandpass 0.5-40 Hz + optional mains notch,
  reflect-padded (no scipy dependency — numpy only).
- `detect_mains_interference`: auto 50/60 Hz narrowband detection (>6x neighbour median).
- `hf_noise_ratio`: spectral power >40 Hz share (QC metric).
- `detect_r_peaks` now runs on the filtered clean band.
- `_envelope_short_period` + `_broad_peak_fraction`: two overcount signatures —
  (a) harmonic: detected RR ≈ 1.7-2.5x a stronger fast envelope periodicity;
  (b) t_wave_alt: 25-75% of detected peaks are broad (>80 ms).
- `_slow_component_rate`: cardiac rate from <3 Hz component (P/T-wave repetition),
  numpy-only FFT lowpass + greedy peak pick.
- Override rule (all must hold): signature fires AND slow_bpm exists AND
  1.5 ≤ computed/slow ≤ 2.6 AND disagreement >25% → use slow rate, method=
  "slow_component", quality capped at Fair.
- `hr_zone`: clinical resting-HR zones (<50 severe brady / 50-60 relative /
  60-100 normal / 100-120 mild tachy / 120-150 tachy / >150 severe tachycardia).
- `preprocess_for_llm` now emits: heart_rate_method, heart_rate_zone(+label),
  heart_rate_abnormal, mains_interference_hz, hf_noise_ratio, and the waveform
  snippet is taken from the FILTERED band (was raw counts).
- `llm_diagnosis.py` summary line now shows method/zone/abnormal/mains/hf;
  full JSON dump carries all fields automatically.
- `web_app.py` /api/ecg/stream metrics + index.html overlay expose zone label.

### Measured results (all verified, 0 failures / 20 checks)
- woman23 CSV: 108.5 → **50.9 bpm** (slow_component), zone=心动过缓(偏低),
  abnormal=True, rhythm=Irregular, quality=Excellent→**Fair**, qrs 17→7.
- Synthetics 50-140 bpm: ALL correct post-fix (50.1/60.0/69.9/90.1/100/109.9/
  120/129.9/140.2) — T-wave double-count at 50-70 FIXED, no high-rate regression
  (slow estimator halves at RR<0.5 s but envACF ratio ~1.08 keeps guard closed).
- 50 Hz mains @30-300 µV: detected, notch applied, HR stays 70, quality→Fair.
- flat/short buffers: no crash, hr=None.

### Adversarial review (this change set)
1. envACF window widened 2.2→2.5 after filtered ratio measured 2.24 — synth90/100
   sit at ratio 2.00 but slow≈computed so the 1.5-2.6 slow-ratio guard blocks
   override. Verified.
2. Always-compare (no gates) REJECTED: synth120-140 would falsely halve because
   the slow estimator skips alternate T-waves at RR<0.5 s. Gates required.
3. wide-QRS rhythms: broad_frac→~1.0 outside [0.25,0.75] → t_wave_alt can't fire.
4. Real 150+ bpm: refractory 0.42 s already caps detection ~143 bpm (pre-existing,
   documented in code comment) — unchanged.
5. FFT brick-wall ringing: mitigated by reflect padding; QC metrics remain on raw.
6. Open: 10 s window at ~52 bpm = ~9 cycles — estimate uncertainty ±10-15%;
   honest "Irregular"/"Fair" labels carry that uncertainty to the LLM.
