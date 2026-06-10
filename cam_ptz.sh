#!/usr/bin/env bash
# PTZ and mode control for eMeet Pixy webcam (USB 328f:00c0)
# Uses v4l2-ctl for PTZ and HID for tracking/idle mode
# Usage: cam_ptz.sh <command> [value]
#
# --- How the HID protocol was reverse-engineered ---
#
# The eMeet Pixy exposes two control interfaces:
#   1. UVC (standard) — pan/tilt/zoom/focus via v4l2-ctl
#   2. HID (proprietary) — tracking, audio, gesture, privacy via /dev/hidrawN
#
# The HID protocol was decoded by capturing USB traffic with usbmon + tshark
# while toggling features in EMEET Studio (Windows VM with VirtualBox USB
# passthrough). Captures filtered for device HID interrupt transfers:
#   tshark -r capture.pcap \
#     -Y "usb.device_address == N && usb.transfer_type == 0x01 && usb.data_len == 32" \
#     -T fields -e frame.time_relative -e usb.endpoint_address -e usbhid.data
#
# HID reports are 32 bytes, report ID 0x09. Structure:
#   Byte 0:    0x09 (report ID)
#   Byte 1:    Command group
#   Bytes 2+:  Subcommand, parameters, value
#
# Decoded commands (OUT = host-to-device on endpoint 0x01):
#
#   Tracking mode (group 0x01):
#     SET: 09 01 01 00 00 01 00 01 XX    XX: 00=off, 01=track, 02=privacy
#     ACK: 09 01 01 01
#
#   Auto-privacy (group 0x02):
#     SET: 09 02 01 00 00 04 00 04 XX    XX: timeout in seconds (00=disable)
#     ACK: 09 02 01 01
#
#   Gesture control (group 0x04, subgroup 0x02):
#     SET: 09 04 02 00 00 02 00 02 02 XX    XX: 00=off, 01=on
#     ACK: 09 04 02 01 00 01 00 01 02
#
#   Audio mode (group 0x05):
#     SET: 09 05 00 03 00 01 00 01 XX    XX: 01=NC, 02=live, 03=original
#     QRY: 09 05 00 04
#
# Anti-flicker uses standard UVC control (power_line_frequency) via v4l2-ctl.
#
# --- To decode additional commands ---
#
# 1. VirtualBox VM with Windows 10 LTSC + EMEET Studio is configured in:
#      modules/programs/virtualbox.nix  (VBox + Extension Pack for USB passthrough)
#      modules/core/users.nix           (vboxusers group)
#    Disable 3D acceleration in VM display settings (EMEET Studio renders transparent otherwise).
#
# 2. Pass the camera to the VM: VBox menu > Devices > USB > EMEET EMEET PIXY
#
# 3. Capture USB traffic with scripts/usb_capture.sh:
#      ./scripts/usb_capture.sh capture    # guided session, records per-feature pcaps
#      ./scripts/usb_capture.sh analyze    # extract eMeet HID commands from pcaps
#
# 4. Compare OUT commands (endpoint 0x01) between feature-on and feature-off captures
#    to identify which bytes change. The value byte is typically at offset 8 or 9.

set -euo pipefail

APP_VERSION="emeet-pixy-tk 0.1"
DEVICE="${PIXY_VIDEO:-/dev/video0}"
HIDRAW="${PIXY_HIDRAW:-/dev/emeet-pixy}"
V4L2_CTL="${V4L2_CTL:-v4l2-ctl}"
STEP_DEG=10
STEP=$((STEP_DEG * 3600))
STATE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/emeet-pixy"
STATE_FILE="$STATE_DIR/state.env"

TRACKING_STATE="unknown"
GESTURE_STATE="unknown"
AUDIO_STATE="nc"
AUTO_MODE="off"

get() { $V4L2_CTL -d "$DEVICE" --get-ctrl="$1" | cut -d' ' -f2; }
set_ctrl() { $V4L2_CTL -d "$DEVICE" --set-ctrl="$1=$2"; }

