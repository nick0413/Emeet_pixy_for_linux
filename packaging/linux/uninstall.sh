#!/usr/bin/env bash
set -euo pipefail

APP_ID="emeet-pixy"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
BIN_FILE="$HOME/.local/bin/$APP_ID"
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/$APP_ID.desktop"
ICON_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps/$APP_ID.png"

rm -rf "$APP_DIR"
rm -f "$BIN_FILE" "$DESKTOP_FILE" "$ICON_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$(dirname "$DESKTOP_FILE")" >/dev/null 2>&1 || true
fi

echo "EMEET PIXY Control uninstalled."
echo "User config and cache were left intact:"
echo "  ${XDG_CONFIG_HOME:-$HOME/.config}/emeet-pixy"
echo "  ${XDG_CACHE_HOME:-$HOME/.cache}/emeet-pixy"
