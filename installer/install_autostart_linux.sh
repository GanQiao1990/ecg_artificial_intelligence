#!/usr/bin/env bash
# Install ECG AI desktop autostart entry (Linux 开机/登录自启)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AUTOSTART_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
DESKTOP_FILE="$AUTOSTART_DIR/ecg-ai-heart-diagnosis.desktop"
ICON_PATH="$ROOT/assets/app_icon.png"
EXEC_PATH="$ROOT/start.sh"

mkdir -p "$AUTOSTART_DIR"

if [[ ! -x "$EXEC_PATH" ]]; then
  chmod +x "$EXEC_PATH"
fi

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
Categories=Medical;Science;
StartupNotify=true
X-GNOME-Autostart-enabled=true
$ICON_LINE
EOF

chmod 644 "$DESKTOP_FILE"
echo "已安装自启: $DESKTOP_FILE"