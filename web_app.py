#!/usr/bin/env python3
"""
ECG AI Diagnosis — Flask Web Application
Replaces the Tkinter GUI with a lightweight HTML/Canvas frontend.
Run: python web_app.py  →  open http://localhost:5000
"""

import os
import sys
import argparse
import threading
import time
import json
import numpy as np
from collections import deque
from datetime import datetime
from typing import Optional, Dict, Any, List

from flask import Flask, render_template, jsonify, request

# Ensure project root is on path
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from ecg_receiver.core.app_config import ensure_data_dirs, load_env, get_api_config
from ecg_receiver.core.ecg_demo import fill_demo_buffer, generate_medical_demo_chunk
from ecg_receiver.core.ecg_signal import (
    DEFAULT_SAMPLE_RATE,
    compute_ecg_metrics,
    preprocess_for_llm,
    parse_ecg_line,
)
from ecg_receiver.core.llm_diagnosis import LLMDiagnosisClient, MODEL_PRESETS
from ecg_receiver.core.serial_handler import SerialHandler

ensure_data_dirs()
load_env()

import logging
logging.getLogger('werkzeug').setLevel(logging.ERROR)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True

# ── Global state ──────────────────────────────────────────────────────────────

class ECGStateManager:
    """Thread-safe ECG state shared between Flask routes and background threads."""

    def __init__(self):
        self.lock = threading.Lock()
        self.demo_mode = False
        self.demo_phase = 0.0
        self.demo_bpm = 72.0
        self.sample_rate = float(DEFAULT_SAMPLE_RATE)
        self.time_window_sec = 10.0
        self.uv_per_count = 1.0
        self.buffer: deque = deque(maxlen=int(self.sample_rate * (self.time_window_sec + 2)))
        self.diagnosis_client: Optional[LLMDiagnosisClient] = None
        self.last_metrics: Dict[str, Any] = {}
        self.last_diagnosis: Optional[Dict[str, Any]] = None
        self.diagnosis_history: List[Dict[str, Any]] = []
        self.api_status = "disconnected"
        self._demo_thread: Optional[threading.Thread] = None
        self.serial_handler: Optional[SerialHandler] = None
        self.serial_port: Optional[str] = None
        self.serial_connected = False
        self._serial_thread: Optional[threading.Thread] = None
        self._serial_stop = False
        self._serial_data_received = False
        self.serial_error = ""

    def get_recent_mv(self, max_points: int = 0) -> List[float]:
        with self.lock:
            data = list(self.buffer)
        if not data:
            return []
        arr = np.asarray(data, dtype=np.float64)
        uv = arr * self.uv_per_count
        mv = (uv / 1000.0).astype(np.float32)
        if max_points and len(mv) > max_points:
            mv = mv[-max_points:]
        return mv.tolist()

    def compute_metrics(self) -> Dict[str, Any]:
        with self.lock:
            data = list(self.buffer)
        if len(data) < 50:
            return {}
        arr = np.asarray(data, dtype=np.float64)
        device_hr = self.demo_bpm if self.demo_mode else None
        metrics = compute_ecg_metrics(
            arr, self.sample_rate, device_hr_bpm=device_hr,
            uv_per_count=self.uv_per_count,
        )
        self.last_metrics = metrics
        return metrics

    def start_demo(self):
        with self.lock:
            self.demo_mode = True
            self.demo_phase = 0.0
            self.buffer.clear()
            self.uv_per_count = 1.0
        preload = fill_demo_buffer(
            total_seconds=self.time_window_sec + 2,
            bpm=self.demo_bpm,
            sample_rate=self.sample_rate,
        )
        with self.lock:
            for v in preload:
                self.buffer.append(v)
        if self._demo_thread and self._demo_thread.is_alive():
            return
        self._demo_thread = threading.Thread(target=self._demo_loop, daemon=True)
        self._demo_thread.start()

    def _demo_loop(self):
        while True:
            with self.lock:
                if not self.demo_mode:
                    break
            chunk = generate_medical_demo_chunk(
                bpm=self.demo_bpm,
                sample_rate=self.sample_rate,
                seconds=0.1,
                phase=self.demo_phase,
            )
            with self.lock:
                self.demo_phase += 0.1
                for v in chunk:
                    self.buffer.append(v)
            time.sleep(0.1)

    def stop_demo(self):
        with self.lock:
            self.demo_mode = False
            self.buffer.clear()
            self.uv_per_count = float(get_api_config().get("uv_per_count", 12.2) or 12.2)

    def list_serial_ports(self) -> list:
        try:
            handler = SerialHandler()
            return handler.list_ports()
        except Exception:
            return []

    def connect_serial(self, port: str) -> bool:
        self.disconnect_serial()
        handler = SerialHandler()
        ok = handler.connect(port)
        if not ok:
            return False
        self.serial_handler = handler
        self.serial_port = port
        self.serial_connected = True
        self._serial_stop = False
        self._serial_data_received = False
        self.serial_error = ""
        self.uv_per_count = float(get_api_config().get("uv_per_count", 12.2) or 12.2)
        self.buffer.clear()
        self._serial_thread = threading.Thread(target=self._serial_loop, daemon=True)
        self._serial_thread.start()
        # Spawn a watchdog thread: if no data in 5s, auto-disconnect
        threading.Thread(target=self._serial_watchdog, daemon=True).start()
        return True

    def _serial_watchdog(self):
        """If no ECG data arrives within 5 seconds, auto-disconnect."""
        time.sleep(5)
        if self._serial_stop:
            return
        if not self._serial_data_received:
            self.serial_error = f"No ECG data received from {self.serial_port} — is hardware connected?"
            self.disconnect_serial()

    def disconnect_serial(self):
        self._serial_stop = True
        self.serial_connected = False
        if self.serial_handler:
            self.serial_handler.disconnect()
            self.serial_handler = None
        self.serial_port = None
        with self.lock:
            self.buffer.clear()

    def _serial_loop(self):
        if not self.serial_handler:
            return
        def callback(line: str):
            if self._serial_stop:
                return
            parsed = parse_ecg_line(line)
            if parsed.ecg_value is not None:
                self._serial_data_received = True
                with self.lock:
                    self.buffer.append(float(parsed.ecg_value))
        self.serial_handler.start_reading(callback)

    def setup_api(self, api_key: str, api_url: str, model_id: str) -> bool:
        try:
            self.diagnosis_client = LLMDiagnosisClient(
                api_key=api_key,
                api_url=api_url,
                model_id=model_id,
                timeout=60,
                sample_rate=self.sample_rate,
            )
            self.api_status = "connected"
            return True
        except Exception as e:
            self.api_status = "error"
            raise e

    def run_diagnosis(self, patient_info: Optional[Dict] = None) -> Dict[str, Any]:
        if not self.diagnosis_client:
            if self.demo_mode:
                return self._demo_diagnosis(patient_info)
            raise ValueError("API not configured")

        with self.lock:
            data = list(self.buffer)
        if len(data) < 100:
            raise ValueError("Not enough ECG data")

        effective_rate = self.sample_rate
        window_samples = int(effective_rate * 10)
        available = min(max(window_samples, 1000), len(data))
        ecg_data = data[-available:]

        processed = self.diagnosis_client.preprocess_ecg_data(
            ecg_data, sample_rate=effective_rate,
            device_hr_bpm=self.demo_bpm if self.demo_mode else None,
        )
        result = self.diagnosis_client.diagnose_heart_condition(processed, patient_info)
        self.last_diagnosis = result
        self.diagnosis_history.append({
            "timestamp": datetime.now().isoformat(),
            "diagnosis": result,
        })
        return result

    def _demo_diagnosis(self, patient_info: Optional[Dict] = None) -> Dict[str, Any]:
        metrics = self.compute_metrics()
        hr = metrics.get("heart_rate_bpm") or 72
        ptp_mv = (metrics.get("peak_to_peak") or 1200) / 1000.0
        return {
            "severity": "normal",
            "primary_diagnosis": f"Normal Sinus Rhythm — HR {hr:.0f} bpm, Regular R-R intervals",
            "confidence": 0.92,
            "timestamp": datetime.now().isoformat(),
            "model_used": "Demo AI (simulated)",
            "key_findings": [
                f"Heart rate: {hr:.0f} bpm (normal range 60-100)",
                f"Rhythm: Normal sinus rhythm — regular R-R intervals detected",
                f"Signal quality: {metrics.get('signal_quality_label', 'Good')}",
                f"Peak-to-peak amplitude: {ptp_mv:.2f} mV (within normal range)",
                "P-wave morphology: normal amplitude and duration",
                "QRS complex: narrow (<120 ms), no conduction abnormalities",
                "ST segment: no elevation or depression observed",
                "T-wave: normal morphology, no inversion",
            ],
            "recommendations": {
                "immediate_actions": [
                    "No immediate clinical intervention required",
                    "Continue routine cardiac monitoring",
                    "Document baseline ECG for future comparison",
                ],
                "follow_up": [
                    "Routine follow-up in 6-12 months recommended",
                    "Annual cardiac check-up advised for patients over 40",
                ],
                "lifestyle": [
                    "Maintain regular cardiovascular exercise (150 min/week)",
                    "Monitor blood pressure regularly",
                    "Maintain healthy diet low in sodium and saturated fats",
                ],
            },
            "secondary_conditions": [],
            "normal_ranges_comparison": {
                "heart_rate": f"{hr:.0f} bpm (normal: 60-100)",
                "pr_interval": "160 ms (normal: 120-200 ms)",
                "qrs_duration": "90 ms (normal: 80-120 ms)",
                "qt_interval": "380 ms (normal: 350-440 ms)",
            },
            "risk_factors": [],
            "prognosis": "Excellent — normal cardiac function with no significant risk factors identified.",
            "clinical_disclaimer": "AI-assisted analysis, not a substitute for clinical judgment.",
        }


