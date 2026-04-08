@echo off
REM ECG AI Heart Diagnosis - Windows EXE Builder
REM Output: dist\ECG_AI_Diagnosis\ECG_AI_Diagnosis.exe

setlocal
set PYTHON=D:\Users\dell\anaconda3\python.exe
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
"%PYTHON%" -m pip install -q pyinstaller customtkinter matplotlib Pillow psutil pyserial requests numpy python-dotenv
if errorlevel 1 (
    echo ERROR: pip install failed
    pause & exit /b 1
)
echo       Dependencies OK
echo.

REM --- Clean previous build ---
echo [3/4] Cleaning old build...
if exist "build"  rmdir /s /q "build"
if exist "dist"   rmdir /s /q "dist"
echo       Clean OK
echo.

REM --- Build ---
echo [4/4] Running PyInstaller...
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
