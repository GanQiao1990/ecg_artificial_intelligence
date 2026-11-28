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

---

## 2026-11-21 — Frontend professional redesign + LLM robustness fixes

### Assumptions (pre-execution)
- A1: classmate test happens in a desktop browser on Windows → keep single-file
  vanilla HTML/CSS/JS, no build step, dark medical-monitor theme acceptable.
- A2: backend already exposes all needed metric fields; only additive changes.
- A3: alert()/native dialogs read as unprofessional → replace with toast system.

### Changes
- `templates/index.html` rewritten: sticky header w/ live clock + status pills
  (conn/demo/API), ECG monitor card (chips, scrollable canvas, signal-alert
  strip), 3-col band (Live Vitals hero HR + zone chip + 6 tiles / Patient form
  w/ localStorage persistence / AI Service card), full-width AI Diagnosis card
  (severity badge + confidence bar + 2x2 section grid + disclaimer), toasts,
  copy-report button, inline SVG favicon (kills 404).
- `web_app.py` /api/ecg/stream: added heart_rate_zone, *_color_token,
  rr_variability passthrough.
- `ecg_signal.py detect_mains_interference`: added MIN_MAINS_AMP_UV=18 floor —
  demo QRS harmonic splatter produced a narrow ~11.9µV line at 60 Hz that
  false-triggered mains detection (quality capped Fair on clean signal).
- `llm_diagnosis.py`: max_tokens 2048→8192 default + ECG_LLM_MAX_TOKENS env;
  auto-retry once at 2x budget (cap 32768) on finish_reason=length + empty
  content; new `truncated_response` error code; request block refactored into
  _build_payload/_post_chat/_extract_content.
- `app_config.py`: ECG_LLM_MAX_TOKENS + ECG_LLM_TIMEOUT_SEC env parsing.
- `web_app.py setup_api`: max_tokens/timeout_sec passthrough; auto-connect
  passes cfg values; truncated_response→502.
- `.env` / `.env.example`: documented both new vars; .env sets
  ECG_LLM_MAX_TOKENS=16384 + ECG_LLM_TIMEOUT_SEC=180 for glm-5.3-flash.

### Measured results
- Demo metrics: HR 72.0, zone 正常范围, Excellent, Regular, R-peaks — clean.
- Mains detection post-fix: demo+EMG40 → None (was 60.0 FP); 50Hz@20/50µV →
  50.0; 60Hz@50µV → 60.0; 15µV below floor → None (bandpass covers it anyway).
- Live UI verify (Playwright): conn pill "Demo Mode" on fresh load, Analyze
  enabled, vitals populate, alerts strip hidden on clean signal, API shows
  "Connected — glm-5.3-flash".
- Diagnosis run 1 (max_tokens 2048): empty_response — provider returned
  finish_reason=length, content="", reasoning_content truncated mid-sentence.
  UI rendered ERROR badge + empty_response code + friendly msg + raw detail.
- Diagnosis run 2 (8192 default): timeout @60s — reasoning model too slow.
- Fix: ECG_LLM_MAX_TOKENS=16384 + ECG_LLM_TIMEOUT_SEC=180 in .env.

### Adversarial review
1. Amplitude floor 18µV vs demo harmonic 11.9µV — margin 1.5x; real mains
   <18µV missed but 0.5-40Hz bandpass removes it regardless → flag-only loss.
2. hf_noise_ratio stays a displayed metric but UI alerts only >0.45 — clean
   demo measures 0.28 (QRS morphology splatter, not noise) so threshold avoids
   false alarms; pure-noise signals (~0.85) still trip quality=Poor upstream.
3. Token retry doubles cost only on actual truncation; non-reasoning models
   unaffected (finish_reason=stop → no retry).
4. connStatus now derived server-state each poll — watchdog error persists
   correctly; manual connect failure still toasts (serial_error only set by
   watchdog path).
5. analyzing flag prevents poll-driven re-enable of Analyze mid-request.

---

## 2026-11-21 (cont.) — Web sample-rate auto mode + principles docs

