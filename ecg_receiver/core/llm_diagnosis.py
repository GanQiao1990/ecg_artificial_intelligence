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


class DiagnosisAPIError(Exception):
    """Diagnosis API failure carrying a user-facing message and technical detail.

    `str(err)` returns the Chinese user message so every caller
    (Flask routes, Tkinter `error_callback(str(e))`) gets the clean
    form for free; `code` is the machine-readable category and
    `detail` keeps the raw technical text for logs/debugging.
    """

    def __init__(self, code: str, user_message: str, detail: str = ""):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.detail = detail

    def to_dict(self) -> Dict[str, Any]:
        return {
            "error": self.user_message,
            "error_code": self.code,
            "detail": self.detail,
            "severity": "unknown",
        }


_HTTP_ERROR_MAP = {
    400: ("bad_request", "AI 服务拒绝了请求参数，请检查模型 ID 是否正确"),
    401: ("auth_failed", "API 密钥无效或已过期，请在 Settings 中检查密钥"),
    403: ("auth_failed", "API 密钥没有访问权限，请检查账户状态或模型权限"),
    404: ("not_found", "API 地址或模型不存在，请检查 API URL 与 Model ID"),
    408: ("timeout", "AI 服务响应超时，请稍后重试"),
    409: ("conflict", "AI 服务返回冲突错误，请稍后重试"),
    429: ("rate_limited", "请求过于频繁或额度已用尽，请稍后重试"),
}

# Hard ceiling for the token-budget retry on truncated (finish_reason=length)
# responses — reasoning models can spend the whole budget on hidden
# chain-of-thought before emitting the JSON report.
_MAX_TOKENS_CAP = 32768


def _api_error_text(resp: requests.Response) -> str:
    """Best-effort extraction of the provider's own error message."""
    try:
        body = resp.json()
    except ValueError:
        return (resp.text or "")[:300].strip()
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            msg = err.get("message") or err.get("type") or ""
            code = err.get("code") or ""
            return f"{code}: {msg}".strip(": ")[:300] if msg or code else str(err)[:300]
        if isinstance(err, str):
            return err[:300]
        msg = body.get("message")
        if isinstance(msg, str):
            return msg[:300]
    return (resp.text or "")[:300].strip()


def _classify_http_error(resp: requests.Response) -> DiagnosisAPIError:
    status = resp.status_code
    detail = f"HTTP {status} — {_api_error_text(resp)}"
    if status in _HTTP_ERROR_MAP:
        code, msg = _HTTP_ERROR_MAP[status]
        return DiagnosisAPIError(code, msg, detail)
    if 500 <= status < 600:
        return DiagnosisAPIError(
            "server_error", f"AI 服务内部错误（HTTP {status}），请稍后重试", detail
        )
    return DiagnosisAPIError(
        "http_error", f"AI 服务返回异常状态（HTTP {status}）", detail
    )


