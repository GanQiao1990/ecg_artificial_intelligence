#!/usr/bin/env python3
"""
ECG AI Heart Diagnosis - Entry Point
"""

import os
import sys


def _bootstrap():
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    try:
        from ecg_receiver.core.app_config import ensure_data_dirs, load_env

        ensure_data_dirs()
        load_env()
    except Exception:
        pass
    return project_root


def _check_display():
    if sys.platform == "win32":
        return True
    if os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        return True
    return False


def main():
    """Launch the ECG AI Heart Diagnosis application."""
    project_root = _bootstrap()

    if not _check_display():
        print(
            "无法启动图形界面：未检测到 DISPLAY / WAYLAND_DISPLAY。\n"
            "请在有桌面的本机运行，或通过 SSH 开启 X11 转发：ssh -X user@host\n"
            "Cannot start GUI: no display. Run on a machine with a desktop session.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        from ecg_receiver.gui_tkinter.main_window_modern import ModernECGMainWindow

        app = ModernECGMainWindow()
        app.run()
    except Exception as e:
        err = str(e)
        print(f"ECG AI 启动失败 / Startup error: {err}", file=sys.stderr)
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("ECG AI - Startup Error", err)
        except Exception:
            pass
        sys.exit(1)


if __name__ == "__main__":
    main()