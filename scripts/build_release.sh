#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:-0.1.0}"
APP_ID="emeet-pixy"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$DIST_DIR/$APP_ID-$VERSION-linux"
ARCHIVE="$DIST_DIR/$APP_ID-$VERSION-linux.tar.gz"

cd "$ROOT_DIR"

python3 - <<'PY'
import ast
for path in ("pixy_ui.py", "pixy_control.py"):
    ast.parse(open(path, encoding="utf-8").read(), filename=path)
    print(f"{path}: syntax ok")
PY

rm -rf "$BUILD_DIR" "$ARCHIVE"
mkdir -p "$BUILD_DIR"

install -m 755 pixy_ui.py "$BUILD_DIR/pixy_ui.py"
install -m 644 pixy_control.py "$BUILD_DIR/pixy_control.py"
install -m 755 packaging/linux/install.sh "$BUILD_DIR/install.sh"
install -m 755 packaging/linux/uninstall.sh "$BUILD_DIR/uninstall.sh"
install -m 644 packaging/linux/emeet-pixy.desktop.in "$BUILD_DIR/emeet-pixy.desktop.in"

if [[ -f design_logo.png ]]; then
    install -m 644 design_logo.png "$BUILD_DIR/design_logo.png"
fi

cat > "$BUILD_DIR/README.txt" <<EOF
EMEET PIXY Control $VERSION

Install:
  ./install.sh

Run:
  emeet-pixy

Dependencies:
  Fedora: sudo dnf install python3-tkinter v4l-utils
  Debian/Ubuntu: sudo apt install python3-tk v4l-utils

Notes:
  HID controls require access to the PIXY hidraw device. If unavailable,
  configure a udev rule that creates /dev/emeet-pixy and grants your user access.
EOF

tar -C "$DIST_DIR" -czf "$ARCHIVE" "$APP_ID-$VERSION-linux"

echo "Release archive created:"
echo "  $ARCHIVE"