- web_app.py: SampleRateTracker wired in (was Tkinter-only). effective_sample_rate()
  returns configured rate in demo/disconnected, else tracker.effective_rate(
  prefer_auto) — auto is default via ECG_SAMPLE_RATE_MODE (auto|configured).
- _serial_loop now feeds tracker.on_sample(timestamp_ms) per line and captures
  parsed.device_hr_bpm → last_device_hr_bpm → passed to compute_ecg_metrics
  (enables device-HR fusion + 2x sample-rate cross-correction on serial too).
- connect_serial resets tracker + device HR; stream exposes sample_rate_mode,
  sample_rate_configured, device_hr_bpm, sample_rate_correction.
- Verified: tracker 20ms timestamps → 50.0 Hz; demo stream rate=500 mode=auto,
  device_hr=72 fused, quality Excellent.
- README.md + README_CN.md: new "Signal Acquisition & Heart-Rate Principles"
  section — ADS1292R chain, stream-vs-ADC rate, tracker/snap/cross-check,
  filter chain, R-peak/NMS/refractory, overcount guards, HR zones, LLM fields.

---

## 2026-11-28 — Full Windows measurement-set validation and possible revision

### Deeper-question check (Rule 1)
Recurring theme: the user repeatedly asks whether the code was revised from the
*latest data* and then whether GitHub reflects that revision. Candidate deeper
question: **is the ECG pipeline validated across the complete real measurement
set, and is the public repository state an accurate representation of that
validation — rather than merely being updated for one convenient CSV?**
This task is an instance of that deeper question, so execution must first
establish coverage across all user-identified measurement files before deciding
whether another code revision is justified.

### Assumptions (pre-execution)
- A1: The six Windows paths supplied by the user on host `10.110.3.80` are real
  measurement exports and are distinct from, or at least not fully represented
  by, the single existing local file `data/ecg_measurement_woman23_normal.csv`.
- A2: File names alone do not establish ordering or physiological condition;
  CSV metadata/content must be inspected before inferring anything.
- A3: Code changes are justified only if multiple real measurements expose a
  reproducible algorithmic discrepancy, or one measurement exposes a clear
  correctness bug with a mechanistic explanation.
- A4: Pulling files from the user's own Windows host is authorized by the user;
  access will be read-only on the source files.
- A5: Existing local code state is clean and equivalent to `origin/main` at
  commit `3bedcc6` before this task.

### Evidence consulted before execution
- User-supplied Windows paths:
  - `D:\腾讯电脑管家软件搬家\C盘清理文件搬家\xwechat_files\qiaoshizhishen_c075\msg\file\2026-11\ecg_measurement (1).csv`
  - `D:\腾讯电脑管家软件搬家\C盘清理文件搬家\xwechat_files\qiaoshizhishen_c075\msg\file\2026-11\ecg_measurement (2).csv`
  - `D:\腾讯电脑管家软件搬家\C盘清理文件搬家\xwechat_files\qiaoshizhishen_c075\msg\file\2026-11\ecg_measurement (3).csv`
  - `D:\腾讯电脑管家软件搬家\C盘清理文件搬家\xwechat_files\qiaoshizhishen_c075\msg\file\2026-11\ecg_measurement_1.csv`
  - `D:\腾讯电脑管家软件搬家\C盘清理文件搬家\xwechat_files\qiaoshizhishen_c075\msg\file\2026-11\ecg_measurement_2.csv`
  - `D:\腾讯电脑管家软件搬家\C盘清理文件搬家\xwechat_files\qiaoshizhishen_c075\msg\file\2026-11\ecg_measurement_3.csv`
- Existing ledger sections above: the 2026-11-20/21 fixes were validated mainly
  against `data/ecg_measurement_woman23_normal.csv` plus synthetic signals.
- Git evidence already measured this session: working tree clean; only local
  branch `main` at `3bedcc6`; no unpushed commits; PR #1 already merged.
- LAN access notes in `/home/qiao/skills_2026/ego-lan-skill/RECOVERY.md`:
  SSH to `dell@10.110.3.80` uses key auth; default shell is cmd.exe;
  PowerShell file execution is preferred over complex inline quoting.

