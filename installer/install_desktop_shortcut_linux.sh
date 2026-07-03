#!/usr/bin/env bash
# Install ECG AI application menu shortcut (Linux)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPS_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
DESKTOP_FILE="$APPS_DIR/ecg-ai-heart-diagnosis.desktop"
ICON_PATH="$ROOT/assets/app_icon.png"
EXEC_PATH="$ROOT/start.sh"

mkdir -p "$APPS_DIR"
chmod +x "$EXEC_PATH" 2>/dev/null || true

ICON_LINE="Icon=utilities-system-monitor"
if [[ -f "$ICON_PATH" ]]; then
  ICON_LINE="Icon=$ICON_PATH"
fi

cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Type=Application
Name=ECG AI 智能心电诊断
Comment=ADS1292R clinical ECG monitoring with AI diagnosis
Exec=$EXEC_PATH
Path=$ROOT
Terminal=false
Categories=Medical;Science;Utility;
StartupNotify=true
$ICON_LINE
EOF

chmod 644 "$DESKTOP_FILE"
echo "已安装桌面快捷方式: $DESKTOP_FILE"