"""
Application configuration — project paths, .env API settings, persisted UI state.

Data is stored under <project_root>/data/ (recordings, diagnosis exports, settings).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from dotenv import load_dotenv

from .ads1292_hardware import (
    ADS1292_SAMPLE_RATES,
    DEFAULT_SAMPLE_RATE,
    FIRMWARE_PROFILES,
    apply_firmware_profile,
    get_firmware_profile,
)

# Project root: ecg_artificial_intelligence/
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = PROJECT_ROOT / "data"
RECORDINGS_DIR = DATA_ROOT / "recordings"
DIAGNOSIS_DIR = DATA_ROOT / "diagnosis"
CONFIG_DIR = DATA_ROOT / "config"
SETTINGS_FILE = CONFIG_DIR / "app_settings.json"
ENV_FILE = PROJECT_ROOT / ".env"
ASSETS_DIR = PROJECT_ROOT / "assets"
APP_ICON_PNG = ASSETS_DIR / "app_icon.png"
APP_ICON_ICO = ASSETS_DIR / "app_icon.ico"


def get_app_icon_path(prefer_ico: bool = False) -> Optional[Path]:
    """Return bundled app icon path if present."""
    if prefer_ico and APP_ICON_ICO.is_file():
        return APP_ICON_ICO
    if APP_ICON_PNG.is_file():
        return APP_ICON_PNG
    if APP_ICON_ICO.is_file():
        return APP_ICON_ICO
    return None


def ensure_data_dirs() -> None:
    """Create standard data directories if missing."""
    for path in (DATA_ROOT, RECORDINGS_DIR, DIAGNOSIS_DIR, CONFIG_DIR):
        path.mkdir(parents=True, exist_ok=True)


def load_env() -> None:
    """Load .env from project root (idempotent)."""
    if ENV_FILE.is_file():
        load_dotenv(ENV_FILE, override=False)
    else:
        example = PROJECT_ROOT / ".env.example"
        if example.is_file():
            load_dotenv(example, override=False)


def get_api_config() -> Dict[str, str]:
    """OpenAI-compatible API settings from environment."""
    load_env()
    api_key = (
        os.getenv("OPENAI_API_KEY")
        or os.getenv("ECG_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or ""
    ).strip()
    base_url = (
        os.getenv("OPENAI_BASE_URL")
        or os.getenv("ECG_API_BASE_URL")
        or "https://api.gptnb.ai/v1/"
    ).strip()
    model_id = (
        os.getenv("ECG_MODEL_ID")
        or os.getenv("AI_SCIENTIST_MODEL")
        or os.getenv("OPENAI_MODEL")
        or "gemini-pro"
    ).strip()
    return {
        "api_key": api_key,
        "api_url": base_url,
        "model_id": model_id,
    }


_DEFAULT_SETTINGS: Dict[str, Any] = {
    "sample_rate_hz": DEFAULT_SAMPLE_RATE,
    "sample_rate_mode": "configured",  # configured | auto
    "time_window_sec": 10,
    "update_interval_ms": 50,
    "paper_speed_mm_s": 25,
    "gain_mm_per_mv": 10,
    "uv_per_count": 12.2,
    "firmware_profile": "protocentral_500",
    "baud_rate": 57600,
    "data_bits": 8,
    "parity": "None",
    "stop_bits": 1,
    "auto_diagnosis_interval_sec": 30,
    "diagnosis_buffer_size": 5000,
    "llm_timeout_sec": 60,
    "max_history_size": 50,
    "export_format": "JSON",
    "recordings_dir": str(RECORDINGS_DIR),
    "diagnosis_dir": str(DIAGNOSIS_DIR),
    "appearance_mode": "dark",
}


def default_settings() -> Dict[str, Any]:
    ensure_data_dirs()
    return dict(_DEFAULT_SETTINGS)


def load_settings() -> Dict[str, Any]:
    """Merge persisted settings with defaults."""
    settings = default_settings()
    if SETTINGS_FILE.is_file():
        try:
            with open(SETTINGS_FILE, encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                settings.update(stored)
        except (json.JSONDecodeError, OSError):
            pass
    sr = float(settings.get("sample_rate_hz", DEFAULT_SAMPLE_RATE))
    if sr not in ADS1292_SAMPLE_RATES:
        settings["sample_rate_hz"] = _snap_sample_rate(sr)
    return settings


def save_settings(settings: Dict[str, Any]) -> None:
    """Persist settings to data/config/app_settings.json."""
    ensure_data_dirs()
    payload = {k: settings[k] for k in _DEFAULT_SETTINGS if k in settings}
    with open(SETTINGS_FILE, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)


def _snap_sample_rate(rate: float) -> float:
    """Snap to nearest ADS1292 standard rate."""
    return float(min(ADS1292_SAMPLE_RATES, key=lambda r: abs(r - rate)))