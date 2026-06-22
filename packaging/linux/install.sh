#!/usr/bin/env bash
set -euo pipefail

APP_ID="emeet-pixy"
APP_NAME="EMEET PIXY Control"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_ID"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICON_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/256x256/apps"
ICON_PATH="$ICON_DIR/$APP_ID.png"
DESKTOP_FILE="$DESKTOP_DIR/$APP_ID.desktop"
BIN_FILE="$BIN_DIR/$APP_ID"

need_file() {
    if [[ ! -f "$SOURCE_DIR/$1" ]]; then
        echo "Missing $1 in $SOURCE_DIR" >&2
        exit 1
    fi
}

need_file pixy_ui.py
need_file pixy_control.py

mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

install -m 755 "$SOURCE_DIR/pixy_ui.py" "$APP_DIR/pixy_ui.py"
install -m 644 "$SOURCE_DIR/pixy_control.py" "$APP_DIR/pixy_control.py"

if [[ -f "$SOURCE_DIR/design_logo.png" ]]; then
    install -m 644 "$SOURCE_DIR/design_logo.png" "$APP_DIR/design_logo.png"
    install -m 644 "$SOURCE_DIR/design_logo.png" "$ICON_PATH"
else
    ICON_PATH="$APP_DIR/design_logo.png"
fi

cat > "$BIN_FILE" <<EOF
#!/usr/bin/env bash
exec python3 "$APP_DIR/pixy_ui.py" "\$@"
EOF
chmod 755 "$BIN_FILE"

sed \
    -e "s|@APP_DIR@|$APP_DIR|g" \
    -e "s|@ICON_PATH@|$ICON_PATH|g" \
    "$SOURCE_DIR/emeet-pixy.desktop.in" > "$DESKTOP_FILE"
chmod 644 "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
fi

echo "$APP_NAME installed."
echo "Launcher: $BIN_FILE"
echo "Desktop entry: $DESKTOP_FILE"
echo
echo "Runtime dependencies:"
echo "  Fedora: sudo dnf install python3-tkinter v4l-utils"
echo "  Debian/Ubuntu: sudo apt install python3-tk v4l-utils"
echo
echo "If the app cannot access HID controls, confirm your /dev/emeet-pixy udev rule and permissions."
