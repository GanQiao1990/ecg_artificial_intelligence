@echo off
REM Install ECG AI to Windows Startup (开机自启)
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

set "APP_DIR=%CD%"
set "LAUNCHER=%APP_DIR%\start.bat"
set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ECG AI 心电诊断.lnk"
set "ICON=%APP_DIR%\assets\app_icon.ico"

if not exist "%LAUNCHER%" (
    echo 错误: 未找到 %LAUNCHER%
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; ^
   $s = $ws.CreateShortcut('%SHORTCUT%'); ^
   $s.TargetPath = '%LAUNCHER%'; ^
   $s.WorkingDirectory = '%APP_DIR%'; ^
   $s.WindowStyle = 7; ^
   if (Test-Path '%ICON%') { $s.IconLocation = '%ICON%,0' }; ^
   $s.Description = 'ECG AI 智能心电诊断 — 开机自启'; ^
   $s.Save()"

if exist "%SHORTCUT%" (
    echo 已安装开机自启:
    echo   %SHORTCUT%
) else (
    echo 安装失败，请以管理员身份重试或手动创建快捷方式。
    pause
    exit /b 1
)
pause