### Planned execution decision
Use SSH/PowerShell, not browser automation, because this is Windows file access,
not a browser task. Copy source CSVs read-only into an ASCII-only temporary
Windows directory, transfer them to Linux under `data/imported_20261128/`,
verify SHA-256 on both sides, inspect schemas/metadata, then run the current
pipeline over all files before deciding whether code changes are warranted.

### In-loop evidence after transfer (2026-11-28)
- SHA-256 verification matched Windows staged copies exactly for all six files.
  `ecg_measurement_win_1.csv` is byte-identical to the pre-existing
  `data/ecg_measurement_woman23_normal.csv`; files 2-6 are new evidence.
- All six are real serial captures (`demo_mode=False`, `serial_connected=True`,
  COM3), 5000 rows each at 500 Hz (10 s). Files 4-6 were captured back-to-back
  within ~25 s, so their heart-rate estimates should be mutually consistent.
- Current pipeline outputs on files 4-6 are unstable: 56.8 / 65.1 / 63.8 bpm, all through `slow_component`, despite the near-contiguous
  acquisition. File 3 remains at 111.3 bpm because the existing slow-component
  peak picker fails (`slow=None`) and the overcount guard cannot engage.
- Exploratory autocorrelation of the <3 Hz component gives a stable physiological
  period across files 4-6: 58.6 / 58.7 / 58.6 bpm with autocorrelation 0.43-0.50.
  It also gives 55.7 bpm for file 1 and 56.2 bpm for file 3 (weak but nonzero).
- Decision point: replace/augment peak-picking in the slow-component fallback
  with autocorrelation as the primary rate estimator. Do not treat unstable
  artifact-corrupted slow peaks as evidence of clinical irregularity unless
  their count and median interval are consistent with the autocorrelation period.

### Revision implemented (2026-11-28)
- `_envelope_short_period` now selects the strongest autocorrelation peak inside
  the artifact-period search window rather than the first side lobe; the accepted
  harmonic ratio was widened to 1.7-3.2 because refractory NMS can undersample a
  periodic impulse train at either half or one-third rate.
- `_slow_component_rate` now uses a 0.5-3 Hz band and autocorrelation as the
  primary rate estimator (`heart_rate_method_detail=acf`). Low-frequency peak
  positions are retained only as supporting metadata; their intervals are not
  treated as clinical RR intervals because they may be P/T waves or artifact
  ringing. Rhythm therefore becomes `Undetermined` and `rr_variability` is null
  whenever this fallback is used.
- Fallback metadata added end-to-end: `heart_rate_method_detail`,
  `heart_rate_confidence` (high ≥0.25, medium ≥0.10, low otherwise),
  `heart_rate_acf_correlation`, and `artifact_period_sec`.
  Quality is capped at Fair for any corrected recording and Poor for
  low-confidence corrections.
- Agreement case added: when a periodic artifact is detected but QRS-based and
  low-frequency rates already agree within 20%, the low-frequency estimate still
  becomes the reported rate because the peak/rhythm evidence is contaminated.
- Firmware-HR interaction fixed: after artifact correction, a conflicting
  firmware HR cannot undo the corrected low-frequency estimate; fusion is used
  only when the two sources agree within 12%. Rationale: the firmware HR is
  derived from the same artifact-contaminated frontend signal.
- Autocorrelation implementation changed from direct `np.correlate` to FFT.
  Measured metric-computation time on `win_4`: mean 13.02 ms before, 6.04 ms
  after (50 iterations, 5000 samples).
- Web stream/API, frontend warning/method tile, LLM prompt, README.md and
  README_CN.md were updated to expose and explain the new fields.
- Permanent regression tests added in `tests/test_ecg_signal_artifact.py` using
  synthetic artifact-contaminated signals only; no patient waveform data are
  committed. `data/imported_20261128/manifest.json` is a sanitized hash/provenance
  manifest; raw CSVs remain untracked under the existing `data/**/*.csv` rule.

### Measured results after revision (2026-11-28)
Real measurement files, `ecg_mV*1000` → µV, 500 Hz:

