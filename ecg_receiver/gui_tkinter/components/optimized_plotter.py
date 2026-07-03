"""Medical-grade ECG strip renderer — standard paper speed, gain, and isometric grid."""

import math
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as FigureCanvas
except ImportError:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkinter as FigureCanvas

from ...core.ecg_signal import DEFAULT_SAMPLE_RATE, compute_ecg_metrics
from ..styles.colors import ERROR_RED, SUCCESS_GREEN, TEXT_GRAY, WARNING_YELLOW

_TOKEN_COLORS = {
    "green": SUCCESS_GREEN,
    "yellow": WARNING_YELLOW,
    "red": ERROR_RED,
    "gray": TEXT_GRAY,
}

# IEC / clinical paper conventions (25 mm/s, 10 mm/mV)
PAPER_SPEED_MM_S = 25.0
GAIN_MM_PER_MV = 10.0
TIME_MINOR_SEC = 0.04   # 1 mm horizontal
TIME_MAJOR_SEC = 0.20   # 5 mm horizontal
AMP_MINOR_MV = 0.10     # 1 mm vertical
AMP_MAJOR_MV = 0.50     # 5 mm vertical


class OptimizedECGPlotter:
    """Render a rolling ECG strip with medical isometric scaling."""

    def __init__(
        self,
        parent_widget,
        width=800,
        height=400,
        sample_rate=DEFAULT_SAMPLE_RATE,
        time_window_sec=10,
        paper_speed_mm_s: float = PAPER_SPEED_MM_S,
        gain_mm_per_mv: float = GAIN_MM_PER_MV,
        uv_per_count: float = 1.0,
    ):
        self.parent = parent_widget
        self.width = width
        self.height = height
        self.sample_rate = float(sample_rate)
        self.time_window_sec = float(time_window_sec)
        self.paper_speed_mm_s = float(paper_speed_mm_s)
        self.gain_mm_per_mv = float(gain_mm_per_mv)
        self.uv_per_count = float(uv_per_count)
        self.max_points = max(500, int(self.sample_rate * self.time_window_sec))

        self.paper_bg = "#fff5f7"
        self.paper_minor = "#f9c5d1"
        self.paper_major = "#e8879a"
        self.trace_color = "#1a1a2e"
        self.reference_color = "#64748b"
        self.text_color = "#1e293b"

        self._y_span_mv = 2.0
        self._y_center_mv = 0.0

        width_inches = max(1.0, width / 100.0)
        height_inches = max(1.0, height / 100.0)
        self.fig = Figure(figsize=(width_inches, height_inches), dpi=100)
        self.fig.patch.set_facecolor(self.paper_bg)

        self.ax = self.fig.add_subplot(111)
        self._configure_axes()

        self.line, = self.ax.plot([], [], color=self.trace_color, linewidth=1.2, solid_capstyle="round")
        self.baseline_line = self.ax.axhline(0.0, color=self.reference_color, linewidth=0.6, linestyle=":", alpha=0.6)
        self.metric_text = self.ax.text(
            0.02,
            0.98,
            "等待心电数据",
            transform=self.ax.transAxes,
            va="top",
            ha="left",
            fontsize=8,
            color=self.text_color,
            bbox={"boxstyle": "round,pad=0.3", "facecolor": "#fffcfd", "edgecolor": "#e2e8f0", "linewidth": 0.6},
        )
        self.scale_text = self.ax.text(
            0.98,
            0.02,
            self._scale_label(),
            transform=self.ax.transAxes,
            va="bottom",
            ha="right",
            fontsize=8,
            color=self.reference_color,
        )

        self.canvas = FigureCanvas(self.fig, parent_widget)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("resize_event", self._on_resize)

        self.last_update = 0.0
        self.update_interval = 50
        self.x_data = np.array([], dtype=np.float32)
        self.y_data_mv = np.array([], dtype=np.float32)
        self.last_metrics = self._empty_metrics()
        self._device_hr_bpm: Optional[float] = None

        self._apply_medical_limits(-self._y_span_mv, self._y_span_mv)
        self.fig.subplots_adjust(left=0.07, right=0.98, top=0.92, bottom=0.12)
        self.canvas.draw_idle()

    def _aspect_ratio(self) -> float:
        """Seconds axis width : mV axis height = paper_speed : gain (25:10 = 2.5)."""
        return self.paper_speed_mm_s / max(self.gain_mm_per_mv, 0.1)

    def _scale_label(self) -> str:
        return f"{self.paper_speed_mm_s:.0f} mm/s  ·  {self.gain_mm_per_mv:.0f} mm/mV"

    def _configure_axes(self):
        self.ax.set_facecolor(self.paper_bg)
        self.ax.set_axisbelow(True)
        self.ax.set_title("医疗级心电条带  Medical ECG Strip", fontsize=11, color=self.text_color, pad=8)
        self.ax.set_xlabel("时间 Time (s)", fontsize=9, color=self.reference_color)
        self.ax.set_ylabel("电压 Amplitude (mV)", fontsize=9, color=self.reference_color)
        self.ax.set_aspect(self._aspect_ratio(), adjustable="box")
        for spine in self.ax.spines.values():
            spine.set_color("#cbd5e1")
            spine.set_linewidth(0.8)
        self.ax.tick_params(axis="both", which="major", labelsize=8, colors=self.reference_color)
        self.ax.tick_params(axis="both", which="minor", length=2, color=self.reference_color)

    def _apply_medical_grid(self):
        self.ax.xaxis.set_major_locator(MultipleLocator(TIME_MAJOR_SEC))
        self.ax.xaxis.set_minor_locator(MultipleLocator(TIME_MINOR_SEC))
        self.ax.yaxis.set_major_locator(MultipleLocator(AMP_MAJOR_MV))
        self.ax.yaxis.set_minor_locator(MultipleLocator(AMP_MINOR_MV))
        self.ax.grid(which="major", color=self.paper_major, linewidth=0.9, alpha=0.55)
        self.ax.grid(which="minor", color=self.paper_minor, linewidth=0.45, alpha=0.85)

    def _apply_medical_limits(self, y_min_mv: float, y_max_mv: float):
        self.ax.set_xlim(-self.time_window_sec, 0.0)
        self.ax.set_ylim(y_min_mv, y_max_mv)
        self._apply_medical_grid()
        self.ax.set_aspect(self._aspect_ratio(), adjustable="box")

    def _counts_to_mv(self, data: np.ndarray) -> np.ndarray:
        """ADS1292-style counts/µV → millivolts for clinical axis."""
        uv = data.astype(np.float64) * self.uv_per_count
        return (uv / 1000.0).astype(np.float32)

    def _medical_y_limits(self, y_mv: np.ndarray) -> Tuple[float, float]:
        """Fixed gain window snapped to 0.5 mV major grid, centered on baseline."""
        if y_mv.size == 0:
            half = self._y_span_mv
            return -half, half

        center = float(np.median(y_mv))
        ptp = float(np.ptp(y_mv))
        half = max(0.5, math.ceil(max(ptp * 0.65, 0.4) / AMP_MAJOR_MV) * AMP_MAJOR_MV)
        half = min(half, 3.0)
        self._y_center_mv = center
        self._y_span_mv = half
        y_min = math.floor((center - half) / AMP_MAJOR_MV) * AMP_MAJOR_MV
        y_max = math.ceil((center + half) / AMP_MAJOR_MV) * AMP_MAJOR_MV
        if y_max - y_min < 1.0:
            y_min, y_max = center - 0.5, center + 0.5
        return y_min, y_max

    def set_paper_speed(self, mm_s: float) -> None:
        if mm_s > 0:
            self.paper_speed_mm_s = float(mm_s)
            self.scale_text.set_text(self._scale_label())
            self.render_plot()

    def set_gain(self, mm_per_mv: float) -> None:
        if mm_per_mv > 0:
            self.gain_mm_per_mv = float(mm_per_mv)
            self.scale_text.set_text(self._scale_label())
            self.render_plot()

    def set_uv_per_count(self, scale: float) -> None:
        if scale > 0:
            self.uv_per_count = float(scale)

    def _on_resize(self, _event):
        self.ax.set_aspect(self._aspect_ratio(), adjustable="box")
        self.canvas.draw_idle()

    def _empty_metrics(self) -> Dict[str, Any]:
        return {
            "heart_rate_bpm": None,
            "heart_rate_source": "unknown",
            "rhythm_label": "Awaiting data",
            "rhythm_color": TEXT_GRAY,
            "signal_quality_label": "Awaiting data",
            "signal_quality_color": TEXT_GRAY,
            "peak_to_peak": 0.0,
            "mean": 0.0,
            "std": 0.0,
            "noise_proxy": 0.0,
            "sample_count": 0,
            "duration_sec": 0.0,
            "rr_variability": None,
            "sample_rate_hz": self.sample_rate,
            "qrs_count": 0,
            "peak_to_peak_mv": 0.0,
        }

    def _map_metrics(self, raw: Dict[str, Any], y_mv: np.ndarray) -> Dict[str, Any]:
        ptp_mv = float(np.ptp(y_mv)) if y_mv.size else 0.0
        return {
            "heart_rate_bpm": raw.get("heart_rate_bpm"),
            "heart_rate_source": raw.get("heart_rate_source", "unknown"),
            "rhythm_label": raw.get("rhythm_label", "Awaiting data"),
            "rhythm_color": _TOKEN_COLORS.get(raw.get("rhythm_color_token"), TEXT_GRAY),
            "signal_quality_label": raw.get("signal_quality_label", "Awaiting data"),
            "signal_quality_color": _TOKEN_COLORS.get(raw.get("signal_quality_color_token"), TEXT_GRAY),
            "peak_to_peak": raw.get("peak_to_peak", 0.0),
            "peak_to_peak_mv": ptp_mv,
            "mean": raw.get("mean", 0.0),
            "std": raw.get("std", 0.0),
            "noise_proxy": raw.get("noise_proxy", 0.0),
            "sample_count": raw.get("sample_count", 0),
            "duration_sec": raw.get("duration_sec", 0.0),
            "rr_variability": raw.get("rr_variability"),
            "sample_rate_hz": raw.get("sample_rate_hz", self.sample_rate),
            "qrs_count": raw.get("qrs_count", 0),
            "computed_hr_bpm": raw.get("computed_hr_bpm"),
            "device_hr_bpm": raw.get("device_hr_bpm"),
        }

    def set_device_heart_rate(self, bpm: Optional[float]) -> None:
        self._device_hr_bpm = bpm

    def _compute_metrics(self, counts: np.ndarray, y_mv: np.ndarray) -> Dict[str, Any]:
        raw = compute_ecg_metrics(
            counts,
            self.sample_rate,
            self._device_hr_bpm,
            uv_per_count=self.uv_per_count,
        )
        return self._map_metrics(raw, y_mv)

    def _format_metric_text(self) -> str:
        metrics = self.last_metrics
        heart_rate = metrics.get("heart_rate_bpm")
        heart_rate_text = f"HR {heart_rate:.0f}" if heart_rate else "HR --"
        source = metrics.get("heart_rate_source", "")
        source_hint = {"device": "固件", "computed": "R峰", "fused": "融合"}.get(source, "")
        ptp = metrics.get("peak_to_peak_mv", 0.0)
        return "\n".join(filter(None, [
            heart_rate_text,
            f"节律 {metrics['rhythm_label'][:12]}",
            f"质量 {metrics['signal_quality_label'][:10]}",
            f"PP {ptp:.2f} mV" if ptp else None,
            f"Fs {metrics.get('sample_rate_hz', self.sample_rate):.0f}",
        ]))

    def update_data(self, new_data: List[float], sample_rate: Optional[float] = None):
        current_time = time.time() * 1000.0
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time
        if sample_rate is not None and sample_rate > 0:
            self.sample_rate = float(sample_rate)
        self.max_points = max(500, int(self.sample_rate * self.time_window_sec))

        counts = np.asarray(new_data, dtype=np.float32).flatten()
        if counts.size == 0:
            return

        if counts.size > self.max_points:
            counts = counts[-self.max_points:]

        duration = min(self.time_window_sec, counts.size / self.sample_rate) if self.sample_rate > 0 else 0.0
        self.x_data = (
            np.linspace(-duration, 0.0, counts.size, dtype=np.float32)
            if counts.size > 1
            else np.array([0.0], dtype=np.float32)
        )
        self.y_data_mv = self._counts_to_mv(counts)
        self.last_metrics = self._compute_metrics(counts, self.y_data_mv)
        self.render_plot()

    def render_plot(self):
        if self.y_data_mv.size == 0:
            return

        y_min, y_max = self._medical_y_limits(self.y_data_mv)
        self.line.set_data(self.x_data, self.y_data_mv)
        try:
            self.baseline_line.set_ydata([self._y_center_mv, self._y_center_mv])
        except Exception:
            pass
        self._apply_medical_limits(y_min, y_max)
        self.metric_text.set_text(self._format_metric_text())
        self.scale_text.set_text(self._scale_label())
        self.canvas.draw_idle()

    def get_metrics(self) -> Dict[str, Any]:
        return dict(self.last_metrics)

    def clear_data(self):
        self.x_data = np.array([], dtype=np.float32)
        self.y_data_mv = np.array([], dtype=np.float32)
        self.last_metrics = self._empty_metrics()
        self.line.set_data([], [])
        self._apply_medical_limits(-self._y_span_mv, self._y_span_mv)
        self.metric_text.set_text("等待心电数据")
        self.canvas.draw_idle()

    def clear_plot(self):
        self.clear_data()