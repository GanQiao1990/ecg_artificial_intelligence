"""
Modern ECG AI Diagnosis GUI - Main Window
Redesigned with CustomTkinter and modern UI principles
"""

import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
import threading
import time
import json
import csv
from datetime import datetime
from typing import Optional, Dict, Any, List
import sys
import os
import numpy as np

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from .components.modern_widgets import *
from .components.optimized_plotter import OptimizedECGPlotter
from .styles.colors import *
from ..core.serial_handler import SerialHandler
from ..core.data_recorder import DataRecorder
from ..core.circular_buffer import CircularECGBuffer
from ..core.performance_monitor import PerformanceMonitor
from ..core.llm_diagnosis import LLMDiagnosisClient, MODEL_PRESETS
from ..core.ecg_signal import (
    ADS1292_SAMPLE_RATES,
    DEFAULT_SAMPLE_RATE,
    SampleRateTracker,
    parse_ecg_line,
    snap_sample_rate,
)
from ..core.ecg_demo import fill_demo_buffer, generate_medical_demo_chunk
from ..core.ads1292_hardware import FIRMWARE_PROFILES, apply_firmware_profile, get_firmware_profile
from ..core.app_config import (
    ensure_data_dirs,
    get_api_config,
    get_app_icon_path,
    load_settings,
    save_settings,
    RECORDINGS_DIR,
    DIAGNOSIS_DIR,
)

class DiagnosisWorker:
    """Worker for ECG diagnosis to prevent UI blocking"""
    
    def __init__(
        self,
        diagnosis_client,
        ecg_data,
        patient_info=None,
        callback=None,
        error_callback=None,
        sample_rate=None,
        device_hr_bpm=None,
    ):
        self.diagnosis_client = diagnosis_client
        self.ecg_data = ecg_data
        self.patient_info = patient_info
        self.callback = callback
        self.error_callback = error_callback
        self.sample_rate = sample_rate
        self.device_hr_bpm = device_hr_bpm
    
    def start(self):
        """Start diagnosis in background thread"""
        def run_diagnosis():
            try:
                processed_data = self.diagnosis_client.preprocess_ecg_data(
                    self.ecg_data,
                    sample_rate=getattr(self, "sample_rate", None),
                    device_hr_bpm=getattr(self, "device_hr_bpm", None),
                )
                diagnosis = self.diagnosis_client.diagnose_heart_condition(processed_data, self.patient_info)
                if self.callback:
                    self.callback(diagnosis)
            except Exception as e:
                if self.error_callback:
                    self.error_callback(str(e))
        
        self.thread = threading.Thread(target=run_diagnosis, daemon=True)
        self.thread.start()


