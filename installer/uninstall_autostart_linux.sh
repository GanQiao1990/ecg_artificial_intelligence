#!/usr/bin/env bash
# Remove ECG AI autostart entry
set -euo pipefail

AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/ecg-ai-heart-diagnosis.desktop"

if [[ -f "$DESKTOP_FILE" ]]; then
  rm -f "$DESKTOP_FILE"
  echo "已移除自启: $DESKTOP_FILE"
else
  echo "未找到自启项: $DESKTOP_FILE"
fi