@echo off
REM Remove ECG AI from Windows Startup
set "SHORTCUT=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\ECG AI 心电诊断.lnk"
if exist "%SHORTCUT%" (
    del /f /q "%SHORTCUT%"
    echo 已移除开机自启: %SHORTCUT%
) else (
    echo 未找到自启快捷方式。
)
pause