| file | saved HR bpm | revised HR bpm | method/detail | confidence | ACF corr | artifact s | rhythm | quality |
|---|---:|---:|---|---|---:|---:|---|---|
| win_1 | 110.29 | 55.74 | slow_component/acf | medium | 0.1916 | 0.2678 | Undetermined | Fair |
| win_2 | 96.77 | 88.09 | slow_component/acf | high | 0.2997 | 0.2513 | Undetermined | Fair |
| win_3 | 110.70 | 55.49 | slow_component/acf | low | 0.0685 | 0.2423 | Undetermined | Poor |
| win_4 | 114.50 | 58.24 | slow_component/acf | high | 0.4719 | 0.2677 | Undetermined | Fair |
| win_5 | 111.11 | 59.66 | slow_component/acf | high | 0.4277 | 0.2676 | Undetermined | Fair |
| win_6 | 110.70 | 59.08 | slow_component/acf | high | 0.4974 | 0.2666 | Undetermined | Fair |

Interpretation boundary: the measurements consistently contain a narrow periodic
spike train near 3.7-4.1 Hz. The pipeline treats this as a non-QRS periodic
artifact and reports the lower-frequency cardiac repetition rate with explicit
confidence. There is **no independent ground truth** (simultaneous reference
pulse oximeter, clinician annotation, or clean lead) in these CSVs; therefore the
low-frequency rates are best-supported estimates, not proven diagnoses.

Verification:
- `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. pytest tests/ -x -q -p no:cacheprovider`
  → **7 passed in 0.14-0.15 s**.
- Clean synthetic 50-140 bpm remained `qrs_peaks` and accurate:
  50.0, 60.0, 69.93, 90.09, 100.0, 109.89, 120.0, 129.87, 140.19 bpm.
  Some synthetic high-rate quality labels are Fair because of morphology/QC, but
  the rate method is not incorrectly converted to the artifact fallback.
- Edge cases: flat and short inputs produce no HR and `No signal` without crash.
- 50 Hz mains test: HR remains 69.93 bpm, method `qrs_peaks`,
  `mains_interference_hz=50.0`, quality Fair.
- Device-conflict regression: synthetic artifact-contaminated 58 bpm recording
  with `device_hr_bpm=115` still reports ≈58 bpm and source `computed`.
- Flask smoke test on port 5056 after POST `/api/demo/start`:
  `/api/ecg/stream` exposed HR 72.0, method `qrs_peaks`, detail/confidence/ACF/
  artifact fields as null, rhythm Regular, quality Excellent, source fused;
  HTML contained the artifact fallback/confidence rendering strings.
- `git diff --check` passes.
- SHA-256 verification of all six local CSVs matches the sanitized committed
  manifest exactly.

### Adversarial review (2026-11-28)
1. **Ground truth remains absent.** The ~4 Hz spike train is mechanistically
   artifact-like, but the files do not prove the true cardiac rate. The new
   confidence/quality/rhythm fields are therefore essential and must not be
   stripped downstream.
2. **Autocorrelation can lock onto a non-cardiac low-frequency oscillation.**
   The 0.5-3 Hz band can include respiration, baseline motion, or artifact
   ringing. This is mitigated but not eliminated by confidence thresholds and
   quality caps.
3. **Partial overcounting remains possible.** If artifact spikes increase the
   measured rate by less than the harmonic gate (for example an occasional extra
   peak rather than 2x/3x undersampling), the correction may not trigger.
4. **Short 10 s windows limit precision.** At 55-60 bpm there are only ~9-10
   cardiac cycles; rate uncertainty of several bpm remains even when the ACF
   peak is strong.
5. **False-positive artifact gates are still possible on unusual real rhythms.**
   Clean synthetic 50-140 bpm regressions pass, but wide-QRS/ectopy-rich real
   recordings were not available in this measurement set.
6. **Sample-rate truth is not independently established by these CSVs.** They
   all state 500 Hz but contain no separate clock reference validating that
   value; all absolute rates scale with any sample-rate error.