clamp() {
    local val=$1 min=$2 max=$3
    (( val < min )) && val=$min
    (( val > max )) && val=$max
    echo "$val"
}

load_state() {
    if [[ -r "$STATE_FILE" ]]; then
        # shellcheck disable=SC1090
        source "$STATE_FILE"
    fi
}

save_state() {
    mkdir -p "$STATE_DIR"
    {
        printf 'TRACKING_STATE=%q\n' "$TRACKING_STATE"
        printf 'GESTURE_STATE=%q\n' "$GESTURE_STATE"
        printf 'AUDIO_STATE=%q\n' "$AUDIO_STATE"
        printf 'AUTO_MODE=%q\n' "$AUTO_MODE"
    } > "$STATE_FILE"
}

find_hidraw() {
    if [[ -n "${PIXY_HIDRAW:-}" && -e "$PIXY_HIDRAW" ]]; then
        echo "$PIXY_HIDRAW"
        return 0
    fi

    if [[ -e /dev/emeet-pixy ]]; then
        echo /dev/emeet-pixy
        return 0
    fi

    for uevent in /sys/class/hidraw/hidraw*/device/uevent; do
        [[ -r "$uevent" ]] || continue

        local text lower hidraw_name
        text=$(<"$uevent")
        lower=${text,,}

        if [[ "$lower" == *"hid_name="*"emeet"*"pixy"* ]] &&
           { [[ "$lower" == *"hid_id="*"328f"*"00c0"* ]] || [[ "$lower" == *"hid_id="*"328f"*"c0"* ]]; }; then
            hidraw_name=$(basename "$(dirname "$(dirname "$uevent")")")
            echo "/dev/$hidraw_name"
            return 0
        fi
    done

    echo "Error: eMeet Pixy HID device not found" >&2
    return 1
}

find_video_device() {
    if [[ -e "$DEVICE" ]]; then
        echo "$DEVICE"
        return 0
    fi

    for uevent in /sys/class/video4linux/video*/device/uevent; do
        [[ -r "$uevent" ]] || continue

        local text lower video_name
        text=$(<"$uevent")
        lower=${text,,}
        video_name=$(basename "$(dirname "$(dirname "$uevent")")")

        if [[ "$lower" == *"product=328f/00c0"* ]] ||
           [[ "$lower" == *"product=328f/c0"* ]] ||
           [[ "$lower" == *"emeet"*"pixy"* ]]; then
            echo "/dev/$video_name"
            return 0
        fi
    done

    for dev in /dev/video*; do
        [[ -e "$dev" ]] || continue
        echo "$dev"
        return 0
    done

    echo "not found"
    return 1
}

send_hid_hex() {
    local hidraw
    hidraw=$(find_hidraw)

    if [[ ! -w "$hidraw" ]]; then
        echo "Error: $hidraw is not writable" >&2
        echo "Hint: check the udev rule or run: ls -l $hidraw" >&2
        return 1
    fi

    python3 - "$hidraw" "$@" <<'PY'
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
try:
    data = bytes(int(arg, 16) for arg in sys.argv[2:])
except ValueError as exc:
    raise SystemExit(f"Invalid HID hex byte: {exc}")

if len(data) > 32:
    raise SystemExit("HID report is longer than 32 bytes")

path.write_bytes(data.ljust(32, b"\x00"))
PY
}

set_tracking() {
    local mode=$1
    send_hid_hex 09 01 01 00 00 01 00 01 "$mode"
    sleep 0.2
    send_hid_hex 09 01 01 01

    case "$mode" in
        00) TRACKING_STATE="idle" ;;
        01) TRACKING_STATE="track" ;;
        02) TRACKING_STATE="privacy" ;;
    esac
    save_state
}

set_gesture() {
    local mode=$1
    send_hid_hex 09 04 02 00 00 02 00 02 02 "$mode"
    sleep 0.2
    send_hid_hex 09 04 02 01 00 01 00 01 02

    case "$mode" in
        00) GESTURE_STATE="off" ;;
        01) GESTURE_STATE="on" ;;
    esac
    save_state
}