state = ECGStateManager()

# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/config")
def api_config():
    cfg = get_api_config()
    return jsonify({
        "presets": {k: v for k, v in MODEL_PRESETS.items()},
        "api_key": cfg.get("api_key", ""),
        "api_url": cfg.get("api_url", ""),
        "model_id": cfg.get("model_id", ""),
        "sample_rate": state.sample_rate,
        "time_window": state.time_window_sec,
    })

@app.route("/api/setup", methods=["POST"])
def setup_api():
    data = request.json or {}
    try:
        ok = state.setup_api(
            data.get("api_key", ""),
            data.get("api_url", ""),
            data.get("model_id", ""),
        )
        return jsonify({"status": "connected" if ok else "error"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/api/serial/ports")
def serial_ports():
    return jsonify({"ports": state.list_serial_ports()})

@app.route("/api/serial/connect", methods=["POST"])
def serial_connect():
    data = request.json or {}
    port = data.get("port", "")
    if not port:
        return jsonify({"status": "error", "message": "No port specified"}), 400
    ok = state.connect_serial(port)
    return jsonify({"status": "connected" if ok else "error", "port": port})

@app.route("/api/serial/disconnect", methods=["POST"])
def serial_disconnect():
    state.disconnect_serial()
    return jsonify({"status": "disconnected"})

@app.route("/api/demo/start", methods=["POST"])
def demo_start():
    state.start_demo()
    return jsonify({"status": "demo_started"})

@app.route("/api/demo/stop", methods=["POST"])
def demo_stop():
    state.stop_demo()
    return jsonify({"status": "demo_stopped"})

@app.route("/api/ecg/stream")
def ecg_stream():
    """Poll-based ECG data endpoint (called every ~100ms by frontend)."""
    max_points = int(state.sample_rate * state.time_window_sec)
    mv_data = state.get_recent_mv(max_points=max_points)
    metrics = state.compute_metrics() if len(mv_data) > 50 else {}
    return jsonify({
        "data": mv_data,
        "sample_rate": state.sample_rate,
        "time_window": state.time_window_sec,
        "demo_mode": state.demo_mode,
        "serial_connected": state.serial_connected,
        "serial_port": state.serial_port,
        "serial_error": state.serial_error,
        "metrics": {
            "heart_rate_bpm": metrics.get("heart_rate_bpm"),
            "heart_rate_source": metrics.get("heart_rate_source"),
            "rhythm_label": metrics.get("rhythm_label"),
            "signal_quality_label": metrics.get("signal_quality_label"),
            "peak_to_peak_mv": (metrics.get("peak_to_peak") or 0) / 1000.0,
            "sample_rate_hz": metrics.get("sample_rate_hz", state.sample_rate),
            "qrs_count": metrics.get("qrs_count"),
        },
    })

@app.route("/api/diagnosis/run", methods=["POST"])
def run_diagnosis():
    data = request.json or {}
    patient_info = data.get("patient_info")
    try:
        result = state.run_diagnosis(patient_info)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e), "severity": "unknown"}), 500

@app.route("/api/diagnosis/history")
def diagnosis_history():
    return jsonify({"history": state.diagnosis_history[-20:]})

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ECG AI Diagnosis Web App")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5000, help="Port (default: 5000)")
    args = parser.parse_args()

    # Auto-connect from .env if available
    cfg = get_api_config()
    if cfg.get("api_key") and cfg.get("api_url") and cfg.get("model_id"):
        try:
            state.setup_api(cfg["api_key"], cfg["api_url"], cfg["model_id"])
            print(f"[API] Auto-connected: {cfg['api_url']} | model: {cfg['model_id']}")
        except Exception as e:
            print(f"[API] Auto-connect failed: {e}")

    print("\n" + "=" * 50)
    print("  ECG AI Diagnosis Web App")
    print(f"  Open: http://{args.host}:{args.port}")
    print("=" * 50 + "\n")

    app.run(host=args.host, port=args.port, debug=False, threaded=True)
