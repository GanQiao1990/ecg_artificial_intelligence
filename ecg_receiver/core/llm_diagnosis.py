"""
Multi-model LLM ECG Diagnosis Client
Supports OpenAI-compatible APIs (GPT-4, Gemini, DeepSeek, Qwen, local models)
"""

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

import numpy as np
import requests

from .ecg_signal import DEFAULT_SAMPLE_RATE, preprocess_for_llm

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

ECG_SYSTEM_PROMPT = """你是一名三甲医院心脏科主任医师，为临床级单导联心电监护系统提供 AI 辅助判读。

## 数据可信度（必须遵守）
- `heart_rate_bpm` 与 `heart_rate_source` 已由系统完成采样率校准（ADS1292R：125/250/500/1000 Hz）及 R 峰检测。
- `heart_rate_source` 为 device 时表示固件直接输出，临床优先级最高；禁止对心率自行 ×2 或 ÷2。
- `signal_quality` 为 Poor/No signal 时，降低 confidence，并在 primary_diagnosis 中说明数据不足。
- `sample_rate_hz`、`qrs_count`、`rr_variability_cv` 用于评估节律，勿忽略。

## 判读参考
- 成人窦性心率：60–100 次/分；rr_variability_cv < 0.08 倾向规则节律。
- 结合 peak_to_peak、波形片段形态讨论 ST/T 改变时须注明单导联局限性。

## 输出要求
- 仅返回合法 JSON（无 markdown 代码块、无前后说明文字）。
- 用语专业、简洁、中文为主；缩写保留（如 PVC、AF、ST）。
- severity：normal=未见明显异常；abnormal=需关注；critical=需紧急处理。
- confidence：0.0–1.0，与信号质量及证据强度一致。

JSON 结构：
{
    "severity": "normal|abnormal|critical",
    "confidence": 0.85,
    "primary_diagnosis": "一句话核心结论（中文）",
    "secondary_conditions": ["鉴别诊断或次要发现"],
    "key_findings": ["客观依据，引用摘要中的数值"],
    "recommendations": {
        "immediate_actions": ["立即措施，无则空数组"],
        "follow_up": ["门诊/复查/Holter 等"],
        "lifestyle": ["生活方式建议"]
    },
    "normal_ranges_comparison": {
        "heart_rate": "与 60–100 次/分对照",
        "rhythm": "节律描述"
    },
    "risk_factors": ["危险因素"],
    "prognosis": "临床意义与预后（简短）",
    "clinical_disclaimer": "本报告为 AI 辅助决策支持，不能替代医师面诊、12 导联心电图及必要实验室检查。"
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
        sample_rate: float = DEFAULT_SAMPLE_RATE,
    ):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.model_id = model_id
        self.timeout = timeout
        self.sample_rate = float(sample_rate)

        if not self.api_url.endswith("/chat/completions"):
            base = self.api_url.rstrip("/")
            if base.endswith("/v1"):
                self.api_url = base + "/chat/completions"
            elif "/v1/" in base or base.endswith("/v1/"):
                self.api_url = base.rstrip("/") + "/chat/completions"
            else:
                self.api_url = base + "/v1/chat/completions"

    def preprocess_ecg_data(
        self,
        ecg_array: list,
        sample_rate: Optional[float] = None,
        device_hr_bpm: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Compute calibrated summary statistics for the LLM prompt."""
        data = np.asarray(ecg_array, dtype=np.float64)
        rate = float(sample_rate) if sample_rate else self.sample_rate
        return preprocess_for_llm(data, rate, device_hr_bpm)

    def diagnose_heart_condition(
        self,
        processed_data: Dict[str, Any],
        patient_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Send ECG data to the LLM and parse the diagnosis response."""

        hr = processed_data.get("heart_rate_bpm")
        hr_src = processed_data.get("heart_rate_source", "unknown")
        fs = processed_data.get("sample_rate_hz")
        user_parts = [
            "【心电分析摘要】系统已完成采样率校准与 R 峰检测，请直接采用下列心率字段。",
            f"心率：{hr} BPM（来源：{hr_src}）| 采样率：{fs} Hz | QRS：{processed_data.get('qrs_count')} | 质量：{processed_data.get('signal_quality')}",
            json.dumps(processed_data, indent=2, ensure_ascii=False),
        ]
        if patient_info:
            user_parts.append("\n【患者信息】")
            user_parts.append(json.dumps(patient_info, indent=2, ensure_ascii=False))
        user_parts.append(
            "\n请输出结构化 JSON 判读报告。勿重新计算心率；信号差时明确说明并降低 confidence。"
        )

        messages = [
            {"role": "system", "content": ECG_SYSTEM_PROMPT},
            {"role": "user", "content": "\n".join(user_parts)},
        ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.2,
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

        diagnosis = self._parse_diagnosis(content)
        diagnosis["input_metrics"] = {
            "heart_rate_bpm": processed_data.get("heart_rate_bpm"),
            "heart_rate_source": processed_data.get("heart_rate_source"),
            "sample_rate_hz": processed_data.get("sample_rate_hz"),
            "signal_quality": processed_data.get("signal_quality"),
        }
        return diagnosis

    @staticmethod
    def _parse_diagnosis(raw_text: str) -> Dict[str, Any]:
        """Try to extract JSON from model output, falling back gracefully."""
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            text = "\n".join(lines).strip()

        try:
            diagnosis = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    diagnosis = json.loads(text[start:end])
                except json.JSONDecodeError:
                    diagnosis = {
                        "severity": "unknown",
                        "confidence": 0.0,
                        "primary_diagnosis": "无法解析模型响应",
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

        diagnosis.setdefault("severity", "unknown")
        diagnosis.setdefault("confidence", 0.0)
        diagnosis.setdefault("primary_diagnosis", "未提供诊断")
        diagnosis.setdefault("key_findings", [])
        diagnosis.setdefault("recommendations", {})
        diagnosis.setdefault("secondary_conditions", [])
        diagnosis.setdefault("normal_ranges_comparison", {})
        diagnosis.setdefault("risk_factors", [])
        diagnosis.setdefault("prognosis", "")
        diagnosis.setdefault("clinical_disclaimer", "本分析为 AI 辅助参考，不能替代临床判断。")
        diagnosis["model_used"] = "LLM analysis"
        diagnosis["timestamp"] = datetime.now().isoformat()

        return diagnosis