set_audio_mode() {
    local mode=$1
    send_hid_hex 09 05 00 03 00 01 00 01 "$mode"
    sleep 0.2
    send_hid_hex 09 05 00 04

    case "$mode" in
        01) AUDIO_STATE="nc" ;;
        02) AUDIO_STATE="live" ;;
        03) AUDIO_STATE="org" ;;
    esac
    save_state
}

set_auto_mode() {
    AUTO_MODE=$1
    save_state
    echo "auto mode: $AUTO_MODE"
}

print_device() {
    local video hidraw
    video=$(find_video_device || true)
    hidraw=$(find_hidraw || true)
    echo "video=$video hidraw=$hidraw"
}

sync_state() {
    save_state
    echo "state source: local cache"
    echo "tracking=$TRACKING_STATE"
    echo "gesture=$GESTURE_STATE"
    echo "audio=$AUDIO_STATE"
    echo "auto=$AUTO_MODE"
}

load_state

case "${1:-help}" in
    idle)
        set_tracking 00
        echo "Tracking OFF (idle)"
        ;;
    track)
        set_tracking 01
        echo "Tracking ON"
        ;;
    privacy)
        set_tracking 02
        echo "Privacy mode ON"
        ;;
    toggle-privacy)
        if [[ "$TRACKING_STATE" == "privacy" ]]; then
            set_tracking 00
            echo "Privacy mode OFF (idle)"
        else
            set_tracking 02
            echo "Privacy mode ON"
        fi
        ;;
    gesture-on)
        set_gesture 01
        echo "Gesture control ON"
        ;;
    gesture-off)
        set_gesture 00
        echo "Gesture control OFF"
        ;;
    toggle-gesture)
        if [[ "$GESTURE_STATE" == "on" ]]; then
            set_gesture 00
            echo "Gesture control OFF"
        else
            set_gesture 01
            echo "Gesture control ON"
        fi
        ;;
    audio)
        if [[ $# -lt 2 ]]; then
            case "$AUDIO_STATE" in
                nc) set_audio_mode 02; echo "Audio: Live mode" ;;
                live) set_audio_mode 03; echo "Audio: Original mode" ;;
                org|*) set_audio_mode 01; echo "Audio: NC mode" ;;
            esac
            exit 0
        fi

        case "$2" in
            nc)   set_audio_mode 01; echo "Audio: NC mode" ;;
            live) set_audio_mode 02; echo "Audio: Live mode" ;;
            org)  set_audio_mode 03; echo "Audio: Original mode" ;;
            *)    echo "Usage: cam_ptz.sh audio nc|live|org"; exit 1 ;;
        esac
        ;;
    flicker)
        case "${2:?Usage: cam_ptz.sh flicker off|50|60}" in
            off) set_ctrl power_line_frequency 0; echo "Anti-flicker: disabled" ;;
            50)  set_ctrl power_line_frequency 1; echo "Anti-flicker: 50Hz" ;;
            60)  set_ctrl power_line_frequency 2; echo "Anti-flicker: 60Hz" ;;
            *)   echo "Usage: cam_ptz.sh flicker off|50|60"; exit 1 ;;
        esac
        ;;
    auto-privacy)
        timeout=${2:-0a}
        # Convert decimal to hex if numeric
        if [[ "$timeout" =~ ^[0-9]+$ ]]; then
            timeout=$(printf '%02x' "$timeout")
        fi
        send_hid_hex 09 02 01 00 00 04 00 04 "$timeout"
        sleep 0.2
        send_hid_hex 09 02 01 01
        if [ "$timeout" = "00" ]; then
            echo "Auto-privacy OFF"
        else
            echo "Auto-privacy ON (timeout: ${2:-10}s)"
        fi
        ;;
    left)
        cur=$(get pan_absolute)
        set_ctrl pan_absolute "$(clamp $((cur - STEP)) -540000 540000)"
        ;;
    right)
        cur=$(get pan_absolute)
        set_ctrl pan_absolute "$(clamp $((cur + STEP)) -540000 540000)"
        ;;
    up)
        cur=$(get tilt_absolute)
        set_ctrl tilt_absolute "$(clamp $((cur + STEP)) -324000 324000)"
        ;;
    down)
        cur=$(get tilt_absolute)
        set_ctrl tilt_absolute "$(clamp $((cur - STEP)) -324000 324000)"
        ;;
    zoom-in)
        cur=$(get zoom_absolute)
        set_ctrl zoom_absolute "$(clamp $((cur + 10)) 100 150)"
        ;;
    zoom-out)
        cur=$(get zoom_absolute)
        set_ctrl zoom_absolute "$(clamp $((cur - 10)) 100 150)"
        ;;
    center)
        set_ctrl pan_absolute 0
        set_ctrl tilt_absolute 0
        set_ctrl zoom_absolute 100
        ;;
    pan)
        deg=${2:?Usage: cam_ptz.sh pan <degrees>}
        set_ctrl pan_absolute "$(clamp $((deg * 3600)) -540000 540000)"
        ;;
    tilt)
        deg=${2:?Usage: cam_ptz.sh tilt <degrees>}
        set_ctrl tilt_absolute "$(clamp $((deg * 3600)) -324000 324000)"
        ;;
    zoom)
        lvl=${2:?Usage: cam_ptz.sh zoom <100-150>}
        set_ctrl zoom_absolute "$(clamp "$lvl" 100 150)"
        ;;
    status)
        echo "pan:  $(($(get pan_absolute) / 3600)) deg"
        echo "tilt: $(($(get tilt_absolute) / 3600)) deg"
        echo "zoom: $(get zoom_absolute)"
        echo "tracking: $TRACKING_STATE"
        echo "gesture: $GESTURE_STATE"
        echo "audio: $AUDIO_STATE"
        echo "auto: $AUTO_MODE"
        ;;
    sync)
        sync_state
        ;;
    probe)
        print_device
        ;;
    device)
        print_device
        ;;
    version)
        echo "$APP_VERSION"
        ;;
    auto)
        case "${2:?Usage: cam_ptz.sh auto full|tracking-only|privacy-only|off}" in
            full) set_auto_mode full ;;
            tracking-only) set_auto_mode tracking-only ;;
            privacy-only) set_auto_mode privacy-only ;;
            off) set_auto_mode off ;;
            *) echo "Usage: cam_ptz.sh auto full|tracking-only|privacy-only|off"; exit 1 ;;
        esac
        ;;
    auto-on)
        set_auto_mode full
        ;;
    auto-off)
        set_auto_mode off
        ;;
    toggle-auto)
        if [[ "$AUTO_MODE" == "off" ]]; then
            set_auto_mode full
        else
            set_auto_mode off
        fi
        ;;
    help|*)
        cat <<'EOF'
cam_ptz.sh - eMeet Pixy PTZ control
Movement (10 deg steps):
  left, right, up, down
Zoom (10-unit steps):
  zoom-in, zoom-out
Absolute:
  pan <degrees>      -150 to 150
  tilt <degrees>     -90 to 90
  zoom <level>       100 to 150
Tracking:
  idle               Disable auto-tracking
  track              Enable auto-tracking
  privacy            Enable privacy mode
  toggle-privacy     Toggle privacy mode using local state
  gesture-on         Enable gesture control
  gesture-off        Disable gesture control
  toggle-gesture     Toggle gesture control using local state
  audio [nc|live|org] Set or cycle audio mode
  auto-privacy <sec> Enable auto-privacy with timeout (0 to disable)
  flicker off|50|60  Anti-flicker frequency
Automation:
  auto full|tracking-only|privacy-only|off
  auto-on, auto-off, toggle-auto
Other:
  center             Reset to home position
  status             Show current position and cached state
  sync               Refresh local state cache output
  probe              Detect video and HID devices
  device             Show current video and HID devices
  version            Show script/app version
EOF
        ;;
esac