class LLMDiagnosisClient:
    """OpenAI-compatible LLM client for ECG diagnosis."""

    def __init__(
        self,
        api_key: str,
        api_url: str = "https://api.gptnb.ai/v1/chat/completions",
        model_id: str = "gemini-pro",
        timeout: int = 60,
        sample_rate: float = DEFAULT_SAMPLE_RATE,
        max_tokens: int = 8192,
    ):
        self.api_key = api_key
        self.api_url = api_url.rstrip("/")
        self.model_id = model_id
        self.timeout = timeout
        self.sample_rate = float(sample_rate)
        self.max_tokens = int(max_tokens) if max_tokens else 8192

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
        zone_label = processed_data.get("heart_rate_zone_label") or "未知"
        hr_method = processed_data.get("heart_rate_method", "qrs_peaks")
        mains = processed_data.get("mains_interference_hz")
        hf = processed_data.get("hf_noise_ratio")
        user_parts = [
            "【心电分析摘要】系统已完成采样率校准、0.5-40Hz 滤波与 R 峰检测，请直接采用下列心率字段。",
            (
                f"心率：{hr} BPM（来源：{hr_src}，方法：{hr_method}，分区：{zone_label}"
                f"{'，心率异常' if processed_data.get('heart_rate_abnormal') else ''}）"
                f"| 采样率：{fs} Hz | QRS：{processed_data.get('qrs_count')} | 节律：{processed_data.get('rhythm_regularity')} | 质量：{processed_data.get('signal_quality')}"
                + (f" | 市电干扰：{mains} Hz" if mains else "")
                + (f" | 高频噪声占比：{hf}" if hf is not None else "")
            ),
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

        max_tokens = self.max_tokens
        result = self._post_chat(self._build_payload(messages, max_tokens))
        content, finish_reason = self._extract_content(result)

        # Reasoning models can burn the whole token budget on hidden
        # chain-of-thought and return finish_reason="length" with empty
        # content — retry once with a doubled budget before giving up.
        if (
            not (isinstance(content, str) and content.strip())
            and finish_reason == "length"
            and max_tokens < _MAX_TOKENS_CAP
        ):
            max_tokens = min(max_tokens * 2, _MAX_TOKENS_CAP)
            result = self._post_chat(self._build_payload(messages, max_tokens))
            content, finish_reason = self._extract_content(result)

        if not isinstance(content, str) or not content.strip():
            detail = json.dumps(result, ensure_ascii=False)[:300]
            if finish_reason == "length":
                raise DiagnosisAPIError(
                    "truncated_response",
                    "AI 模型输出被 token 上限截断（推理过程过长），请重试或更换模型",
                    detail,
                )
            raise DiagnosisAPIError(
                "empty_response",
                "AI 服务返回了空内容，请重试或更换模型",
                detail,
            )

        diagnosis = self._parse_diagnosis(content)
        diagnosis["input_metrics"] = {
            "heart_rate_bpm": processed_data.get("heart_rate_bpm"),
            "heart_rate_source": processed_data.get("heart_rate_source"),
            "sample_rate_hz": processed_data.get("sample_rate_hz"),
            "signal_quality": processed_data.get("signal_quality"),
        }
        return diagnosis

    def _build_payload(self, messages: List[Dict[str, str]], max_tokens: int) -> Dict[str, Any]:
        return {
            "model": self.model_id,
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": max_tokens,
        }

    def _post_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST one chat-completion request; classify transport/HTTP errors."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout,
            )
        except requests.Timeout:
            raise DiagnosisAPIError(
                "timeout",
                f"AI 服务响应超时（>{self.timeout} 秒），请检查网络后重试",
                f"POST {self.api_url} timed out",
            ) from None
        except requests.ConnectionError as exc:
            raise DiagnosisAPIError(
                "connect_failed",
                "无法连接 AI 服务，请检查网络连接与 API 地址",
                str(exc),
            ) from None
        except requests.RequestException as exc:
            raise DiagnosisAPIError(
                "network_error", "网络请求失败，请检查网络后重试", str(exc)
            ) from None

        if resp.status_code != 200:
            raise _classify_http_error(resp)

        try:
            return resp.json()
        except ValueError:
            raise DiagnosisAPIError(
                "bad_response",
                "AI 服务返回了无法解析的响应（非 JSON）",
                (resp.text or "")[:300],
            ) from None

    @staticmethod
    def _extract_content(result: Dict[str, Any]):
        """Return (content, finish_reason); raise bad_response on malformed body."""
        try:
            choice = result["choices"][0]
            content = choice["message"].get("content")
            finish_reason = choice.get("finish_reason")
            return content, finish_reason
        except (KeyError, IndexError, TypeError, AttributeError):
            raise DiagnosisAPIError(
                "bad_response",
                "AI 服务响应缺少 choices 字段，模型或接口可能不兼容",
                json.dumps(result, ensure_ascii=False)[:300],
            ) from None

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