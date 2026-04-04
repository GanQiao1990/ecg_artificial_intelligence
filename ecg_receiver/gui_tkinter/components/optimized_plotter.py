"""Diagnostic ECG plotting tuned for clinician-readable rolling strips."""

import math
import time
from typing import Any, Dict, List

import numpy as np
from matplotlib.figure import Figure
from matplotlib.ticker import MultipleLocator

try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg as FigureCanvas
except ImportError:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkinter as FigureCanvas

from ..styles.colors import ERROR_RED, SUCCESS_GREEN, TEXT_GRAY, WARNING_YELLOW


class OptimizedECGPlotter:
    """Render a stable rolling ECG strip with lightweight signal metrics."""

    def __init__(self, parent_widget, width=800, height=400, sample_rate=250, time_window_sec=10):
        self.parent = parent_widget
        self.width = width
        self.height = height
        self.sample_rate = float(sample_rate)
        self.time_window_sec = float(time_window_sec)
        self.max_points = max(500, int(self.sample_rate * self.time_window_sec))

        self.paper_bg = "#fff8f5"
        self.paper_minor = "#f6c9d3"
        self.paper_major = "#e88aa0"
        self.trace_color = "#0f766e"
        self.reference_color = "#64748b"
        self.text_color = "#334155"

        width_inches = max(1.0, width / 100.0)
        height_inches = max(1.0, height / 100.0)
        self.fig = Figure(figsize=(width_inches, height_inches), dpi=100)
        self.fig.patch.set_facecolor(self.paper_bg)

        self.ax = self.fig.add_subplot(111)
        self._configure_axes()

        self.line, = self.ax.plot([], [], color=self.trace_color, linewidth=1.8, solid_capstyle="round")
        self.ax.axhline(0.0, color=self.reference_color, linewidth=0.8, linestyle="--", alpha=0.75)
        self.metric_text = self.ax.text(
            0.02,
            0.98,
            "Awaiting live ECG data",
            transform=self.ax.transAxes,
            va="top",
            ha="left",
            fontsize=9,
            color=self.text_color,
            bbox={"boxstyle": "round,pad=0.35", "facecolor": "#fffdfc", "edgecolor": "#e2e8f0", "linewidth": 0.8},
        )

        self.canvas = FigureCanvas(self.fig, parent_widget)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        self.last_update = 0.0
        self.update_interval = 50
        self.x_data = np.array([], dtype=np.float32)
        self.y_data = np.array([], dtype=np.float32)
        self.last_metrics = self._empty_metrics()

        self._update_y_grid(-200.0, 200.0)
        self.fig.tight_layout(pad=1.0)
        self.canvas.draw_idle()

    def _configure_axes(self):
        self.ax.set_facecolor(self.paper_bg)
        self.ax.set_axisbelow(True)
        self.ax.set_title("Diagnostic ECG Strip", fontsize=12, color=self.text_color, pad=10)
        self.ax.set_xlabel("Seconds before current sample", fontsize=10, color=self.reference_color)
        self.ax.set_ylabel("Signal", fontsize=10, color=self.reference_color)
        self.ax.set_xlim(-self.time_window_sec, 0.0)
        self.ax.set_ylim(-200.0, 200.0)
        self.ax.xaxis.set_major_locator(MultipleLocator(1.0))
        self.ax.xaxis.set_minor_locator(MultipleLocator(0.2))
        self.ax.grid(which="major", axis="x", color=self.paper_major, linewidth=0.8, alpha=0.55)
        self.ax.grid(which="minor", axis="x", color=self.paper_minor, linewidth=0.45, alpha=0.85)

        for spine in self.ax.spines.values():
            spine.set_color("#cbd5e1")
            spine.set_linewidth(0.8)

        self.ax.tick_params(axis="both", which="major", labelsize=9, colors=self.reference_color)
        self.ax.tick_params(axis="both", which="minor", length=0)

    def _empty_metrics(self) -> Dict[str, Any]:
        return {
            "heart_rate_bpm": None,
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
        }

    def _nice_step(self, value: float) -> float:
        value = max(value, 1.0)
        exponent = math.floor(math.log10(value))
        fraction = value / (10 ** exponent)
        if fraction <= 1:
            nice_fraction = 1
        elif fraction <= 2:
            nice_fraction = 2
        elif fraction <= 5:
            nice_fraction = 5
        else:
            nice_fraction = 10
        return nice_fraction * (10 ** exponent)

    def _update_y_grid(self, y_min: float, y_max: float):
        major_step = self._nice_step((y_max - y_min) / 6.0)
        minor_step = max(major_step / 5.0, 1.0)
        y_start = math.floor(y_min / major_step) * major_step
        y_end = math.ceil(y_max / major_step) * major_step
        self.ax.set_yticks(np.arange(y_start, y_end + major_step, major_step))
        self.ax.yaxis.set_minor_locator(MultipleLocator(minor_step))
        self.ax.grid(which="major", axis="y", color=self.paper_major, linewidth=0.8, alpha=0.55)
        self.ax.grid(which="minor", axis="y", color=self.paper_minor, linewidth=0.45, alpha=0.85)

    def _detect_peaks(self, data: np.ndarray) -> List[int]:
        if data.size < max(3, int(self.sample_rate)):
            return []

        threshold = float(np.mean(data) + 0.6 * np.std(data))
        min_distance = max(1, int(self.sample_rate * 0.25))
        peaks: List[int] = []

        for idx in range(1, data.size - 1):
            if data[idx] >= threshold and data[idx] >= data[idx - 1] and data[idx] > data[idx + 1]:
                if not peaks or idx - peaks[-1] >= min_distance:
                    peaks.append(idx)

        return peaks

    def _compute_metrics(self, data: np.ndarray) -> Dict[str, Any]:
        peak_to_peak = float(np.ptp(data)) if data.size else 0.0
        mean = float(np.mean(data)) if data.size else 0.0
        std = float(np.std(data)) if data.size else 0.0
        noise_proxy = float(np.std(np.diff(data))) if data.size > 1 else 0.0
        duration_sec = float(data.size / self.sample_rate) if self.sample_rate > 0 else 0.0

        peaks = self._detect_peaks(data)
        heart_rate = None
        rr_variability = None
        rhythm_label = "Insufficient data"
        rhythm_color = TEXT_GRAY

        if len(peaks) > 1:
            rr_intervals = np.diff(peaks) / self.sample_rate
            avg_rr = float(np.mean(rr_intervals)) if rr_intervals.size else 0.0
            if avg_rr > 0:
                heart_rate = 60.0 / avg_rr
                rr_variability = float(np.std(rr_intervals) / avg_rr) if rr_intervals.size > 1 else 0.0

            if rr_variability is None:
                rhythm_label = "Undetermined"
                rhythm_color = TEXT_GRAY
            elif rr_variability < 0.08:
                rhythm_label = "Regular"
                rhythm_color = SUCCESS_GREEN
            elif rr_variability < 0.16:
                rhythm_label = "Mild irregularity"
                rhythm_color = WARNING_YELLOW
            else:
                rhythm_label = "Irregular"
                rhythm_color = ERROR_RED

        drift_window = max(1, data.size // 5)
        baseline_drift = abs(float(np.median(data[:drift_window]) - np.median(data[-drift_window:]))) if data.size else 0.0
        snr_proxy = peak_to_peak / max(noise_proxy, 1.0)

        if peak_to_peak < 5:
            quality_label = "No signal"
            quality_color = TEXT_GRAY
        elif snr_proxy >= 10 and baseline_drift <= peak_to_peak * 0.25:
            quality_label = "Excellent"
            quality_color = SUCCESS_GREEN
        elif snr_proxy >= 6 and baseline_drift <= peak_to_peak * 0.4:
            quality_label = "Good"
            quality_color = SUCCESS_GREEN
        elif snr_proxy >= 3:
            quality_label = "Fair"
            quality_color = WARNING_YELLOW
        else:
            quality_label = "Poor"
            quality_color = ERROR_RED

        return {
            "heart_rate_bpm": heart_rate,
            "rhythm_label": rhythm_label,
            "rhythm_color": rhythm_color,
            "signal_quality_label": quality_label,
            "signal_quality_color": quality_color,
            "peak_to_peak": peak_to_peak,
            "mean": mean,
            "std": std,
            "noise_proxy": noise_proxy,
            "sample_count": int(data.size),
            "duration_sec": duration_sec,
            "rr_variability": rr_variability,
        }

    def _compute_display_limits(self, data: np.ndarray) -> List[float]:
        if data.size == 0:
            return [-200.0, 200.0]

        lower = float(np.percentile(data, 2))
        upper = float(np.percentile(data, 98))
        center = (lower + upper) / 2.0
        half_range = max((upper - lower) * 0.7, float(np.ptp(data)) * 0.35, 50.0)

        y_min = center - half_range
        y_max = center + half_range
        major_step = self._nice_step((y_max - y_min) / 6.0)

        return [
            math.floor(y_min / major_step) * major_step,
            math.ceil(y_max / major_step) * major_step,
        ]

    def _format_metric_text(self) -> str:
        metrics = self.last_metrics
        heart_rate = metrics.get("heart_rate_bpm")
        heart_rate_text = f"HR {heart_rate:.0f} BPM" if heart_rate else "HR --"
        return "\n".join([
            heart_rate_text,
            f"Rhythm {metrics['rhythm_label']}",
            f"Quality {metrics['signal_quality_label']}",
            f"P-P {metrics['peak_to_peak']:.0f}",
            f"Window {metrics['duration_sec']:.1f}s",
        ])

    def update_data(self, new_data: List[float], sample_rate: int = 250):
        """Render the current ECG snapshot as a rolling strip."""
        current_time = time.time() * 1000.0
        if current_time - self.last_update < self.update_interval:
            return

        self.last_update = current_time
        self.sample_rate = float(sample_rate)
        self.max_points = max(500, int(self.sample_rate * self.time_window_sec))

        data = np.asarray(new_data, dtype=np.float32).flatten()
        if data.size == 0:
            return

        if data.size > self.max_points:
            data = data[-self.max_points:]

        duration = min(self.time_window_sec, data.size / self.sample_rate) if self.sample_rate > 0 else 0.0
        self.x_data = np.linspace(-duration, 0.0, data.size, dtype=np.float32) if data.size > 1 else np.array([0.0], dtype=np.float32)
        self.y_data = data
        self.last_metrics = self._compute_metrics(self.y_data)
        self.render_plot()

    def render_plot(self):
        if self.y_data.size == 0:
            return

        y_min, y_max = self._compute_display_limits(self.y_data)
        self.line.set_data(self.x_data, self.y_data)
        self.ax.set_xlim(-self.time_window_sec, 0.0)
        self.ax.set_ylim(y_min, y_max)
        self._update_y_grid(y_min, y_max)
        self.metric_text.set_text(self._format_metric_text())
        self.canvas.draw_idle()

    def get_metrics(self) -> Dict[str, Any]:
        return dict(self.last_metrics)

    def clear_data(self):
        self.x_data = np.array([], dtype=np.float32)
        self.y_data = np.array([], dtype=np.float32)
        self.last_metrics = self._empty_metrics()
        self.line.set_data([], [])
        self.ax.set_xlim(-self.time_window_sec, 0.0)
        self.ax.set_ylim(-200.0, 200.0)
        self._update_y_grid(-200.0, 200.0)
        self.metric_text.set_text("Awaiting live ECG data")
        self.canvas.draw_idle()

    def clear_plot(self):
        """Backward-compatible alias used by older callers."""
        self.clear_data()
