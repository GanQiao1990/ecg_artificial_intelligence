@echo off
REM ECG AI 一键启动 — Windows：虚拟环境 + 依赖 + GUI
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ========================================
echo  ECG AI 智能心电诊断 — 一键启动
echo ========================================

where python >nul 2>&1
if errorlevel 1 (
    echo 错误: 未找到 python，请安装 Python 3.10+ 并加入 PATH
    pause
    exit /b 1
)

if not exist ".venv\Scripts\activate.bat" (
    echo [1/3] 创建虚拟环境 .venv ...
    python -m venv .venv
    if errorlevel 1 (
        echo 创建虚拟环境失败
        pause
        exit /b 1
    )
) else (
    echo [1/3] 虚拟环境已存在
)

call .venv\Scripts\activate.bat

echo [2/3] 安装依赖 ...
python -m pip install -q -U pip
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo 依赖安装失败
    pause
    exit /b 1
)

if not exist "data\recordings" mkdir data\recordings
if not exist "data\diagnosis" mkdir data\diagnosis
if not exist "data\config" mkdir data\config

if not exist ".env" if exist ".env.example" (
    echo 提示: 复制 .env.example 为 .env 并填写 OPENAI_API_KEY
)

echo [3/3] 启动 GUI ...
python launch_modern_gui.py
pause