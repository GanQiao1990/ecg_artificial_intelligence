"""
Multi-model LLM ECG Diagnosis Client
Supports OpenAI-compatible APIs (GPT-4, Gemini, DeepSeek, Qwen, local models)
"""

import json
import threading
from typing import Any, Dict, List, Optional

import numpy as np
import requests


# ── Model presets ────────────────────────────────────────────────────────────
MODEL_PRESETS = {
    "Gemini Pro (default)": {
        "api_url": "https://api.gptnb.ai/v1/chat/completions",
        "model_id": "gemini-pro",
    },
    "Gemini 2.0 Flash": {
        "api_url": "https://api.gptnb.ai/v1/chat/completions",
        "model_id": "gemini-2.0-flash",
    },
    "GPT-4o": {
        "api_url": "https://api.openai.com/v1/chat/completions",
        "model_id": "gpt-4o",
    },
    "GPT-4o-mini": {
        "api_url": "https://api.openai.com/v1/chat/completions",
        "model_id": "gpt-4o-mini",
    },
    "DeepSeek Chat": {
        "api_url": "https://api.deepseek.com/v1/chat/completions",
        "model_id": "deepseek-chat",
    },
    "Qwen Max": {
        "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
        "model_id": "qwen-max",
    },
    "Custom / Local": {
        "api_url": "",
        "model_id": "",
    },
}

ECG_SYSTEM_PROMPT = """You are an expert clinical cardiologist AI assistant specializing in ECG interpretation.
Analyze the provided ECG data and return a structured JSON diagnosis.

IMPORTANT: Return ONLY valid JSON with this exact structure:
{
    "severity": "normal|abnormal|critical",
    "confidence": 0.85,
    "primary_diagnosis": "Description of the primary finding",
    "secondary_conditions": ["condition1", "condition2"],
    "key_findings": ["finding1", "finding2", "finding3"],
    "recommendations": {
        "immediate_actions": ["action1", "action2"],
        "follow_up": ["follow1", "follow2"],
        "lifestyle": ["rec1", "rec2"]
    },
    "normal_ranges_comparison": {
        "heart_rate": "72 BPM (normal: 60-100)",
        "rhythm": "Regular sinus rhythm"
    },
    "risk_factors": ["factor1"],
    "prognosis": "Overall assessment statement"
}
"""


class LLMDiagnosisClient:
    """OpenAI-compatible LLM client for ECG diagnosis."""

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://api.gptnb.ai/v1/chat/completions",
        model_id: str = "gemini-pro",
        timeout: int = 60,
    ):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.model_id = model_id
        self.timeout = timeout

        # Ensure URL ends with /chat/completions
        if not self.api_url.endswith("/chat/completions"):
            self.api_url = self.api_url.rstrip("/") + "/v1/chat/completions"

    def preprocess_ecg_data(self, ecg_array: list) -> Dict[str, Any]:
        """Compute summary statistics for the LLM prompt."""
        data = np.asarray(ecg_array, dtype=np.float64)
        if data.size == 0:
            return {"raw_sample_count": 0}

        stats: Dict[str, Any] = {
            "raw_sample_count": int(data.size),
            "duration_sec": round(data.size / 250.0, 2),
            "mean_uV": round(float(np.mean(data)), 2),
            "std_uV": round(float(np.std(data)), 2),
            "min_uV": round(float(np.min(data)), 2),
            "max_uV": round(float(np.max(data)), 2),
            "peak_to_peak_uV": round(float(np.ptp(data)), 2),
            "rms_uV": round(float(np.sqrt(np.mean(data ** 2))), 2),
        }

        # Simple R-peak detection for HR estimate
        threshold = float(np.mean(data) + 0.6 * np.std(data))
        min_dist = max(1, int(250 * 0.3))
        peaks: List[int] = []
        for i in range(1, data.size - 1):
            if data[i] >= threshold and data[i] >= data[i - 1] and data[i] > data[i + 1]:
                if not peaks or i - peaks[-1] >= min_dist:
                    peaks.append(i)

        if len(peaks) > 1:
            rr = np.diff(peaks) / 250.0
            stats["estimated_hr_bpm"] = round(60.0 / float(np.mean(rr)), 1)
            stats["rr_variability_cv"] = round(float(np.std(rr) / np.mean(rr)), 4)
            stats["num_beats_detected"] = len(peaks)
        else:
            stats["estimated_hr_bpm"] = None
            stats["num_beats_detected"] = len(peaks)

        # Include a compact waveform snippet (last 5 seconds, decimated)
        snippet_len = min(1250, data.size)
        snippet = data[-snippet_len:]
        decimation = max(1, snippet_len // 250)
        decimated = snippet[::decimation].tolist()
        stats["waveform_snippet"] = [round(v, 1) for v in decimated]

        return stats

    def diagnose_heart_condition(
        self,
        processed_data: Dict[str, Any],
        patient_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send ECG data to the LLM and parse the diagnosis response."""

        user_content_parts = ["ECG Data Summary:", json.dumps(processed_data, indent=2)]
        if patient_info:
            user_content_parts.append("\nPatient Information:")
            user_content_parts.append(json.dumps(patient_info, indent=2))
        user_content_parts.append(
            "\nPlease analyze this ECG data and provide a structured diagnosis in JSON format."
        )

        messages = [
            {"role": "system", "content": ECG_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(user_content_parts)},
        ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2048,
        }

        resp = requests.post(
            self.api_url,
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        resp.raise_for_status()

        result = resp.json()
        content = result["choices"][0]["message"]["content"]

        return self._parse_diagnosis(content)

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_diagnosis(raw_text: str) -> Dict[str, Any]:
        """Try to extract JSON from model output, falling back gracefully."""
        # Strip markdown code fences if present
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            diagnosis = json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object inside the text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    diagnosis = json.loads(text[start:end])
                except json.JSONDecodeError:
                    diagnosis = {
                        "severity": "unknown",
                        "confidence": 0.0,
                        "primary_diagnosis": "Unable to parse model response",
                        "key_findings": [raw_text[:500]],
                        "parse_error": "Model did not return valid JSON",
                    }
            else:
                diagnosis = {
                    "severity": "unknown",
                    "confidence": 0.0,
                    "primary_diagnosis": raw_text[:200],
                    "key_findings": [],
                    "parse_error": "No JSON found in response",
                }

        # Ensure mandatory keys exist
        diagnosis.setdefault("severity", "unknown")
        diagnosis.setdefault("confidence", 0.0)
        diagnosis.setdefault("primary_diagnosis", "No diagnosis provided")
        diagnosis.setdefault("key_findings", [])
        diagnosis.setdefault("recommendations", {})
        diagnosis.setdefault("secondary_conditions", [])
        diagnosis.setdefault("normal_ranges_comparison", {})
        diagnosis.setdefault("risk_factors", [])
        diagnosis.setdefault("prognosis", "")
        diagnosis["model_used"] = "LLM analysis"
        diagnosis["timestamp"] = __import__("datetime").datetime.now().isoformat()

        return diagnosis
