@echo off
REM ECG AI Heart Diagnosis - Windows EXE Builder
REM Output: dist\ECG_AI_Diagnosis\ECG_AI_Diagnosis.exe

setlocal
REM Use current Python if PYTHON not set (portable across machines)
if not defined PYTHON set PYTHON=python
set SPEC=ecg_ai.spec

echo ========================================
echo  ECG AI Heart Diagnosis - Build EXE
echo ========================================
echo.

REM --- Check Python ---
"%PYTHON%" --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found at %PYTHON%
    echo Edit PYTHON variable at top of this file.
    pause & exit /b 1
)
echo [1/4] Python OK
echo.

REM --- Install / upgrade build deps ---
echo [2/4] Installing dependencies...
"%PYTHON%" -m pip install -q -r requirements.txt pyinstaller
if errorlevel 1 (
    echo ERROR: pip install failed
    pause & exit /b 1
)
echo       Dependencies OK
echo.

REM --- App icon ---
if not exist "assets\app_icon.ico" (
    echo [3/5] Generating application icon...
    "%PYTHON%" scripts\generate_app_icon.py
) else (
    echo [3/5] Application icon OK
)
echo.

REM --- Clean previous build ---
echo [4/5] Cleaning old build...
if exist "build"  rmdir /s /q "build"
if exist "dist"   rmdir /s /q "dist"
echo       Clean OK
echo.

REM --- Build ---
echo [5/5] Running PyInstaller...
"%PYTHON%" -m PyInstaller %SPEC% --clean --noconfirm
if errorlevel 1 (
    echo.
    echo ERROR: PyInstaller build failed. See output above.
    pause & exit /b 1
)

echo.
echo ========================================
if exist "dist\ECG_AI_Diagnosis\ECG_AI_Diagnosis.exe" (
    echo  SUCCESS!
    echo  Executable: dist\ECG_AI_Diagnosis\ECG_AI_Diagnosis.exe
    echo.
    echo  To distribute: copy the entire dist\ECG_AI_Diagnosis\ folder.
    echo  Double-click ECG_AI_Diagnosis.exe to run.
) else (
    echo  Build finished but exe not found - check errors above.
)
echo ========================================
echo.
pause
