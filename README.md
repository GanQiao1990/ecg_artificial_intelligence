# ECG Receiver Standalone

Real-time ECG visualization and AI-assisted diagnosis for ESP32 + ADS1292R devices.

This project is built for first-time users who want to connect an ECG source, see a live strip, and review a structured diagnosis summary without reading the code first.

## Repository

Source code: [GitHub repository](https://github.com/GanQiao1990/ecg_receiver_standalone-)

## What This App Does

- Shows a live ECG strip with a clinical-style grid and a rolling 10-second window.
- Displays heart rate, rhythm regularity, signal quality, and trace range.
- Sends ECG segments to an AI diagnosis service for structured findings and recommendations.
- Saves ECG data to CSV for later review.

## Which Interface Should I Use?

If you are new to the project, start with the modern Tkinter diagnosis GUI.

- Modern Tkinter diagnosis GUI: best for doctor-facing review and the clearest ECG summary.
- Legacy PyQt GUI: kept for compatibility, but not recommended for new users.

## Quick Start

### 1. Create a Python Environment

Use any Python 3.8+ environment. A virtual environment is the simplest option.

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows, use the equivalent PowerShell or Command Prompt activation command.

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

If you prefer, the GUI launchers can also install missing GUI packages automatically.

### 3. Launch the GUI

Modern diagnosis GUI:

```bash
python launch_modern_gui.py
```

Legacy PyQt GUI:

```bash
python -m ecg_receiver.main
```

Important:

- Run commands from the repository root where `launch_modern_gui.py` is located.
- If your default `python` points to a different environment (for example `D:\MGLTOOL\python.exe`), run with your intended Python executable explicitly.

Windows example:

```powershell
& "D:\Users\dell\anaconda3\python.exe" launch_modern_gui.py
```

## First-Time User Workflow

1. Start the GUI.
2. Select the serial port for your ECG device.
3. Click Connect and wait for the live strip to appear.
4. Enter your API key and API URL if you want AI diagnosis.
5. Click Analyze ECG after a few seconds of data have arrived.
6. Use Auto Mode if you want the app to analyze ECG automatically every 30 seconds.

You can use the visualization without an API key. AI diagnosis requires a valid API key and network access.

## What the Doctor Sees

The modern diagnosis GUI is organized to make review faster:

- A live 10-second ECG strip with an ECG-paper style grid.
- Heart rate, rhythm label, signal quality, and trace range beside the monitor.
- A structured diagnosis panel with severity, confidence, key findings, immediate actions, follow-up, and clinical notes.
- A diagnosis history tab for comparing prior results.

## Hardware Requirements

- ESP32 board.
- ADS1292R ECG front end or compatible ECG source.
- USB cable.
- ECG firmware that streams serial data to the computer.

Default serial settings in the app are tuned for common ECG demo firmware:

- Baud rate: 57600
- Data source: serial line stream

## Supported Data Formats

The serial reader accepts two common formats:

### Standard CSV Format

```text
DATA,timestamp,ecg_value,resp_value,heart_rate,status
```

Example:

```text
DATA,1234567890,1024,512,75,OK
```

### Simple Numeric Format

One ECG sample per line:

```text
-7
-6
-5
1024
1050
```

If your firmware uses another format, adjust the serial parser in the core code.

## AI Diagnosis Setup

1. Open the modern diagnosis GUI.
2. Enter your API key.
3. Check the API URL shown in the app and update it if your provider uses a different endpoint.
4. Click Setup API.
5. Wait for the connection status to turn ready before running analysis.

## Recording Data

Use Start Recording to save incoming ECG data to a CSV file. This is useful when you want to review traces later or share them with another clinician.

## Troubleshooting

### No serial port appears

- Make sure the ESP32 is plugged in.
- Check that the USB cable supports data, not only charging.
- Install the correct USB driver for your board.
- On Linux, add your user to the dialout group and log out/in again.

### ECG data does not appear

- Confirm the device is sending serial data at the expected baud rate.
- Check that the firmware matches one of the supported formats above.
- Reconnect the device and wait a few seconds for the buffer to fill.

### AI diagnosis fails

- Verify the API key.
- Check the API URL.
- Confirm that the machine has internet access.
- Wait until the app has enough ECG data before running analysis.

### The modern GUI cannot start

- Reinstall dependencies with `pip install -r requirements.txt`.
- If Tkinter support is missing on your Linux system, install your distribution’s Tk packages.
- Confirm you are in the project root before launching.
- Confirm `python` points to a Python 3 environment with the project dependencies installed.

### Error: can't open file 'launch_kivy_gui.py'

This repository currently does not include `launch_kivy_gui.py`. Use:

```bash
python launch_modern_gui.py
```

Or provide an explicit Python path on Windows:

```powershell
& "D:\Users\dell\anaconda3\python.exe" launch_modern_gui.py
```

## Testing

Useful commands for checking the project:

```bash
python -m py_compile launch_modern_gui.py
python -m py_compile ecg_receiver/gui_tkinter/main_window_modern.py
python -m py_compile ecg_receiver/gui_tkinter/components/optimized_plotter.py
python -m py_compile ecg_receiver/core/llm_diagnosis.py
```

## Project Layout

- `launch_modern_gui.py`: modern doctor-facing Tkinter GUI launcher.
- `ecg_receiver/core`: serial handling, buffers, recording, and performance monitoring.
- `ecg_receiver/gui_tkinter`: modern diagnosis-focused desktop interface.
- `ecg_receiver/gui`: legacy PyQt interface.

## Safety Note

This software is a visualization and decision-support tool. It does not replace clinical judgment or emergency medical care.

## Recommended Next Step

Start with the modern Tkinter diagnosis GUI, connect your device, and verify that the live strip and signal metrics look stable before relying on the AI summary.