class SettingsDialog(ctk.CTkToplevel):
    """Full settings dialog with serial, display, diagnosis, and data tabs."""

    def __init__(self, parent_app, **kwargs):
        super().__init__(**kwargs)
        self.app = parent_app
        self.title("Settings")
        self.geometry("560x520")
        self.resizable(False, False)
        self.configure(fg_color=BG_DARK)
        self.transient(parent_app.root)
        self.grab_set()

        self._build_ui()
        self._load_current_settings()

    def _build_ui(self):
        tabs = ctk.CTkTabview(self, fg_color=BG_CARD)
        tabs.pack(fill="both", expand=True, padx=14, pady=(14, 6))

        # ── Serial tab ───────────────────────────────────────────────
        tabs.add("Serial")
        serial = tabs.tab("Serial")

        ctk.CTkLabel(serial, text="固件配置 (ADS1292R 盾板):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        profile_labels = [p.label for p in FIRMWARE_PROFILES.values()]
        self.firmware_profile_combo = ctk.CTkComboBox(
            serial,
            values=profile_labels,
            fg_color=BG_LIGHT,
            text_color=TEXT_WHITE,
        )
        self.firmware_profile_combo.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(
            serial,
            text="切换固件后自动设置波特率、采样率、µV/计数",
            font=ctk.CTkFont(size=10),
            text_color=TEXT_GRAY,
        ).pack(anchor="w", padx=10, pady=(0, 4))

        ctk.CTkLabel(serial, text="Baud Rate:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.baud_combo = ctk.CTkComboBox(serial, values=["9600", "19200", "38400", "57600", "115200", "230400"], fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.baud_combo.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(serial, text="Data Bits:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.databits_combo = ctk.CTkComboBox(serial, values=["7", "8"], fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.databits_combo.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(serial, text="Parity:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.parity_combo = ctk.CTkComboBox(serial, values=["None", "Even", "Odd"], fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.parity_combo.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(serial, text="Stop Bits:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.stopbits_combo = ctk.CTkComboBox(serial, values=["1", "1.5", "2"], fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.stopbits_combo.pack(fill="x", padx=10, pady=4)

        # ── Display tab ──────────────────────────────────────────────
        tabs.add("Display")
        display = tabs.tab("Display")

        ctk.CTkLabel(display, text="ECG Time Window (seconds):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.time_window_entry = ctk.CTkEntry(display, fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.time_window_entry.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(
            display,
            text="Sample Rate (Hz) — ADS1292R 固件实际值:",
            text_color=TEXT_WHITE,
        ).pack(anchor="w", padx=10, pady=(10, 0))
        self.sample_rate_combo = ctk.CTkComboBox(
            display,
            values=[str(r) for r in ADS1292_SAMPLE_RATES],
            fg_color=BG_LIGHT,
            text_color=TEXT_WHITE,
        )
        self.sample_rate_combo.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(display, text="采样率模式:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.sample_rate_mode_combo = ctk.CTkComboBox(
            display,
            values=["configured", "auto"],
            fg_color=BG_LIGHT,
            text_color=TEXT_WHITE,
        )
        self.sample_rate_mode_combo.pack(fill="x", padx=10, pady=4)
        ctk.CTkLabel(
            display,
            text="心率异常时请选 configured 并核对 125/250/500/1000",
            font=ctk.CTkFont(size=10),
            text_color=TEXT_GRAY,
        ).pack(anchor="w", padx=10, pady=(0, 4))

        ctk.CTkLabel(display, text="Plot Update Interval (ms):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.update_interval_entry = ctk.CTkEntry(display, fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.update_interval_entry.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(display, text="Paper Speed (mm/s):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.paper_speed_combo = ctk.CTkComboBox(display, values=["12.5", "25", "50"], fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.paper_speed_combo.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(display, text="Gain (mm/mV):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.gain_combo = ctk.CTkComboBox(display, values=["5", "10", "20"], fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.gain_combo.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(display, text="µV per count (amplitude scale):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.uv_scale_entry = ctk.CTkEntry(display, fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.uv_scale_entry.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(display, text="Appearance Mode:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.appearance_combo = ctk.CTkComboBox(display, values=["Dark", "Light", "System"], fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.appearance_combo.pack(fill="x", padx=10, pady=4)

        # ── Diagnosis tab ────────────────────────────────────────────
        tabs.add("Diagnosis")
        diag = tabs.tab("Diagnosis")

        ctk.CTkLabel(diag, text="Auto-Diagnosis Interval (seconds):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.auto_interval_entry = ctk.CTkEntry(diag, fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.auto_interval_entry.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(diag, text="Diagnosis Buffer Size (samples):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.diag_buffer_entry = ctk.CTkEntry(diag, fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.diag_buffer_entry.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(diag, text="LLM Request Timeout (seconds):", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.timeout_entry = ctk.CTkEntry(diag, fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.timeout_entry.pack(fill="x", padx=10, pady=4)

        ctk.CTkLabel(diag, text="Max Diagnosis History:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.max_history_entry = ctk.CTkEntry(diag, fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.max_history_entry.pack(fill="x", padx=10, pady=4)

        # ── Data tab ─────────────────────────────────────────────────
        tabs.add("Data")
        data = tabs.tab("Data")

        ctk.CTkLabel(data, text="Recording Directory:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        rec_row = ctk.CTkFrame(data, fg_color="transparent")
        rec_row.pack(fill="x", padx=10, pady=4)
        self.rec_dir_entry = ctk.CTkEntry(rec_row, fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.rec_dir_entry.pack(side="left", fill="x", expand=True)
        ctk.CTkButton(rec_row, text="Browse", width=70, command=self._browse_rec_dir, fg_color=SECONDARY_BLUE).pack(side="right", padx=(6, 0))

        ctk.CTkLabel(data, text="Export Default Format:", text_color=TEXT_WHITE).pack(anchor="w", padx=10, pady=(10, 0))
        self.export_fmt_combo = ctk.CTkComboBox(data, values=["JSON", "CSV", "TXT"], fg_color=BG_LIGHT, text_color=TEXT_WHITE)
        self.export_fmt_combo.pack(fill="x", padx=10, pady=4)

        # ── Buttons bar ──────────────────────────────────────────────
        btn_frame = ctk.CTkFrame(self, fg_color="transparent")
        btn_frame.pack(fill="x", padx=14, pady=(6, 14))
        ctk.CTkButton(btn_frame, text="Save", fg_color=SUCCESS_GREEN, hover_color="#059669", command=self._save).pack(side="right", padx=(6, 0))
        ctk.CTkButton(btn_frame, text="Cancel", fg_color=BG_LIGHT, hover_color=BG_HOVER, command=self.destroy).pack(side="right")

    # ── populate from current app state ──────────────────────────────
    def _profile_label_for_name(self, name: str) -> str:
        return get_firmware_profile(name).label

    def _profile_name_for_label(self, label: str) -> Optional[str]:
        for key, profile in FIRMWARE_PROFILES.items():
            if profile.label == label:
                return key
        return None

    def _load_current_settings(self):
        profile_name = getattr(self.app, "_firmware_profile", "protocentral_500")
        self.firmware_profile_combo.set(self._profile_label_for_name(profile_name))
        self.baud_combo.set(str(getattr(self.app, '_baud_rate', 57600)))
        self.databits_combo.set(str(getattr(self.app, '_data_bits', 8)))
        self.parity_combo.set(getattr(self.app, '_parity', 'None'))
        self.stopbits_combo.set(str(getattr(self.app, '_stop_bits', 1)))

        self.time_window_entry.insert(0, str(getattr(self.app.ecg_plot, 'time_window_sec', 10)))
        sr = int(getattr(self.app.ecg_plot, 'sample_rate', DEFAULT_SAMPLE_RATE))
        self.sample_rate_combo.set(str(snap_sample_rate(sr) or sr))
        self.sample_rate_mode_combo.set(getattr(self.app, '_sample_rate_mode', 'configured'))
        self.update_interval_entry.insert(0, str(getattr(self.app.ecg_plot, 'update_interval', 50)))
        self.paper_speed_combo.set(str(getattr(self.app.ecg_plot, 'paper_speed_mm_s', 25)))
        self.gain_combo.set(str(getattr(self.app.ecg_plot, 'gain_mm_per_mv', 10)))
        self.uv_scale_entry.insert(0, str(getattr(self.app.ecg_plot, 'uv_per_count', 1.0)))
        self.appearance_combo.set(ctk.get_appearance_mode())

        self.auto_interval_entry.insert(0, str(self.app.auto_diagnosis_interval))
        self.diag_buffer_entry.insert(0, str(self.app.diagnosis_buffer_size))
        self.timeout_entry.insert(0, str(getattr(self.app, '_llm_timeout', 60)))
        self.max_history_entry.insert(0, str(self.app.max_history_size))

        self.rec_dir_entry.insert(0, self.app.data_recorder.base_dir)
        self.export_fmt_combo.set(getattr(self.app, '_export_format', 'JSON'))

    def _browse_rec_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.rec_dir_entry.delete(0, "end")
            self.rec_dir_entry.insert(0, d)

    def _apply_firmware_profile_to_app(self, profile_name: str) -> None:
        fragment = apply_firmware_profile(profile_name)
        self.app._firmware_profile = fragment["firmware_profile"]
        self.app._baud_rate = int(fragment["baud_rate"])
        self.app.serial_handler.baudrate = self.app._baud_rate
        self.app.ecg_plot.sample_rate = float(fragment["sample_rate_hz"])
        self.app.sample_rate_tracker.set_configured_rate(float(fragment["sample_rate_hz"]))
        self.app.ecg_plot.set_uv_per_count(float(fragment["uv_per_count"]))
        self.baud_combo.set(str(self.app._baud_rate))
        self.sample_rate_combo.set(str(int(fragment["sample_rate_hz"])))
        self.uv_scale_entry.delete(0, "end")
        self.uv_scale_entry.insert(0, str(fragment["uv_per_count"]))

    def _save(self):
        try:
            label = self.firmware_profile_combo.get()
            profile_name = self._profile_name_for_label(label)
            if profile_name:
                self._apply_firmware_profile_to_app(profile_name)

            self.app._baud_rate = int(self.baud_combo.get())
            self.app.serial_handler.baudrate = self.app._baud_rate
            self.app._data_bits = int(self.databits_combo.get())
            self.app._parity = self.parity_combo.get()
            self.app._stop_bits = float(self.stopbits_combo.get())

            tw = float(self.time_window_entry.get())
            if 1 <= tw <= 60:
                self.app.ecg_plot.time_window_sec = tw
            sr = int(self.sample_rate_combo.get())
            if sr in ADS1292_SAMPLE_RATES:
                self.app.ecg_plot.sample_rate = float(sr)
                self.app.sample_rate_tracker.set_configured_rate(float(sr))
            mode = self.sample_rate_mode_combo.get().strip().lower()
            if mode in ("configured", "auto"):
                self.app._sample_rate_mode = mode
            ui = int(self.update_interval_entry.get())
            if 10 <= ui <= 1000:
                self.app.ecg_plot.update_interval = ui

            self.app.ecg_plot.set_paper_speed(float(self.paper_speed_combo.get()))
            self.app.ecg_plot.set_gain(float(self.gain_combo.get()))
            uv_scale = float(self.uv_scale_entry.get())
            if uv_scale > 0:
                self.app.ecg_plot.set_uv_per_count(uv_scale)

            mode = self.appearance_combo.get()
            ctk.set_appearance_mode(mode.lower())

            ai = int(self.auto_interval_entry.get())
            if 5 <= ai <= 600:
                self.app.auto_diagnosis_interval = ai
            db = int(self.diag_buffer_entry.get())
            if 500 <= db <= 50000:
                self.app.diagnosis_buffer_size = db
            to = int(self.timeout_entry.get())
            if 5 <= to <= 300:
                self.app._llm_timeout = to
            mh = int(self.max_history_entry.get())
            if 5 <= mh <= 500:
                self.app.max_history_size = mh

            rec_dir = self.rec_dir_entry.get().strip()
            if rec_dir and os.path.isdir(rec_dir):
                self.app.data_recorder.base_dir = rec_dir

            self.app._export_format = self.export_fmt_combo.get()
            self.app._persist_settings()

            self.destroy()
        except Exception as exc:
            messagebox.showerror("Settings Error", str(exc))

class ModernECGMainWindow:
    """Modern ECG AI Diagnosis Main Window"""
    
    def __init__(self):
        """Initialize the modern ECG GUI"""
        ensure_data_dirs()
        self._app_settings = load_settings()

        # Configure CustomTkinter
        ctk.set_appearance_mode(self._app_settings.get("appearance_mode", "dark"))
        ctk.set_default_color_theme("blue")
        
        # Initialize core components
        self.serial_handler = SerialHandler()
        self.data_recorder = DataRecorder(base_dir=self._app_settings.get("recordings_dir", str(RECORDINGS_DIR)))
        self.diagnosis_client: Optional[LLMDiagnosisClient] = None
        self.diagnosis_worker: Optional[DiagnosisWorker] = None
        
        # Data management with performance optimizations
        configured_sr = float(self._app_settings.get("sample_rate_hz", DEFAULT_SAMPLE_RATE))
        self.ecg_buffer = CircularECGBuffer(max_size=int(self._app_settings.get("diagnosis_buffer_size", 5000)))
        self.raw_ecg_values = []
        self.diagnosis_buffer_size = int(self._app_settings.get("diagnosis_buffer_size", 5000))
        self.packets_received = 0
        self.last_diagnosis = None
        self.diagnosis_history = []
        self.max_history_size = int(self._app_settings.get("max_history_size", 50))
        
        # Auto-diagnosis settings
        self.auto_diagnosis_enabled = False
        self.auto_diagnosis_interval = int(self._app_settings.get("auto_diagnosis_interval_sec", 30))
        self.last_auto_diagnosis = 0
        
        # Settings state
        self._firmware_profile = self._app_settings.get("firmware_profile", "protocentral_500")
        self._profile_defaults = apply_firmware_profile(self._firmware_profile)
        self._baud_rate = int(self._app_settings.get("baud_rate", self._profile_defaults["baud_rate"]))
        self.serial_handler.baudrate = self._baud_rate
        self._data_bits = int(self._app_settings.get("data_bits", 8))
        self._parity = self._app_settings.get("parity", "None")
        self._stop_bits = float(self._app_settings.get("stop_bits", 1))
        self._llm_timeout = int(self._app_settings.get("llm_timeout_sec", 60))
        self._export_format = self._app_settings.get("export_format", "JSON")
        self._sample_rate_mode = self._app_settings.get("sample_rate_mode", "configured")
        self._diagnosis_export_dir = self._app_settings.get("diagnosis_dir", str(DIAGNOSIS_DIR))
        self._selected_model_preset = list(MODEL_PRESETS.keys())[0]
        
        # Performance monitoring
        self.performance_monitor = PerformanceMonitor()
        self.performance_monitor.start_monitoring()

        self.sample_rate_tracker = SampleRateTracker(configured_rate=configured_sr)
        self._latest_device_hr: Optional[float] = None
        self._demo_mode = False
        self._demo_phase = 0.0
        self._demo_after_id = None
        self._demo_bpm = 72.0
        
        self.create_main_window()
        self.setup_ui()
        self.setup_data_processing()
        self._apply_env_api_config()
        
    def create_main_window(self):
        """Create and configure main window"""
        self.root = ctk.CTk()
        self.root.title("ECG AI 智能心电诊断平台 | Clinical ECG Intelligence")
        self.root.geometry(f"{LAYOUT['window_width']}x{LAYOUT['window_height']}")
        self.root.configure(fg_color=BG_DARK)
        
        self.root.resizable(True, True)
        self.root.minsize(1200, 700)
        self._set_window_icon()
        
        # Center window on screen
        self.center_window()
        
        # Configure closing behavior
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
    
    def _set_window_icon(self) -> None:
        icon_path = get_app_icon_path(prefer_ico=(sys.platform == "win32"))
        if not icon_path:
            return
        try:
            if sys.platform == "win32" and icon_path.suffix.lower() == ".ico":
                self.root.iconbitmap(default=str(icon_path))
            else:
                img = tk.PhotoImage(file=str(icon_path))
                self.root.iconphoto(True, img)
                self._window_icon_ref = img
        except Exception:
            pass

    def center_window(self):
        """Center window on screen"""
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - LAYOUT['window_width']) // 2
        y = (self.root.winfo_screenheight() - LAYOUT['window_height']) // 2
        self.root.geometry(f"{LAYOUT['window_width']}x{LAYOUT['window_height']}+{x}+{y}")
    
    def setup_ui(self):
        """Setup the complete user interface"""
        # Main container with header and content
        self.main_container = ctk.CTkFrame(self.root, fg_color="transparent")
        self.main_container.pack(fill="both", expand=True, padx=10, pady=10)
        
        self.create_header()
        self.create_main_content()
        self.create_footer()
    
    def create_header(self):
        """Create modern header with title and controls"""
        self.header_frame = ctk.CTkFrame(
            self.main_container,
            height=LAYOUT["header_height"],
            fg_color=BG_CARD,
            corner_radius=LAYOUT["radius_lg"]
        )
        self.header_frame.pack(fill="x", pady=(0, 10))
        self.header_frame.pack_propagate(False)
        
        # Left side - Logo and title
        left_header = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        left_header.pack(side="left", fill="y", padx=LAYOUT["padding_lg"])
        
        # App title with icon
        title_label = ctk.CTkLabel(
            left_header,
            text="ECG AI 智能心电诊断",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["title"], weight="bold"),
            text_color=TEXT_WHITE
        )
        title_label.pack(side="left", pady=LAYOUT["padding_md"])
        
        # Subtitle
        subtitle_label = ctk.CTkLabel(
            left_header,
            text="实时监护 · 采样率校准 · 大模型辅助判读",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["small"]),
            text_color=TEXT_GRAY
        )
        subtitle_label.pack(side="left", padx=(LAYOUT["padding_md"], 0), pady=LAYOUT["padding_md"])
        
        # Right side - Controls
        right_header = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        right_header.pack(side="right", fill="y", padx=LAYOUT["padding_lg"])
        
        # Settings button
        self.settings_btn = ModernButton(
            right_header,
            text="Settings",
            style="secondary",
            icon="settings",
            width=100,
            command=self.open_settings
        )
        self.settings_btn.pack(side="right", padx=(LAYOUT["padding_sm"], 0), pady=LAYOUT["padding_md"])
        
        self.quick_start_btn = ModernButton(
            right_header,
            text="一键启动",
            style="success",
            width=100,
            command=self.quick_start_workflow,
        )
        self.quick_start_btn.pack(side="right", padx=(LAYOUT["padding_sm"], 0), pady=LAYOUT["padding_md"])

        # Help button
        self.help_btn = ModernButton(
            right_header,
            text="Help",
            style="secondary", 
            icon="help",
            width=80,
            command=self.show_help
        )
        self.help_btn.pack(side="right", pady=LAYOUT["padding_md"])
    
    def create_main_content(self):
        """Create main content area with ECG monitor and diagnosis panels"""
        self.content_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.content_frame.pack(fill="both", expand=True)
        
        # Left panel - ECG Monitor
        self.create_ecg_panel()
        
        # Right panel - AI Diagnosis
        self.create_diagnosis_panel()
    
    def create_ecg_panel(self):
        """Create ECG monitoring panel"""
        self.ecg_panel = ModernCard(
            self.content_frame,
            title="医疗级心电监护  ECG Monitor"
        )
        self.ecg_panel.pack(side="left", fill="both", expand=True, padx=(0, 5))
        
        ecg_content = self.ecg_panel.get_content_frame()
        
        # ECG Plot with performance optimization
        plot_frame = ctk.CTkFrame(ecg_content, fg_color=BG_LIGHT, corner_radius=LAYOUT["radius_lg"])
        plot_frame.pack(fill="both", expand=True, pady=(0, 10))
        
        self.ecg_plot = OptimizedECGPlotter(
            plot_frame,
            width=800,
            height=400,
            sample_rate=float(
                self._app_settings.get(
                    "sample_rate_hz",
                    self._profile_defaults.get("sample_rate_hz", DEFAULT_SAMPLE_RATE),
                )
            ),
            time_window_sec=float(self._app_settings.get("time_window_sec", 10)),
            paper_speed_mm_s=float(self._app_settings.get("paper_speed_mm_s", 25)),
            gain_mm_per_mv=float(self._app_settings.get("gain_mm_per_mv", 10)),
            uv_per_count=float(
                self._app_settings.get(
                    "uv_per_count",
                    self._profile_defaults.get("uv_per_count", 12.2),
                )
            ),
        )
        self.ecg_plot.update_interval = int(self._app_settings.get("update_interval_ms", 50))
        
        # Control panel
        self.create_control_panel(ecg_content)
        
        # Statistics panel
        self.create_statistics_panel(ecg_content)
    
    def create_control_panel(self, parent):
        """Create device control panel"""
        control_card = ModernCard(parent, title="设备控制  Device Control")
        control_card.pack(fill="x", pady=(0, 10))
        
        control_content = control_card.get_content_frame()
        
        # Port selection row
        port_frame = ctk.CTkFrame(control_content, fg_color="transparent")
        port_frame.pack(fill="x", pady=(0, 10))
        
        port_label = ctk.CTkLabel(
            port_frame,
            text="Serial Port:",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["body"]),
            text_color=TEXT_WHITE
        )
        port_label.pack(side="left")
        
        self.port_combo = ctk.CTkComboBox(
            port_frame,
            width=200,
            fg_color=BG_LIGHT,
            button_color=SECONDARY_BLUE,
            button_hover_color=PRIMARY_BLUE,
            text_color=TEXT_WHITE
        )
        self.port_combo.pack(side="left", padx=(10, 0))
        
        # Refresh ports button
        self.refresh_btn = ModernButton(
            port_frame,
            text="Refresh",
            style="secondary",
            icon="refresh",
            width=100,
            command=self.scan_ports
        )
        self.refresh_btn.pack(side="right")
        
        # Connection controls row
        connect_frame = ctk.CTkFrame(control_content, fg_color="transparent")
        connect_frame.pack(fill="x", pady=(0, 10))
        
        # Connect button
        self.connect_btn = ModernButton(
            connect_frame,
            text="Connect",
            style="primary",
            icon="connect",
            width=120,
            command=self.toggle_connection
        )
        self.connect_btn.pack(side="left")
        
        # Record button
        self.record_btn = ModernButton(
            connect_frame,
            text="Start Recording",
            style="success",
            icon="record", 
            width=140,
            state="disabled",
            command=self.toggle_recording
        )
        self.record_btn.pack(side="left", padx=(10, 0))
        
        # Connection status
        self.connection_status = StatusIndicator(connect_frame, status="disconnected")
        self.connection_status.pack(side="right")

        # Paper speed / gain quick controls
        scale_frame = ctk.CTkFrame(control_content, fg_color="transparent")
        scale_frame.pack(fill="x", pady=(0, 8))

        ctk.CTkLabel(scale_frame, text="走纸:", text_color=TEXT_GRAY, font=ctk.CTkFont(size=11)).pack(side="left")
        self.speed_combo = ctk.CTkComboBox(
            scale_frame, width=90, values=["12.5", "25", "50"],
            fg_color=BG_LIGHT, text_color=TEXT_WHITE,
            command=self._on_paper_speed_changed,
        )
        self.speed_combo.set("25")
        self.speed_combo.pack(side="left", padx=(4, 12))

        ctk.CTkLabel(scale_frame, text="增益:", text_color=TEXT_GRAY, font=ctk.CTkFont(size=11)).pack(side="left")
        self.gain_quick_combo = ctk.CTkComboBox(
            scale_frame, width=80, values=["5", "10", "20"],
            fg_color=BG_LIGHT, text_color=TEXT_WHITE,
            command=self._on_gain_changed,
        )
        self.gain_quick_combo.set("10")
        self.gain_quick_combo.pack(side="left", padx=(4, 12))

        self.demo_btn = ModernButton(
            scale_frame,
            text="演示波形",
            style="secondary",
            width=100,
            command=self.toggle_demo_mode,
        )
        self.demo_btn.pack(side="right")
    
    def create_statistics_panel(self, parent):
        """Create real-time statistics panel"""
        stats_card = ModernCard(parent, title="Real-time Statistics")
        stats_card.pack(fill="x")
        
        stats_content = stats_card.get_content_frame()
        
        stats_grid = ctk.CTkFrame(stats_content, fg_color="transparent")
        stats_grid.pack(fill="x")
        
        for column in range(5):
            stats_grid.grid_columnconfigure(column, weight=1)

        hr_frame, self.hr_label = self.create_metric_tile(stats_grid, "心率 HR", "-- BPM", SUCCESS_GREEN)
        hr_frame.grid(row=0, column=0, padx=(0, 4), pady=5, sticky="ew")

        rhythm_frame, self.rhythm_label = self.create_metric_tile(stats_grid, "节律", "等待数据", TEXT_WHITE)
        rhythm_frame.grid(row=0, column=1, padx=2, pady=5, sticky="ew")

        quality_frame, self.quality_label = self.create_metric_tile(stats_grid, "信号质量", "等待数据", TEXT_WHITE)
        quality_frame.grid(row=0, column=2, padx=2, pady=5, sticky="ew")

        fs_frame, self.sample_rate_label = self.create_metric_tile(stats_grid, "采样率", f"{DEFAULT_SAMPLE_RATE:.0f} Hz", TEXT_WHITE)
        fs_frame.grid(row=0, column=3, padx=2, pady=5, sticky="ew")

        range_frame, self.range_label = self.create_metric_tile(stats_grid, "幅值 P-P", "-- mV", TEXT_WHITE)
        range_frame.grid(row=0, column=4, padx=(4, 0), pady=5, sticky="ew")

        self.hr_source_label = ctk.CTkLabel(
            stats_content,
            text="心率来源：—",
            font=ctk.CTkFont(size=10),
            text_color=TEXT_GRAY,
        )
        self.hr_source_label.pack(anchor="w", padx=4, pady=(0, 4))

    def create_metric_tile(self, parent, title: str, value: str, value_color: str = TEXT_WHITE):
        """Create a compact metric tile used across the clinician dashboard."""
        tile = ctk.CTkFrame(parent, fg_color=BG_LIGHT, corner_radius=8)
        ctk.CTkLabel(
            tile,
            text=title,
            font=ctk.CTkFont(size=10),
            text_color=TEXT_GRAY
        ).pack(pady=(6, 0))
        value_label = ctk.CTkLabel(
            tile,
            text=value,
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=value_color,
            justify="center"
        )
        value_label.pack(pady=(0, 6), padx=8)
        return tile, value_label
    
    def create_diagnosis_panel(self):
        """Create AI diagnosis panel"""
        self.diagnosis_panel = ModernCard(
            self.content_frame,
            title="AI 智能判读"
        )
        self.diagnosis_panel.pack(side="right", fill="both", expand=False, 
                                padx=(5, 0), ipadx=LAYOUT["sidebar_width"]-40)
        
        diagnosis_content = self.diagnosis_panel.get_content_frame()
        
        # API Configuration
        self.create_api_config(diagnosis_content)
        
        # Patient Information
        self.create_patient_info(diagnosis_content)
        
        # Diagnosis Controls
        self.create_diagnosis_controls(diagnosis_content)
        
        # Results Display
        self.create_results_display(diagnosis_content)
    
    def create_api_config(self, parent):
        """Create API configuration section with model selection"""
        api_card = ModernCard(parent, title="大模型与 API")
        api_card.pack(fill="x", pady=(0, 10))
        
        api_content = api_card.get_content_frame()
        
        # Model preset selector
        model_frame = ctk.CTkFrame(api_content, fg_color="transparent")
        model_frame.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(model_frame, text="Model:", text_color=TEXT_WHITE).pack(anchor="w")
        self.model_combo = ctk.CTkComboBox(
            model_frame,
            values=list(MODEL_PRESETS.keys()),
            fg_color=BG_LIGHT,
            button_color=SECONDARY_BLUE,
            button_hover_color=PRIMARY_BLUE,
            text_color=TEXT_WHITE,
            command=self._on_model_preset_changed,
        )
        self.model_combo.pack(fill="x", pady=(5, 0))
        self.model_combo.set(self._selected_model_preset)
        
        # API Key input
        key_frame = ctk.CTkFrame(api_content, fg_color="transparent")
        key_frame.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(key_frame, text="API Key:", text_color=TEXT_WHITE).pack(anchor="w")
        self.api_key_entry = ctk.CTkEntry(
            key_frame,
            placeholder_text="Enter your API key",
            show="*",
            fg_color=BG_LIGHT,
            border_color=TEXT_GRAY,
            text_color=TEXT_WHITE
        )
        self.api_key_entry.pack(fill="x", pady=(5, 0))
        
        # API URL input  
        url_frame = ctk.CTkFrame(api_content, fg_color="transparent")
        url_frame.pack(fill="x", pady=(0, 10))
        
        ctk.CTkLabel(url_frame, text="API URL:", text_color=TEXT_WHITE).pack(anchor="w")
        self.api_url_entry = ctk.CTkEntry(
            url_frame,
            fg_color=BG_LIGHT,
            border_color=TEXT_GRAY,
            text_color=TEXT_WHITE
        )
        self.api_url_entry.pack(fill="x", pady=(5, 0))

        # Model ID input (for custom)
        model_id_frame = ctk.CTkFrame(api_content, fg_color="transparent")
        model_id_frame.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(model_id_frame, text="Model ID:", text_color=TEXT_WHITE).pack(anchor="w")
        self.model_id_entry = ctk.CTkEntry(
            model_id_frame,
            fg_color=BG_LIGHT,
            border_color=TEXT_GRAY,
            text_color=TEXT_WHITE,
            placeholder_text="e.g. gpt-4o, gemini-pro"
        )
        self.model_id_entry.pack(fill="x", pady=(5, 0))

        # Populate from default preset
        self._on_model_preset_changed(self._selected_model_preset)
        
        # Setup button and status
        setup_frame = ctk.CTkFrame(api_content, fg_color="transparent")
        setup_frame.pack(fill="x")
        
        self.setup_api_btn = ModernButton(
            setup_frame,
            text="Setup API",
            style="primary",
            width=100,
            command=self.setup_diagnosis_api
        )
        self.setup_api_btn.pack(side="left")
        
        self.api_status = StatusIndicator(setup_frame, status="disconnected")
        self.api_status.pack(side="right")
    
    def create_patient_info(self, parent):
        """Create patient information section"""
        patient_card = ModernCard(parent, title="患者信息")
        patient_card.pack(fill="x", pady=(0, 10))
        
        patient_content = patient_card.get_content_frame()
        
        # Age and Gender row
        info_row1 = ctk.CTkFrame(patient_content, fg_color="transparent")
        info_row1.pack(fill="x", pady=(0, 10))
        
        # Age
        age_frame = ctk.CTkFrame(info_row1, fg_color="transparent")
        age_frame.pack(side="left", fill="x", expand=True)
        
        ctk.CTkLabel(age_frame, text="Age:", text_color=TEXT_WHITE).pack(anchor="w")
        self.age_entry = ctk.CTkEntry(
            age_frame,
            width=80,
            placeholder_text="45",
            fg_color=BG_LIGHT,
            border_color=TEXT_GRAY,
            text_color=TEXT_WHITE
        )
        self.age_entry.pack(fill="x", pady=(5, 0))
        
        # Gender
        gender_frame = ctk.CTkFrame(info_row1, fg_color="transparent")
        gender_frame.pack(side="right", fill="x", expand=True, padx=(10, 0))
        
        ctk.CTkLabel(gender_frame, text="Gender:", text_color=TEXT_WHITE).pack(anchor="w")
        self.gender_combo = ctk.CTkComboBox(
            gender_frame,
            values=["", "Male", "Female", "Other"],
            fg_color=BG_LIGHT,
            button_color=SECONDARY_BLUE,
            text_color=TEXT_WHITE
        )
        self.gender_combo.pack(fill="x", pady=(5, 0))
        
        # Symptoms
        symptoms_frame = ctk.CTkFrame(patient_content, fg_color="transparent")
        symptoms_frame.pack(fill="x")
        
        ctk.CTkLabel(symptoms_frame, text="Symptoms:", text_color=TEXT_WHITE).pack(anchor="w")
        self.symptoms_entry = ctk.CTkEntry(
            symptoms_frame,
            placeholder_text="e.g., chest pain, shortness of breath",
            fg_color=BG_LIGHT,
            border_color=TEXT_GRAY,
            text_color=TEXT_WHITE
        )
        self.symptoms_entry.pack(fill="x", pady=(5, 0))
    
    def create_diagnosis_controls(self, parent):
        """Create diagnosis control section"""
        control_card = ModernCard(parent, title="判读控制")
        control_card.pack(fill="x", pady=(0, 10))
        
        control_content = control_card.get_content_frame()
        
        # Progress indicator
        self.progress_indicator = ProgressIndicator(control_content)
        
        # Control buttons row 1
        button_frame = ctk.CTkFrame(control_content, fg_color="transparent")
        button_frame.pack(fill="x", pady=(0, 10))
        
        self.diagnose_btn = ModernButton(
            button_frame,
            text="分析心电图",
            style="primary",
            icon="heart",
            state="disabled",
            command=self.start_diagnosis
        )
        self.diagnose_btn.pack(side="left", fill="x", expand=True)
        
        self.auto_diagnosis_btn = ModernButton(
            button_frame,
            text="Auto Mode",
            style="secondary",
            command=self.toggle_auto_diagnosis
        )
        self.auto_diagnosis_btn.pack(side="right", padx=(10, 0))

        # Control buttons row 2 - Export
        export_frame = ctk.CTkFrame(control_content, fg_color="transparent")
        export_frame.pack(fill="x", pady=(0, 10))

        self.export_btn = ModernButton(
            export_frame,
            text="Export Report",
            style="secondary",
            icon="download",
            command=self.export_diagnosis_report,
        )
        self.export_btn.pack(side="left", fill="x", expand=True)

        self.export_all_btn = ModernButton(
            export_frame,
            text="Export All History",
            style="secondary",
            command=self.export_all_history,
        )
        self.export_all_btn.pack(side="right", padx=(10, 0))
        
        # Status label
        self.diagnosis_status_label = ctk.CTkLabel(
            control_content,
            text="Ready for diagnosis",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["small"]),
            text_color=TEXT_GRAY
        )
        self.diagnosis_status_label.pack()
    
    def create_results_display(self, parent):
        """Create diagnosis results display"""
        self.results_tabs = ModernTabView(parent)
        self.results_tabs.pack(fill="both", expand=True)
        
        self.results_tabs.add("Current")
        current_tab = self.results_tabs.tab("Current")

        overview_frame = ctk.CTkFrame(
            current_tab,
            fg_color=BG_DARK,
            corner_radius=LAYOUT["radius_md"],
            border_width=1,
            border_color=CARD_STYLE["border"]
        )
        overview_frame.pack(fill="x", padx=5, pady=(5, 10))

        overview_header = ctk.CTkFrame(overview_frame, fg_color="transparent")
        overview_header.pack(fill="x", padx=12, pady=(12, 6))

        self.current_severity_badge = ctk.CTkLabel(
            overview_header,
            text="Awaiting analysis",
            fg_color=BG_LIGHT,
            corner_radius=999,
            text_color=TEXT_WHITE,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["small"], weight="bold")
        )
        self.current_severity_badge.pack(side="left", ipadx=10, ipady=3)

        self.current_confidence_label = ctk.CTkLabel(
            overview_header,
            text="Confidence --",
            text_color=TEXT_GRAY,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["small"], weight="bold")
        )
        self.current_confidence_label.pack(side="right")

        self.current_primary_label = ctk.CTkLabel(
            overview_frame,
            text="Run an ECG analysis to populate the clinical summary.",
            text_color=TEXT_WHITE,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["subheading"], weight="bold"),
            justify="left",
            anchor="w",
            wraplength=480
        )
        self.current_primary_label.pack(fill="x", padx=12)

        self.current_meta_label = ctk.CTkLabel(
            overview_frame,
            text="The current trace metrics update continuously so the doctor can judge rhythm, rate, and signal quality before running AI analysis.",
            text_color=TEXT_GRAY,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["small"]),
            justify="left",
            anchor="w",
            wraplength=480
        )
        self.current_meta_label.pack(fill="x", padx=12, pady=(4, 12))

        summary_metrics = ctk.CTkFrame(current_tab, fg_color="transparent")
        summary_metrics.pack(fill="x", padx=5, pady=(0, 10))
        for column in range(4):
            summary_metrics.grid_columnconfigure(column, weight=1)

        result_hr_frame, self.result_hr_value = self.create_metric_tile(summary_metrics, "心率", "-- BPM", SUCCESS_GREEN)
        result_hr_frame.grid(row=0, column=0, padx=(0, 5), pady=5, sticky="ew")

        result_rhythm_frame, self.result_rhythm_value = self.create_metric_tile(summary_metrics, "Rhythm", "Awaiting data", TEXT_WHITE)
        result_rhythm_frame.grid(row=0, column=1, padx=2.5, pady=5, sticky="ew")

        result_quality_frame, self.result_quality_value = self.create_metric_tile(summary_metrics, "Signal Quality", "Awaiting data", TEXT_WHITE)
        result_quality_frame.grid(row=0, column=2, padx=2.5, pady=5, sticky="ew")

        result_window_frame, self.result_window_value = self.create_metric_tile(summary_metrics, "Analysis Window", "--", TEXT_WHITE)
        result_window_frame.grid(row=0, column=3, padx=(5, 0), pady=5, sticky="ew")

        details_frame = ctk.CTkScrollableFrame(current_tab, fg_color="transparent")
        details_frame.pack(fill="both", expand=True, padx=5, pady=(0, 5))

        self.findings_text = self.create_result_section(details_frame, "关键发现", height=120)
        self.actions_text = self.create_result_section(details_frame, "即时建议", height=110)
        self.follow_up_text = self.create_result_section(details_frame, "随访与生活方式", height=110)
        self.notes_text = self.create_result_section(details_frame, "临床备注", height=140)

        self.results_tabs.add("History")
        history_tab = self.results_tabs.tab("History")
        
        self.history_text = ctk.CTkTextbox(
            history_tab,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["small"]),
            fg_color=BG_DARK,
            text_color=TEXT_WHITE
        )
        self.history_text.pack(fill="both", expand=True, padx=5, pady=5)
        
        self.results_tabs.add("ECG Stats")
        stats_tab = self.results_tabs.tab("ECG Stats")
        
        self.ecg_stats_text = ctk.CTkTextbox(
            stats_tab,
            font=ctk.CTkFont(family=FONT_FAMILY_MONO, size=FONT_SIZES["small"]),
            fg_color=BG_DARK,
            text_color=TEXT_WHITE
        )
        self.ecg_stats_text.pack(fill="both", expand=True, padx=5, pady=5)

        self.set_textbox_content(self.findings_text, "No findings available yet.")
        self.set_textbox_content(self.actions_text, "Run an ECG analysis to populate immediate actions.")
        self.set_textbox_content(self.follow_up_text, "Follow-up guidance will appear here after diagnosis.")
        self.set_textbox_content(self.notes_text, "Comparisons with normal ranges, risk factors, and prognosis notes will appear here.")
        self.update_clinical_snapshot()

    def create_result_section(self, parent, title: str, height: int = 120):
        """Create a structured result section with a read-only textbox."""
        section = ctk.CTkFrame(parent, fg_color=BG_LIGHT, corner_radius=LAYOUT["radius_md"])
        section.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(
            section,
            text=title,
            text_color=TEXT_WHITE,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["body"], weight="bold")
        ).pack(anchor="w", padx=12, pady=(10, 6))

        textbox = ctk.CTkTextbox(
            section,
            height=height,
            fg_color=BG_DARK,
            text_color=TEXT_LIGHT,
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["body"]),
            wrap="word"
        )
        textbox.pack(fill="x", padx=12, pady=(0, 12))
        textbox.configure(state="disabled")
        return textbox

    def set_textbox_content(self, textbox, text: str):
        """Update a read-only textbox without exposing editing state to the rest of the UI."""
        textbox.configure(state="normal")
        textbox.delete("1.0", "end")
        textbox.insert("1.0", text.strip() if text else "No data available.")
        textbox.configure(state="disabled")

    def get_heart_rate_style(self, heart_rate: Optional[float]):
        """Return display text and color for the heart-rate metric."""
        if not heart_rate:
            return "-- BPM", TEXT_GRAY
        if 60 <= heart_rate <= 100:
            return f"{heart_rate:.0f} BPM", SUCCESS_GREEN
        if 50 <= heart_rate <= 110:
            return f"{heart_rate:.0f} BPM", WARNING_YELLOW
        return f"{heart_rate:.0f} BPM", ERROR_RED

    def update_clinical_snapshot(self, metrics: Optional[Dict[str, Any]] = None):
        """Sync real-time strip metrics into both the monitor and diagnosis summary."""
        metrics = metrics or self.ecg_plot.get_metrics()

        hr_text, hr_color = self.get_heart_rate_style(metrics.get("heart_rate_bpm"))
        self.hr_label.configure(text=hr_text, text_color=hr_color)
        self.result_hr_value.configure(text=hr_text, text_color=hr_color)

        rhythm_label = metrics.get("rhythm_label", "Awaiting data")
        rhythm_color = metrics.get("rhythm_color", TEXT_GRAY)
        self.rhythm_label.configure(text=rhythm_label, text_color=rhythm_color)
        self.result_rhythm_value.configure(text=rhythm_label, text_color=rhythm_color)

        quality_label = metrics.get("signal_quality_label", "Awaiting data")
        quality_color = metrics.get("signal_quality_color", TEXT_GRAY)
        self.quality_label.configure(text=quality_label, text_color=quality_color)
        self.result_quality_value.configure(text=quality_label, text_color=quality_color)

        ptp_mv = metrics.get("peak_to_peak_mv") or (metrics.get("peak_to_peak", 0.0) / 1000.0)
        self.range_label.configure(
            text=f"{ptp_mv:.2f} mV" if ptp_mv else "-- mV",
            text_color=TEXT_WHITE if ptp_mv else TEXT_GRAY,
        )

        duration = metrics.get("duration_sec", 0.0)
        samples = metrics.get("sample_count", 0)
        if duration > 0 and samples > 0:
            self.result_window_value.configure(text=f"{duration:.1f}s | {samples}", text_color=TEXT_WHITE)
        else:
            self.result_window_value.configure(text="--", text_color=TEXT_GRAY)

        fs = metrics.get("sample_rate_hz", self.ecg_plot.sample_rate)
        mode_tag = "自动" if getattr(self, "_sample_rate_mode", "configured") == "auto" else "配置"
        self.sample_rate_label.configure(text=f"{fs:.0f} Hz ({mode_tag})", text_color=TEXT_WHITE)

        source_map = {
            "device": "固件心率",
            "computed": "R 峰检测",
            "fused": "固件+R峰融合",
            "unknown": "—",
        }
        self.hr_source_label.configure(
            text=f"心率来源：{source_map.get(metrics.get('heart_rate_source'), '—')}"
        )
    
    def create_footer(self):
        """Create footer with status information"""
        self.footer_frame = ctk.CTkFrame(
            self.main_container,
            height=LAYOUT["footer_height"],
            fg_color=BG_CARD,
            corner_radius=LAYOUT["radius_md"]
        )
        self.footer_frame.pack(fill="x", pady=(10, 0))
        self.footer_frame.pack_propagate(False)
        
        # Status information
        status_frame = ctk.CTkFrame(self.footer_frame, fg_color="transparent")
        status_frame.pack(fill="both", expand=True, padx=LAYOUT["padding_md"])
        
        self.footer_status = ctk.CTkLabel(
            status_frame,
            text="Acquisition: Ready | Heart Rate: -- BPM | Last Diagnosis: Never",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["small"]),
            text_color=TEXT_GRAY
        )
        self.footer_status.pack(side="left", pady=10)

        self.footer_acquisition_text = "Ready"
        self.footer_diagnosis_text = "Last Diagnosis: Never"
        self.footer_performance_text = ""
        
        # Version info
        version_label = ctk.CTkLabel(
            status_frame,
            text="ECG AI v3.0 Clinical",
            font=ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["tiny"]),
            text_color=TEXT_GRAY
        )
        version_label.pack(side="right", pady=10)
    
    def setup_data_processing(self):
        """Setup data processing and timers"""
        self.data_queue = []
        self.data_lock = threading.Lock()
        self.data_after_id = None
        self.auto_diagnosis_after_id = self.root.after(1000, self.check_auto_diagnosis)
        self.stats_after_id = self.root.after(1000, self.update_statistics)
        
        # Initial port scan
        self.scan_ports()
    
    def run(self):
        """Start the GUI main loop with cleanup handling"""
        try:
            self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
            self.root.mainloop()
        finally:
            self.cleanup()
    
    def on_closing(self):
        """Handle application closing"""
        try:
            self._persist_settings()
        except Exception:
            pass
        try:
            self.cancel_scheduled_callbacks()

            # Stop performance monitoring
            if hasattr(self, 'performance_monitor'):
                self.performance_monitor.stop_monitoring()
            
            # Disconnect serial if connected
            if hasattr(self, 'serial_handler') and self.serial_handler.is_connected:
                self.serial_handler.disconnect()
            
            # Stop data recording if active
            if hasattr(self, 'data_recorder') and self.data_recorder.recording:
                self.data_recorder.stop_recording()
                
            # Print final performance report
            if hasattr(self, 'performance_monitor'):
                print("\n" + "="*50)
                print("📊 Final Performance Report")
                print("="*50)
                self.performance_monitor.print_performance_summary()
                print("="*50)
                
        except Exception as e:
            print(f"Error during cleanup: {e}")
        finally:
            self.root.destroy()
    
    def cleanup(self):
        """Cleanup resources"""
        try:
            self.cancel_scheduled_callbacks()
            if hasattr(self, 'performance_monitor'):
                self.performance_monitor.stop_monitoring()
        except Exception as e:
            print(f"Cleanup error: {e}")

    def _on_paper_speed_changed(self, value: str):
        try:
            self.ecg_plot.set_paper_speed(float(value))
        except ValueError:
            pass

    def _on_gain_changed(self, value: str):
        try:
            self.ecg_plot.set_gain(float(value))
        except ValueError:
            pass

    def toggle_demo_mode(self):
        """Inject synthetic medical ECG for visualization testing."""
        if self._demo_mode:
            self.stop_demo_mode()
            return
        if self.serial_handler.is_connected:
            self.show_warning("演示模式", "请先断开串口连接，再启动演示波形。")
            return
        self.start_demo_mode()

    def start_demo_mode(self):
        self._demo_mode = True
        self._demo_phase = 0.0
        self.ecg_buffer.clear()
        self.ecg_plot.clear_data()
        self.ecg_plot.set_device_heart_rate(self._demo_bpm)
        self._latest_device_hr = self._demo_bpm

        preload = fill_demo_buffer(
            total_seconds=self.ecg_plot.time_window_sec + 2,
            bpm=self._demo_bpm,
            sample_rate=self._effective_sample_rate(),
        )
        for value in preload:
            self.ecg_buffer.append([value])

        self.demo_btn.configure(text="停止演示", fg_color=WARNING_YELLOW)
        self.connection_status.update_status("connected")
        self.update_footer_status("演示模式 — 医疗级模拟心电")
        self.start_data_processing_loop()
        self._schedule_demo_tick()

    def stop_demo_mode(self):
        if not self._demo_mode and self._demo_after_id is None:
            return
        self._demo_mode = False
        if self._demo_after_id is not None:
            try:
                self.root.after_cancel(self._demo_after_id)
            except Exception:
                pass
            self._demo_after_id = None
        if hasattr(self, "demo_btn"):
            self.demo_btn.configure(text="演示波形")
        if hasattr(self, "ecg_buffer"):
            self.ecg_buffer.clear()
        if hasattr(self, "ecg_plot"):
            self.ecg_plot.clear_data()
        if hasattr(self, "connection_status"):
            self.connection_status.update_status("disconnected")
        if hasattr(self, "footer_status"):
            self.update_footer_status("演示已停止")

    def _schedule_demo_tick(self):
        if not self._demo_mode:
            return
        rate = self._effective_sample_rate()
        chunk = generate_medical_demo_chunk(
            bpm=self._demo_bpm,
            sample_rate=rate,
            seconds=0.1,
            phase=self._demo_phase,
        )
        self._demo_phase += 0.1
        for value in chunk:
            self.ecg_buffer.append([value])
        recent = self.ecg_buffer.get_recent_data(self.ecg_plot.max_points)
        self.ecg_plot.update_data(recent.tolist(), sample_rate=rate)
        self.update_clinical_snapshot(self.ecg_plot.get_metrics())
        self._demo_after_id = self.root.after(100, self._schedule_demo_tick)

    def cancel_scheduled_callbacks(self):
        """Cancel recurring Tk callbacks so reconnects and shutdown stay stable."""
        self.stop_demo_mode()
        for attr_name in ("data_after_id", "auto_diagnosis_after_id", "stats_after_id"):
            callback_id = getattr(self, attr_name, None)
            if callback_id:
                try:
                    self.root.after_cancel(callback_id)
                except Exception:
                    pass
                setattr(self, attr_name, None)

    def start_data_processing_loop(self):
        """Start the Tk-based data queue polling loop if it is not already running."""
        if self.data_after_id is None:
            self.data_after_id = self.root.after(50, self.process_data_queue)
    
    # Implementation of core functionality methods
    
    def scan_ports(self):
        """Scan for available serial ports"""
        try:
            ports = self.serial_handler.list_ports()
            self.port_combo.configure(values=ports)
            if ports:
                self.port_combo.set(ports[0])
                self.update_footer_status(f"Found {len(ports)} serial ports")
            else:
                self.update_footer_status("No serial ports found")
        except Exception as e:
            self.show_error("Port Scan Error", f"Failed to scan ports: {str(e)}")
    
    def toggle_connection(self):
        """Connect to or disconnect from the selected serial port"""
        if self._demo_mode:
            self.stop_demo_mode()
        if not self.serial_handler.is_connected:
            port = self.port_combo.get()
            if not port:
                self.show_warning("Connection Error", "Please select a serial port.")
                return
            
            self.connect_btn.configure(text="Connecting...", state="disabled")
            self.connection_status.update_status("connecting")
            
            # Connect in background thread
            def connect_thread():
                try:
                    self.serial_handler.baudrate = self._baud_rate
                    success = self.serial_handler.connect(port)
                    self.root.after(0, self.on_connection_result, success, port)
                except Exception as e:
                    self.root.after(0, self.on_connection_error, str(e))
            
            threading.Thread(target=connect_thread, daemon=True).start()
        else:
            self.disconnect_serial()
    
    def on_connection_result(self, success: bool, port: str):
        """Handle connection result"""
        if success:
            self.connect_btn.configure(text="Disconnect", state="normal")
            self.record_btn.configure(state="normal")
            self.connection_status.update_status("connected")
            
            # Clear previous data
            self.raw_ecg_values.clear()
            self.ecg_buffer.clear()
            self.packets_received = 0
            self.ecg_plot.clear_data()
            
            # Start data processing
            self.serial_handler.start_reading(self.handle_serial_data)
            self.start_data_processing_loop()
            
            self.update_footer_status(f"Connected to {port}")
            self.show_success("Connection Successful", f"Connected to {port}")
        else:
            self.on_connection_error("Connection failed")
    
    def on_connection_error(self, error_msg: str):
        """Handle connection error"""
        self.connect_btn.configure(text="Connect", state="normal")
        self.connection_status.update_status("error")
        self.update_footer_status(f"Connection failed: {error_msg}")
        self.show_error("Connection Error", f"Could not connect to device: {error_msg}")
    
    def disconnect_serial(self):
        """Disconnect from serial port"""
        if self.data_recorder and self.data_recorder.recording:
            self.stop_recording()
        
        self.serial_handler.disconnect()
        if self.data_after_id is not None:
            try:
                self.root.after_cancel(self.data_after_id)
            except Exception:
                pass
            self.data_after_id = None
        self.connect_btn.configure(text="Connect", state="normal")
        self.record_btn.configure(state="disabled")
        self.connection_status.update_status("disconnected")
        self.update_footer_status("Disconnected from device")
    
    def toggle_recording(self):
        """Start or stop recording ECG data"""
        if not self.data_recorder.recording:
            self.start_recording()
        else:
            self.stop_recording()
    
    def start_recording(self):
        """Start recording ECG data to CSV file"""
        try:
            if self.data_recorder.start_recording():
                self.record_btn.configure(text="Stop Recording")
                self.record_btn.configure(fg_color=WARNING_YELLOW)
                filename = self.data_recorder.current_filename
                self.update_footer_status(f"Recording to {filename}")
                self.show_success("Recording Started", f"Recording ECG data to {filename}")
            else:
                self.show_error("Recording Error", "Could not start recording")
        except Exception as e:
            self.show_error("Recording Error", f"Failed to start recording: {str(e)}")
    
    def stop_recording(self):
        """Stop recording ECG data"""
        try:
            self.data_recorder.stop_recording()
            self.record_btn.configure(text="Start Recording")
            self.record_btn.configure(fg_color=SUCCESS_GREEN)
            self.update_footer_status("Recording stopped")
        except Exception as e:
            self.show_error("Recording Error", f"Failed to stop recording: {str(e)}")
    
    def setup_diagnosis_api(self):
        """Setup the AI diagnosis API client using selected model"""
        api_key = self.api_key_entry.get().strip()
        api_url = self.api_url_entry.get().strip()
        model_id = self.model_id_entry.get().strip()
        
        if not api_key:
            self.show_warning("API Setup", "Please enter an API key.")
            return
        if not api_url:
            self.show_warning("API Setup", "Please enter an API URL.")
            return
        if not model_id:
            self.show_warning("API Setup", "Please enter a Model ID.")
            return
        
        self.setup_api_btn.configure(text="Setting up...", state="disabled")
        self.api_status.update_status("connecting")
        
        def setup_thread():
            try:
                self.diagnosis_client = LLMDiagnosisClient(
                    api_key=api_key,
                    api_url=api_url,
                    model_id=model_id,
                    timeout=self._llm_timeout,
                    sample_rate=self._effective_sample_rate(),
                )
                self.root.after(0, self.on_api_setup_success)
            except Exception as e:
                self.root.after(0, self.on_api_setup_error, str(e))
        
        threading.Thread(target=setup_thread, daemon=True).start()
    
    def on_api_setup_success(self):
        """Handle successful API setup"""
        self.setup_api_btn.configure(text="Setup API", state="normal")
        self.api_status.update_status("connected")
        self.diagnose_btn.configure(state="normal" if self.ecg_buffer.count > 100 else "disabled")
        self.show_success("API Setup", "Diagnosis API configured successfully!")
        
    def on_api_setup_error(self, error_msg: str):
        """Handle API setup error"""
        self.setup_api_btn.configure(text="Setup API", state="normal") 
        self.api_status.update_status("error")
        self.show_error("API Setup Error", f"Failed to setup API: {error_msg}")
    
    def start_diagnosis(self):
        """Start ECG diagnosis analysis with performance optimizations"""
        if not self.diagnosis_client:
            self.show_warning("Diagnosis", "Please setup the API first.")
            return
        
        if self.ecg_buffer.count < 100:
            self.show_warning("Diagnosis", "Not enough ECG data for analysis. Please wait for more data.")
            return
        
        if self.diagnosis_worker and hasattr(self.diagnosis_worker, 'thread') and self.diagnosis_worker.thread.is_alive():
            self.show_info("Diagnosis", "Diagnosis already in progress.")
            return
        
        # Get patient information
        patient_info = self.get_patient_info()
        
        # Use optimized circular buffer to get diagnosis data
        effective_rate = self._effective_sample_rate()
        window_samples = int(effective_rate * 10)
        available_samples = min(max(window_samples, 1000), self.ecg_buffer.count)
        ecg_data_for_diagnosis = self.ecg_buffer.get_recent_data(available_samples).tolist()
        
        # Show progress
        self.progress_indicator.show_progress(0.1, "Starting diagnosis...")
        self.diagnose_btn.configure(state="disabled", text="Analyzing...")
        self.diagnosis_status_label.configure(text="Analyzing ECG data...", text_color=SECONDARY_BLUE)
        
        # Start diagnosis worker
        self.diagnosis_worker = DiagnosisWorker(
            self.diagnosis_client,
            ecg_data_for_diagnosis,
            patient_info,
            callback=self.on_diagnosis_completed,
            error_callback=self.on_diagnosis_error,
            sample_rate=effective_rate,
            device_hr_bpm=self._latest_device_hr,
        )
        self.diagnosis_worker.start()
        
        # Update progress periodically
        self.update_diagnosis_progress()
    
    def update_diagnosis_progress(self):
        """Update diagnosis progress indicator"""
        if self.diagnosis_worker and hasattr(self.diagnosis_worker, 'thread') and self.diagnosis_worker.thread.is_alive():
            # Simulate progress
            progress = min(0.9, time.time() % 30 / 30)  # Max 90% until complete
            self.progress_indicator.show_progress(progress, "Analyzing with AI...")
            self.root.after(500, self.update_diagnosis_progress)
    
    def on_diagnosis_completed(self, diagnosis: Dict[str, Any]):
        """Handle completed diagnosis with history management"""
        self.progress_indicator.show_progress(1.0, "Diagnosis complete!")
        self.root.after(1000, self.progress_indicator.hide)
        
        self.last_diagnosis = diagnosis
        
        # Add to history with memory management
        self.diagnosis_history.append({
            'timestamp': datetime.now().isoformat(),
            'diagnosis': diagnosis
        })
        
        # Limit history size to prevent memory bloat
        if len(self.diagnosis_history) > self.max_history_size:
            self.diagnosis_history = self.diagnosis_history[-self.max_history_size:]
        
        # Update UI
        self.display_diagnosis(diagnosis)
        self.update_diagnosis_history()
        
        # Reset UI state
        self.diagnose_btn.configure(state="normal", text="分析心电图")
        self.diagnosis_status_label.configure(text="Diagnosis completed", text_color=SUCCESS_GREEN)
        
        severity = diagnosis.get('severity', 'unknown')
        confidence = diagnosis.get('confidence', 0)
        self.update_footer_diagnosis(f"Last Diagnosis: {severity.title()} ({confidence:.0%})")
    
    def on_diagnosis_error(self, error_message: str):
        """Handle diagnosis error"""
        self.progress_indicator.hide()
        self.diagnose_btn.configure(state="normal", text="分析心电图")
        self.diagnosis_status_label.configure(text=f"Diagnosis failed: {error_message}", text_color=ERROR_RED)

        self.current_severity_badge.configure(text="Analysis Error", fg_color=ERROR_RED, text_color=TEXT_WHITE)
        self.current_confidence_label.configure(text="Confidence --", text_color=TEXT_GRAY)
        self.current_primary_label.configure(text="The AI diagnosis request failed.", text_color=TEXT_WHITE)
        self.current_meta_label.configure(text=error_message, text_color=ERROR_RED)
        self.set_textbox_content(self.findings_text, error_message)
        self.set_textbox_content(self.actions_text, "Check API connectivity, confirm the ECG trace is live, and retry the analysis.")
        self.set_textbox_content(self.follow_up_text, "If the error persists, validate the API key and review the serial data quality before another run.")
        self.set_textbox_content(self.notes_text, f"Failure time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self.results_tabs.set("Current")
        
        self.show_error("Diagnosis Error", error_message)
    
    def get_patient_info(self) -> Optional[Dict[str, Any]]:
        """Get patient information from form inputs"""
        patient_info = {}
        
        age = self.age_entry.get().strip()
        if age:
            try:
                patient_info['age'] = int(age)
            except ValueError:
                pass
        
        gender = self.gender_combo.get()
        if gender:
            patient_info['gender'] = gender.lower()
        
        symptoms = self.symptoms_entry.get().strip()
        if symptoms:
            patient_info['symptoms'] = symptoms
        
        return patient_info if patient_info else None
    
    def toggle_auto_diagnosis(self):
        """Toggle automatic diagnosis mode"""
        self.auto_diagnosis_enabled = not self.auto_diagnosis_enabled
        
        if self.auto_diagnosis_enabled:
            self.auto_diagnosis_btn.configure(
                text="Auto: ON",
                fg_color=BUTTON_SUCCESS["bg"],
                hover_color=BUTTON_SUCCESS["hover_bg"],
                text_color=BUTTON_SUCCESS["fg"],
                border_width=0
            )
            self.diagnosis_status_label.configure(text="Auto-diagnosis enabled (every 30s)", text_color=SUCCESS_GREEN)
        else:
            self.auto_diagnosis_btn.configure(
                text="Auto: OFF",
                fg_color=BUTTON_SECONDARY["bg"],
                hover_color=BUTTON_SECONDARY["hover_bg"],
                text_color=BUTTON_SECONDARY["fg"],
                border_width=1,
                border_color=BUTTON_SECONDARY["border"]
            )
            self.diagnosis_status_label.configure(text="Auto-diagnosis disabled", text_color=TEXT_GRAY)
    
    def handle_serial_data(self, data: str):
        """Handle data received from serial port (called from worker thread)"""
        with self.data_lock:
            self.data_queue.append(data)
    
    def process_data_queue(self):
        """Process queued serial data in main thread"""
        if not self.serial_handler.is_connected:
            self.data_after_id = None
            return
        
        with self.data_lock:
            data_to_process = self.data_queue.copy()
            self.data_queue.clear()
        
        for data in data_to_process:
            self.process_ecg_data(data)
        
        # Schedule next update
        if self.serial_handler.is_connected:
            self.data_after_id = self.root.after(50, self.process_data_queue)
        else:
            self.data_after_id = None
    
    def _effective_sample_rate(self) -> float:
        """Configured or auto-estimated sample rate for time-based metrics."""
        prefer_auto = getattr(self, "_sample_rate_mode", "configured") == "auto"
        rate = self.sample_rate_tracker.effective_rate(prefer_auto=prefer_auto)
        self.ecg_plot.sample_rate = rate
        if self.diagnosis_client:
            self.diagnosis_client.sample_rate = rate
        return rate

    def _apply_env_api_config(self) -> None:
        """Pre-fill API fields from project .env and auto-connect when key is present."""
        if not hasattr(self, "api_key_entry"):
            return
        cfg = get_api_config()
        if cfg.get("api_key"):
            self.api_key_entry.delete(0, "end")
            self.api_key_entry.insert(0, cfg["api_key"])
        if cfg.get("api_url"):
            self.api_url_entry.delete(0, "end")
            self.api_url_entry.insert(0, cfg["api_url"])
        if cfg.get("model_id"):
            self.model_id_entry.delete(0, "end")
            self.model_id_entry.insert(0, cfg["model_id"])
        if cfg.get("api_key") and cfg.get("api_url") and cfg.get("model_id"):
            self.root.after(800, self.setup_diagnosis_api)

    def _persist_settings(self) -> None:
        """Save current UI settings to data/config/app_settings.json."""
        payload = {
            "sample_rate_hz": float(self.ecg_plot.sample_rate),
            "sample_rate_mode": getattr(self, "_sample_rate_mode", "configured"),
            "time_window_sec": float(self.ecg_plot.time_window_sec),
            "update_interval_ms": int(self.ecg_plot.update_interval),
            "paper_speed_mm_s": float(self.ecg_plot.paper_speed_mm_s),
            "gain_mm_per_mv": float(self.ecg_plot.gain_mm_per_mv),
            "uv_per_count": float(self.ecg_plot.uv_per_count),
            "firmware_profile": getattr(self, "_firmware_profile", "protocentral_500"),
            "baud_rate": self._baud_rate,
            "data_bits": self._data_bits,
            "parity": self._parity,
            "stop_bits": self._stop_bits,
            "auto_diagnosis_interval_sec": self.auto_diagnosis_interval,
            "diagnosis_buffer_size": self.diagnosis_buffer_size,
            "llm_timeout_sec": self._llm_timeout,
            "max_history_size": self.max_history_size,
            "export_format": self._export_format,
            "recordings_dir": self.data_recorder.base_dir,
            "diagnosis_dir": getattr(self, "_diagnosis_export_dir", str(DIAGNOSIS_DIR)),
            "appearance_mode": ctk.get_appearance_mode().lower(),
        }
        save_settings(payload)
        self._app_settings = payload

    def quick_start_workflow(self) -> None:
        """One-click: env API setup, port scan, and readiness check."""
        self._apply_env_api_config()
        self.scan_ports()
        if not self.diagnosis_client:
            self.setup_diagnosis_api()
        self.update_footer_status(
            "一键启动：已加载 .env API、刷新串口。请选择端口并 Connect，或点击「演示波形」体验。"
        )
        self.show_info(
            "一键启动",
            "已完成：\n"
            "1. 从项目 .env 加载 OpenAI 兼容 API\n"
            "2. 刷新可用串口\n"
            "3. 数据目录：data/recordings、data/diagnosis\n\n"
            "若心率约为真实值 2 倍：Settings → Display → Sample Rate 选固件实际值（常见 500 Hz）。",
        )

    def process_ecg_data(self, data: str):
        """Process individual ECG data point with performance optimizations"""
        start_time = time.time()
        
        try:
            parsed = parse_ecg_line(data)
            ecg_value = parsed.ecg_value

            if ecg_value is not None:
                self.packets_received += 1
                self.performance_monitor.record_frame()
                self.sample_rate_tracker.on_sample(parsed.device_timestamp_ms)

                if parsed.device_hr_bpm is not None:
                    self._latest_device_hr = parsed.device_hr_bpm
                    self.ecg_plot.set_device_heart_rate(parsed.device_hr_bpm)

                self.ecg_buffer.append([ecg_value])

                effective_rate = self._effective_sample_rate()
                recent_data = self.ecg_buffer.get_recent_data(self.ecg_plot.max_points)
                self.ecg_plot.update_data(recent_data, sample_rate=effective_rate)
                
                # Record if enabled
                if self.data_recorder and self.data_recorder.recording:
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
                    self.data_recorder.write_data(timestamp, ecg_value)
                
                # Enable diagnosis button if enough data
                if self.ecg_buffer.count > 100 and self.diagnosis_client:
                    self.diagnose_btn.configure(state="normal")
                
                # Record processing time for performance monitoring
                processing_time = time.time() - start_time
                self.performance_monitor.record_update_time(processing_time)
                
        except Exception as e:
            print(f"Error processing ECG data: {e}")
    
    def check_auto_diagnosis(self):
        """Check if auto-diagnosis should be performed"""
        current_time = time.time()
        
        if (self.auto_diagnosis_enabled and 
            self.diagnosis_client and 
            self.ecg_buffer.count >= 1000 and
            current_time - self.last_auto_diagnosis >= self.auto_diagnosis_interval and
            not (self.diagnosis_worker and hasattr(self.diagnosis_worker, 'thread') and self.diagnosis_worker.thread.is_alive())):
            
            print("Performing automatic diagnosis...")
            self.last_auto_diagnosis = current_time
            self.start_diagnosis()
        
        # Schedule next check
        self.auto_diagnosis_after_id = self.root.after(1000, self.check_auto_diagnosis)
    
    def update_statistics(self):
        """Update real-time statistics display with performance monitoring"""
        # Get performance report
        perf_report = self.performance_monitor.get_performance_report()

        if self.ecg_buffer.count > 0:
            metrics = self.ecg_plot.get_metrics()
            if metrics.get("sample_count", 0) == 0:
                recent_data = self.ecg_buffer.get_recent_data(self.ecg_plot.max_points)
                self.ecg_plot.update_data(recent_data, sample_rate=self._effective_sample_rate())
                metrics = self.ecg_plot.get_metrics()

            self.update_clinical_snapshot(metrics)

            buffer_usage = (self.ecg_buffer.count / self.ecg_buffer.max_size) * 100
            self.update_footer_performance(
                f"CPU {perf_report['cpu_percent']:.0f}% | RAM {perf_report['memory_mb']:.0f}MB | FPS {perf_report['frame_rate']:.1f} | Buffer {buffer_usage:.0f}%"
            )
        else:
            self.update_clinical_snapshot()
            self.update_footer_performance("")
        
        # Update ECG statistics tab
        self.update_ecg_statistics_display()
        
        # Schedule next update
        self.stats_after_id = self.root.after(1000, self.update_statistics)
    
    def display_diagnosis(self, diagnosis: Dict[str, Any]):
        """Display diagnosis results"""
        severity = diagnosis.get('severity', 'unknown').lower()
        severity_color = SEVERITY_COLORS.get(severity, BG_LIGHT)
        severity_text_color = BG_DARK if severity == 'moderate' else TEXT_WHITE

        primary = diagnosis.get('primary_diagnosis', 'Unknown diagnosis')
        confidence = diagnosis.get('confidence', 0.0)
        timestamp = diagnosis.get('timestamp', datetime.now().isoformat())

        self.current_severity_badge.configure(
            text=f"{severity.upper()}" if severity != 'unknown' else "UNKNOWN",
            fg_color=severity_color,
            text_color=severity_text_color
        )
        self.current_confidence_label.configure(text=f"Confidence {confidence:.0%}", text_color=severity_color)
        self.current_primary_label.configure(text=primary, text_color=TEXT_WHITE)
        self.current_meta_label.configure(
            text=f"Updated {timestamp[:19].replace('T', ' ')} | Model {diagnosis.get('model_used', 'AI analysis')}",
            text_color=TEXT_GRAY
        )

        findings = diagnosis.get('key_findings', [])
        self.set_textbox_content(
            self.findings_text,
            "\n".join([f"- {finding}" for finding in findings]) or "No key findings were returned by the model."
        )

        recommendations = diagnosis.get('recommendations', {})
        immediate_actions = recommendations.get('immediate_actions', [])
        follow_up = recommendations.get('follow_up', [])
        lifestyle = recommendations.get('lifestyle', [])

        self.set_textbox_content(
            self.actions_text,
            "\n".join([f"- {item}" for item in immediate_actions]) or "No immediate actions were provided."
        )

        follow_up_lines = [f"- {item}" for item in follow_up + lifestyle]
        self.set_textbox_content(
            self.follow_up_text,
            "\n".join(follow_up_lines) or "No follow-up or lifestyle guidance was provided."
        )

        notes_lines = []
        secondary = diagnosis.get('secondary_conditions', [])
        if secondary:
            notes_lines.append("Secondary considerations:")
            notes_lines.extend([f"- {condition}" for condition in secondary])

        normal_ranges = diagnosis.get('normal_ranges_comparison', {})
        if normal_ranges:
            notes_lines.append("")
            notes_lines.append("Comparison with normal ranges:")
            for label, value in normal_ranges.items():
                notes_lines.append(f"- {label.replace('_', ' ').title()}: {value}")

        risk_factors = diagnosis.get('risk_factors', [])
        if risk_factors:
            notes_lines.append("")
            notes_lines.append("Risk factors:")
            notes_lines.extend([f"- {factor}" for factor in risk_factors])

        prognosis = diagnosis.get('prognosis')
        if prognosis:
            notes_lines.append("")
            notes_lines.append(f"Prognosis: {prognosis}")

        if diagnosis.get('parse_error'):
            notes_lines.append("")
            notes_lines.append(f"Parsing note: {diagnosis['parse_error']}")

        self.set_textbox_content(self.notes_text, "\n".join(notes_lines) or "No additional clinical notes were provided.")
        self.update_clinical_snapshot(self.ecg_plot.get_metrics())
        
        # Switch to current results tab
        self.results_tabs.set("Current")
    
    def format_diagnosis_text(self, diagnosis: Dict[str, Any]) -> str:
        """Format diagnosis for display"""
        lines = []
        lines.append("=== ECG DIAGNOSIS REPORT ===")
        lines.append(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")
        
        # Primary diagnosis
        primary = diagnosis.get('primary_diagnosis', 'Unknown')
        severity = diagnosis.get('severity', 'unknown')
        confidence = diagnosis.get('confidence', 0.0)
        
        lines.append(f"PRIMARY DIAGNOSIS: {primary}")
        lines.append(f"SEVERITY: {severity.upper()}")
        lines.append(f"CONFIDENCE: {confidence:.1%}")
        lines.append("")
        
        # Secondary conditions
        secondary = diagnosis.get('secondary_conditions', [])
        if secondary:
            lines.append("POSSIBLE SECONDARY CONDITIONS:")
            for condition in secondary:
                lines.append(f"• {condition}")
            lines.append("")
        
        # Key findings
        findings = diagnosis.get('key_findings', [])
        if findings:
            lines.append("KEY ECG FINDINGS:")
            for finding in findings:
                lines.append(f"• {finding}")
            lines.append("")
        
        # Recommendations
        recommendations = diagnosis.get('recommendations', {})
        if recommendations:
            lines.append("RECOMMENDATIONS:")
            
            immediate = recommendations.get('immediate_actions', [])
            if immediate:
                lines.append("Immediate Actions:")
                for action in immediate:
                    lines.append(f"• {action}")
                lines.append("")
        
        return "\n".join(lines)
    
    def update_diagnosis_history(self):
        """Update diagnosis history display"""
        self.history_text.delete("1.0", "end")
        
        history_text = ""
        for i, entry in enumerate(reversed(self.diagnosis_history[-10:])):  # Last 10 diagnoses
            timestamp = entry['timestamp']
            diagnosis = entry['diagnosis']
            
            dt = datetime.fromisoformat(timestamp)
            time_str = dt.strftime('%Y-%m-%d %H:%M:%S')
            
            primary = diagnosis.get('primary_diagnosis', 'Unknown')
            severity = diagnosis.get('severity', 'unknown')
            confidence = diagnosis.get('confidence', 0.0)
            
            history_text += f"{i+1}. [{time_str}]\n"
            history_text += f"   Diagnosis: {primary}\n"
            history_text += f"   Severity: {severity}, Confidence: {confidence:.1%}\n\n"
        
        self.history_text.insert("1.0", history_text)
    
    def update_ecg_statistics_display(self):
        """Update ECG statistics display"""
        if self.ecg_buffer.count == 0:
            return
        
        self.ecg_stats_text.delete("1.0", "end")
        
        # Get data from circular buffer
        data = self.ecg_buffer.get_recent_data(min(1000, self.ecg_buffer.count))
        metrics = self.ecg_plot.get_metrics()
        
        stats_text = f"=== ECG STATISTICS ===\n"
        stats_text += f"Last Updated: {datetime.now().strftime('%H:%M:%S')}\n\n"
        stats_text += f"Sample Count: {self.ecg_buffer.count}\n"
        rate = self._effective_sample_rate()
        stats_text += f"Sample Rate: {rate:.1f} Hz (configured/estimated)\n"
        stats_text += f"Duration: {self.ecg_buffer.count / max(rate, 1):.1f} seconds\n\n"
        stats_text += f"Clinical Summary:\n"
        stats_text += f"- Estimated HR: {self.hr_label.cget('text')}\n"
        stats_text += f"- Rhythm: {metrics.get('rhythm_label', 'Awaiting data')}\n"
        stats_text += f"- Signal Quality: {metrics.get('signal_quality_label', 'Awaiting data')}\n"
        stats_text += f"- Displayed Window: {metrics.get('duration_sec', 0.0):.1f} seconds\n"
        stats_text += f"- Peak-to-Peak: {metrics.get('peak_to_peak', 0.0):.2f}\n\n"
        stats_text += f"Voltage Statistics:\n"
        stats_text += f"• Mean: {np.mean(data):.2f} μV\n"
        stats_text += f"• Std Dev: {np.std(data):.2f} μV\n"
        stats_text += f"• Min: {np.min(data):.2f} μV\n"
        stats_text += f"• Max: {np.max(data):.2f} μV\n"
        stats_text += f"• Peak-to-Peak: {np.max(data) - np.min(data):.2f} μV\n"
        stats_text += f"• RMS: {np.sqrt(np.mean(data**2)):.2f} μV\n"
        
        self.ecg_stats_text.insert("1.0", stats_text)
    
    def update_footer_status(self, status: str):
        """Update footer status text"""
        self.footer_acquisition_text = status
        self.render_footer_status()

    def update_footer_diagnosis(self, status: str):
        """Update footer diagnosis summary text."""
        self.footer_diagnosis_text = status
        self.render_footer_status()

    def update_footer_performance(self, status: str):
        """Update footer performance summary without overwriting acquisition state."""
        self.footer_performance_text = status
        self.render_footer_status()

    def render_footer_status(self):
        """Render the full footer string from its individual state segments."""
        hr_text = self.hr_label.cget("text") if hasattr(self, 'hr_label') else "-- BPM"
        parts = [
            f"Acquisition: {self.footer_acquisition_text}",
            f"Heart Rate: {hr_text}",
            self.footer_diagnosis_text,
        ]
        if self.footer_performance_text:
            parts.append(self.footer_performance_text)
        full_status = " | ".join(parts)
        self.footer_status.configure(text=full_status)
    
    # Utility methods for dialogs
    def show_success(self, title: str, message: str):
        """Show success message dialog"""
        messagebox.showinfo(title, message)
    
    def show_warning(self, title: str, message: str):
        """Show warning message dialog"""
        messagebox.showwarning(title, message)
    
    def show_error(self, title: str, message: str):
        """Show error message dialog"""
        messagebox.showerror(title, message)
    
    def show_info(self, title: str, message: str):
        """Show information message dialog"""
        messagebox.showinfo(title, message)
    
    def open_settings(self):
        """Open full settings dialog"""
        SettingsDialog(self)

    def _on_model_preset_changed(self, preset_name: str):
        """Populate API URL and Model ID from the selected preset."""
        self._selected_model_preset = preset_name
        preset = MODEL_PRESETS.get(preset_name, {})
        url = preset.get("api_url", "")
        mid = preset.get("model_id", "")

        self.api_url_entry.delete(0, "end")
        if url:
            self.api_url_entry.insert(0, url)

        self.model_id_entry.delete(0, "end")
        if mid:
            self.model_id_entry.insert(0, mid)

    def export_diagnosis_report(self):
        """Export the latest diagnosis report to file."""
        if not self.last_diagnosis:
            self.show_warning("Export", "No diagnosis available to export.")
            return

        fmt = getattr(self, '_export_format', 'JSON')
        ext_map = {"JSON": ".json", "CSV": ".csv", "TXT": ".txt"}
        default_ext = ext_map.get(fmt, ".json")

        export_dir = getattr(self, "_diagnosis_export_dir", str(DIAGNOSIS_DIR))
        os.makedirs(export_dir, exist_ok=True)
        filepath = filedialog.asksaveasfilename(
            defaultextension=default_ext,
            initialdir=export_dir,
            filetypes=[
                ("JSON files", "*.json"),
                ("CSV files", "*.csv"),
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
            initialfile=f"ecg_diagnosis_{datetime.now().strftime('%Y%m%d_%H%M%S')}{default_ext}",
        )
        if not filepath:
            return

        try:
            if filepath.endswith(".json"):
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(self.last_diagnosis, f, indent=2, ensure_ascii=False, default=str)
            elif filepath.endswith(".csv"):
                self._export_diagnosis_csv(filepath, [self.last_diagnosis])
            else:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(self.format_diagnosis_text(self.last_diagnosis))
            self.show_success("Export", f"Report saved to:\n{filepath}")
        except Exception as exc:
            self.show_error("Export Error", str(exc))

    def export_all_history(self):
        """Export all diagnosis history to a file."""
        if not self.diagnosis_history:
            self.show_warning("Export", "No diagnosis history to export.")
            return

        fmt = getattr(self, '_export_format', 'JSON')
        ext_map = {"JSON": ".json", "CSV": ".csv", "TXT": ".txt"}
        default_ext = ext_map.get(fmt, ".json")

        export_dir = getattr(self, "_diagnosis_export_dir", str(DIAGNOSIS_DIR))
        os.makedirs(export_dir, exist_ok=True)
        filepath = filedialog.asksaveasfilename(
            defaultextension=default_ext,
            initialdir=export_dir,
            filetypes=[
                ("JSON files", "*.json"),
                ("CSV files", "*.csv"),
                ("Text files", "*.txt"),
                ("All files", "*.*"),
            ],
            initialfile=f"ecg_history_{datetime.now().strftime('%Y%m%d_%H%M%S')}{default_ext}",
        )
        if not filepath:
            return

        try:
            if filepath.endswith(".json"):
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(self.diagnosis_history, f, indent=2, ensure_ascii=False, default=str)
            elif filepath.endswith(".csv"):
                all_diags = [e["diagnosis"] for e in self.diagnosis_history]
                self._export_diagnosis_csv(filepath, all_diags)
            else:
                with open(filepath, "w", encoding="utf-8") as f:
                    for entry in self.diagnosis_history:
                        f.write(self.format_diagnosis_text(entry["diagnosis"]))
                        f.write("\n" + "=" * 60 + "\n\n")
            self.show_success("Export", f"History ({len(self.diagnosis_history)} records) saved to:\n{filepath}")
        except Exception as exc:
            self.show_error("Export Error", str(exc))

    @staticmethod
    def _export_diagnosis_csv(filepath: str, diagnoses: List[Dict[str, Any]]):
        """Write a list of diagnosis dicts to CSV."""
        fieldnames = [
            "timestamp", "severity", "confidence", "primary_diagnosis",
            "key_findings", "immediate_actions", "follow_up", "lifestyle",
            "secondary_conditions", "risk_factors", "prognosis",
        ]
        with open(filepath, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for d in diagnoses:
                rec = d.get("recommendations", {})
                row = {
                    "timestamp": d.get("timestamp", ""),
                    "severity": d.get("severity", ""),
                    "confidence": d.get("confidence", ""),
                    "primary_diagnosis": d.get("primary_diagnosis", ""),
                    "key_findings": "; ".join(d.get("key_findings", [])),
                    "immediate_actions": "; ".join(rec.get("immediate_actions", [])),
                    "follow_up": "; ".join(rec.get("follow_up", [])),
                    "lifestyle": "; ".join(rec.get("lifestyle", [])),
                    "secondary_conditions": "; ".join(d.get("secondary_conditions", [])),
                    "risk_factors": "; ".join(d.get("risk_factors", [])),
                    "prognosis": d.get("prognosis", ""),
                }
                writer.writerow(row)
    
    def show_help(self):
        """Show help dialog"""
        help_text = """
ECG AI 智能心电诊断 — 使用说明

快速上手：
1. 在「大模型与 API」中填写 API Key，点击 Setup API
2. 选择串口并 Connect，等待实时波形
3. 确认采样率（ADS1292R 默认 500 Hz，可在 Settings 修改）
4. 采集约 10 秒后点击「分析心电图」

心率说明：
• 系统已校准采样率，修复以往 2 倍心率偏差
• 优先使用固件心率；否则用 R 峰检测（0.42s 不应期）
• 界面显示「心率来源」便于核查

更多说明见 README_CN.md
        """
        messagebox.showinfo("使用帮助", help_text)

def main():
    """Main entry point for modern ECG GUI"""
    try:
        app = ModernECGMainWindow()
        app.run()
    except Exception as e:
        messagebox.showerror("Error", f"Failed to start ECG AI application: {str(e)}")

if __name__ == "__main__":
    main()