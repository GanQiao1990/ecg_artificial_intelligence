# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for ECG AI Heart Diagnosis
Produces a single-folder Windows application: dist/ECG_AI_Diagnosis/
"""

import sys
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None

# Collect customtkinter assets (themes, images)
ctk_datas = collect_data_files('customtkinter')

a = Analysis(
    ['launch_modern_gui.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('.env.example', '.'),
        ('ecg_receiver', 'ecg_receiver'),
    ] + ctk_datas,
    hiddenimports=[
        # ecg_receiver package
        'ecg_receiver',
        'ecg_receiver.diagnosis',
        'ecg_receiver.core',
        'ecg_receiver.core.serial_handler',
        'ecg_receiver.core.data_recorder',
        'ecg_receiver.core.circular_buffer',
        'ecg_receiver.core.performance_monitor',
        'ecg_receiver.gui_tkinter',
        'ecg_receiver.gui_tkinter.main_window_modern',
        'ecg_receiver.gui_tkinter.components',
        'ecg_receiver.gui_tkinter.components.modern_widgets',
        'ecg_receiver.gui_tkinter.components.optimized_plotter',
        'ecg_receiver.gui_tkinter.styles',
        'ecg_receiver.gui_tkinter.styles.colors',
        # GUI / plot
        'customtkinter',
        'matplotlib',
        'matplotlib.backends.backend_tkagg',
        'PIL',
        'PIL._tkinter_finder',
        # serial / data
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        # system
        'psutil',
        'requests',
        'numpy',
        'dotenv',
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PyQt5', 'PyQt6', 'kivy', 'pytest',
        'IPython', 'jupyter', 'scipy', 'pandas',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ECG_AI_Diagnosis',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,          # No console window - pure GUI app
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,              # Add .ico path here if you have one
    version_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='ECG_AI_Diagnosis',
)
