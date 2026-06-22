# EMEET PIXY Control Release Notes

## Build A Linux Release

Create a downloadable release archive:

```bash
./scripts/build_release.sh 0.1.0
```

Upload the generated archive to a GitHub Release:

```text
dist/emeet-pixy-0.1.0-linux.tar.gz
```

## User Install Steps

```bash
tar -xzf emeet-pixy-0.1.0-linux.tar.gz
cd emeet-pixy-0.1.0-linux
./install.sh
```

Run from the app launcher or terminal:

```bash
emeet-pixy
```

## Runtime Dependencies

Fedora:

```bash
sudo dnf install python3-tkinter v4l-utils
```

Debian/Ubuntu:

```bash
sudo apt install python3-tk v4l-utils
```

HID controls require user access to the PIXY hidraw device, ideally through a udev rule that creates `/dev/emeet-pixy`.
