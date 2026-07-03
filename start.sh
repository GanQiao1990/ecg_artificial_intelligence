#!/usr/bin/env bash
# ECG AI 一键启动 — 创建虚拟环境、安装依赖、启动现代 GUI
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

echo "========================================"
echo " ECG AI 智能心电诊断 — 一键启动"
echo "========================================"

if ! command -v python3 >/dev/null 2>&1; then
  echo "错误: 未找到 python3，请先安装 Python 3.10+"
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[1/3] 创建虚拟环境 .venv ..."
  python3 -m venv .venv
else
  echo "[1/3] 虚拟环境已存在"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[2/3] 安装依赖 ..."
pip install -q -U pip
pip install -q -r requirements.txt

mkdir -p data/recordings data/diagnosis data/config

if [ ! -f ".env" ] && [ -f ".env.example" ]; then
  echo "提示: 复制 .env.example 为 .env 并填写 OPENAI_API_KEY"
fi

echo "[3/3] 启动 GUI ..."
exec python launch_modern_gui.py