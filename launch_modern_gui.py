#!/usr/bin/env python3
"""
ECG AI Heart Diagnosis - Entry Point
"""

import sys
import os

def main():
    """Launch the ECG AI Heart Diagnosis application"""
    # Ensure project root is on path
    project_root = os.path.dirname(os.path.abspath(__file__))
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    try:
        from ecg_receiver.gui_tkinter.main_window_modern import ModernECGMainWindow
        app = ModernECGMainWindow()
        app.run()
    except Exception as e:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("ECG AI - Startup Error", str(e))
        sys.exit(1)

if __name__ == "__main__":
    main()
