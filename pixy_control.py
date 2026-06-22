#!/usr/bin/env python3

import errno
import os
import pathlib
import re
import subprocess
import time


APP_VERSION = "emeet-pixy-tk 0.1"
STATE_PATH = pathlib.Path(os.environ.get("XDG_CACHE_HOME", "~/.cache")).expanduser() / "emeet-pixy" / "state.env"


class PixyControlError(RuntimeError):
    pass


class PixyController:
    def __init__(self, video_device="/dev/video0", hidraw=None):
        self.video_device = video_device
        self.hidraw = hidraw or self.find_hidraw()
        self.state = {
            "TRACKING_STATE": "unknown",
            "GESTURE_STATE": "unknown",
            "AUDIO_STATE": "nc",
            "AUTO_MODE": "off",
        }
        self.load_state()

    def load_state(self):
        if not STATE_PATH.exists():
            return

        try:
            for line in STATE_PATH.read_text(encoding="utf-8").splitlines():
                if "=" not in line or line.strip().startswith("#"):
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")

                if key in self.state:
                    self.state[key] = value
        except OSError:
            return

    def save_state(self):
        try:
            STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            STATE_PATH.write_text(
                "".join(f"{key}={value}\n" for key, value in self.state.items()),
                encoding="utf-8",
            )
        except OSError as exc:
            raise PixyControlError(f"Failed to save state cache: {exc}") from exc

    def run_command(self, command_text):
        args = command_text.split()
        if not args:
            return ""

        command = args[0]
        rest = args[1:]

        if command == "idle":
            self.set_tracking("00")
            return "Tracking OFF (idle)"
        if command == "track":
            self.set_tracking("01")
            return "Tracking ON"
        if command == "privacy":
            self.set_tracking("02")
            return "Privacy mode ON"
        if command == "toggle-privacy":
            if self.state["TRACKING_STATE"] == "privacy":
                self.set_tracking("00")
                return "Privacy mode OFF (idle)"
            self.set_tracking("02")
            return "Privacy mode ON"

        if command == "gesture-on":
            self.set_gesture("01")
            return "Gesture control ON"
        if command == "gesture-off":
            self.set_gesture("00")
            return "Gesture control OFF"
        if command == "toggle-gesture":
            if self.state["GESTURE_STATE"] == "on":
                self.set_gesture("00")
                return "Gesture control OFF"
            self.set_gesture("01")
            return "Gesture control ON"

        if command == "audio":
            return self.run_audio(rest)
        if command == "auto-privacy":
            timeout = rest[0] if rest else "0"
            return self.set_auto_privacy(timeout)
        if command == "flicker":
            return self.set_flicker(rest)

        if command == "left":
            self.step_control("pan_absolute", -10 * 3600, -540000, 540000)
            return ""
        if command == "right":
            self.step_control("pan_absolute", 10 * 3600, -540000, 540000)
            return ""
        if command == "up":
            self.step_control("tilt_absolute", 10 * 3600, -324000, 324000)
            return ""
        if command == "down":
            self.step_control("tilt_absolute", -10 * 3600, -324000, 324000)
            return ""
        if command == "zoom-in":
            self.step_control("zoom_absolute", 10, 100, 150)
            return ""
        if command == "zoom-out":
            self.step_control("zoom_absolute", -10, 100, 150)
            return ""
        if command == "center":
            self.set_control("pan_absolute", 0)
            self.set_control("tilt_absolute", 0)
            self.set_control("zoom_absolute", 100)
            return ""

        if command == "pan":
            degrees = self.require_int(rest, "Usage: pan <degrees>")
            value = self.clamp(degrees * 3600, -540000, 540000)
            self.set_control("pan_absolute", value)
            return ""
        if command == "tilt":
            degrees = self.require_int(rest, "Usage: tilt <degrees>")
            value = self.clamp(degrees * 3600, -324000, 324000)
            self.set_control("tilt_absolute", value)
            return ""
        if command == "zoom":
            level = self.require_int(rest, "Usage: zoom <100-150>")
            value = self.clamp(level, 100, 150)
            self.set_control("zoom_absolute", value)
            return ""

        if command == "status":
            return self.status()
        if command == "sync":
            return self.sync_state()
        if command == "probe" or command == "device":
            return self.device_summary()
        if command == "version":
            return APP_VERSION

        if command == "auto":
            if not rest:
                raise PixyControlError("Usage: auto full|tracking-only|privacy-only|off")
            return self.set_auto_mode(rest[0])
        if command == "auto-on":
            return self.set_auto_mode("full")
        if command == "auto-off":
            return self.set_auto_mode("off")
        if command == "toggle-auto":
            if self.state["AUTO_MODE"] == "off":
                return self.set_auto_mode("full")
            return self.set_auto_mode("off")

        raise PixyControlError(f"Unknown command: {command_text}")

    def run_audio(self, args):
        if not args:
            try:
                self.query_audio_state()
            except PixyControlError:
                pass

            current = self.state["AUDIO_STATE"]
            if current == "nc":
                return self.set_audio_mode("02")
            if current == "live":
                return self.set_audio_mode("03")
            return self.set_audio_mode("01")

        mode = args[0]
        if mode == "nc":
            return self.set_audio_mode("01")
        if mode == "live":
            return self.set_audio_mode("02")
        if mode == "org":
            return self.set_audio_mode("03")

        raise PixyControlError("Usage: audio [nc|live|org]")

    def set_flicker(self, args):
        if not args:
            raise PixyControlError("Usage: flicker off|50|60")

        mode = args[0]
        values = {"off": 0, "50": 1, "60": 2}
        labels = {"off": "disabled", "50": "50Hz", "60": "60Hz"}

        if mode not in values:
            raise PixyControlError("Usage: flicker off|50|60")

        self.set_control("power_line_frequency", values[mode])
        return f"Anti-flicker: {labels[mode]}"

    def set_auto_privacy(self, timeout):
        if not timeout.isdigit():
            raise PixyControlError("Usage: auto-privacy <seconds>")

        value = int(timeout)
        if value < 0 or value > 255:
            raise PixyControlError("auto-privacy timeout must be between 0 and 255 seconds")

        self.send_hid_hex("09", "02", "01", "00", "00", "04", "00", "04", f"{value:02x}")
        time.sleep(0.2)
        self.send_hid_hex("09", "02", "01", "01")

        if value == 0:
            return "Auto-privacy OFF"
        return f"Auto-privacy ON (timeout: {value}s)"

    def set_auto_mode(self, mode):
        valid_modes = {"full", "tracking-only", "privacy-only", "off"}
        if mode not in valid_modes:
            raise PixyControlError("Usage: auto full|tracking-only|privacy-only|off")

        self.state["AUTO_MODE"] = mode
        self.save_state()
        return f"auto mode: {mode}"

    def status(self):
        try:
            self.query_audio_state()
        except PixyControlError:
            pass

        pan = self.get_control("pan_absolute", 0) // 3600
        tilt = self.get_control("tilt_absolute", 0) // 3600
        zoom = self.get_control("zoom_absolute", 100)

        return "\n".join([
            f"pan:  {pan} deg",
            f"tilt: {tilt} deg",
            f"zoom: {zoom}",
            f"tracking: {self.state['TRACKING_STATE']}",
            f"gesture: {self.state['GESTURE_STATE']}",
            f"audio: {self.state['AUDIO_STATE']}",
            f"auto: {self.state['AUTO_MODE']}",
        ])

    def sync_state(self):
        source = "local cache"
        try:
            self.query_audio_state()
            source = "camera query for audio; local cache for tracking/gesture/auto"
        except PixyControlError:
            pass

        self.save_state()
        return "\n".join([
            f"state source: {source}",
            f"tracking={self.state['TRACKING_STATE']}",
            f"gesture={self.state['GESTURE_STATE']}",
            f"audio={self.state['AUDIO_STATE']}",
            f"auto={self.state['AUTO_MODE']}",
        ])

    def device_summary(self):
        video = self.find_video_device()
        hidraw = self.hidraw or self.find_hidraw() or "not found"
        return f"video={video} hidraw={hidraw}"

    def set_tracking(self, mode):
        self.send_hid_hex("09", "01", "01", "00", "00", "01", "00", "01", mode)
        time.sleep(0.2)
        self.send_hid_hex("09", "01", "01", "01")

        self.state["TRACKING_STATE"] = {
            "00": "idle",
            "01": "track",
            "02": "privacy",
        }.get(mode, "unknown")
        self.save_state()

    def set_gesture(self, mode):
        self.send_hid_hex("09", "04", "02", "00", "00", "02", "00", "02", "02", mode)
        time.sleep(0.2)
        self.send_hid_hex("09", "04", "02", "01", "00", "01", "00", "01", "02")

        self.state["GESTURE_STATE"] = {
            "00": "off",
            "01": "on",
        }.get(mode, "unknown")
        self.save_state()

    def set_audio_mode(self, mode):
        self.send_hid_hex("09", "05", "00", "03", "00", "01", "00", "01", mode)
        time.sleep(0.2)

        self.state["AUDIO_STATE"] = {
            "01": "nc",
            "02": "live",
            "03": "org",
        }.get(mode, self.state["AUDIO_STATE"])

        try:
            self.query_audio_state()
        except PixyControlError:
            self.save_state()

        labels = {"01": "NC", "02": "Live", "03": "Original"}
        return f"Audio: {labels[mode]} mode"

    def query_audio_state(self):
        response = self.query_hid_hex("09", "05", "00", "04")
        if len(response) < 9:
            raise PixyControlError("Audio query returned a short response")

        mode = response[8]
        if mode == 1:
            self.state["AUDIO_STATE"] = "nc"
        elif mode == 2:
            self.state["AUDIO_STATE"] = "live"
        elif mode == 3:
            self.state["AUDIO_STATE"] = "org"
        else:
            raise PixyControlError(f"Audio query returned unknown mode byte: {mode:02x}")

        self.save_state()
        return self.state["AUDIO_STATE"]

    def send_hid_hex(self, *hex_bytes):
        hidraw = self.require_hidraw(writable=True)
        data = self.hex_bytes(hex_bytes)
        if len(data) > 32:
            raise PixyControlError("HID report is longer than 32 bytes")

        try:
            pathlib.Path(hidraw).write_bytes(data.ljust(32, b"\x00"))
        except OSError as exc:
            raise PixyControlError(f"Failed to write HID report to {hidraw}: {exc}") from exc

    def query_hid_hex(self, *hex_bytes, timeout=0.75):
        hidraw = self.require_hidraw(readable=True, writable=True)
        data = self.hex_bytes(hex_bytes)
        if len(data) > 32:
            raise PixyControlError("HID report is longer than 32 bytes")

        try:
            fd = os.open(hidraw, os.O_RDWR | os.O_NONBLOCK)
        except OSError as exc:
            raise PixyControlError(f"Failed to open {hidraw}: {exc}") from exc

        try:
            self.drain_hid(fd)
            os.write(fd, data.ljust(32, b"\x00"))

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                try:
                    response = os.read(fd, 64)
                except BlockingIOError:
                    time.sleep(0.05)
                    continue
                except OSError as exc:
                    if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                        time.sleep(0.05)
                        continue
                    raise

                if response:
                    return response
        except OSError as exc:
            raise PixyControlError(f"Failed to query {hidraw}: {exc}") from exc
        finally:
            os.close(fd)

        raise PixyControlError(f"No HID response from {hidraw}")

    def require_hidraw(self, readable=False, writable=False):
        hidraw = self.hidraw or self.find_hidraw()
        if not hidraw:
            raise PixyControlError("EMEET PIXY HID device not found")

        self.hidraw = hidraw

        if readable and not os.access(hidraw, os.R_OK):
            raise PixyControlError(f"{hidraw} is not readable")
        if writable and not os.access(hidraw, os.W_OK):
            raise PixyControlError(f"{hidraw} is not writable")

        return hidraw

    @staticmethod
    def drain_hid(fd):
        while True:
            try:
                os.read(fd, 64)
            except BlockingIOError:
                return
            except OSError as exc:
                if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                    return
                raise

    @staticmethod
    def hex_bytes(values):
        try:
            return bytes(int(value, 16) if isinstance(value, str) else int(value) for value in values)
        except ValueError as exc:
            raise PixyControlError(f"Invalid HID hex byte: {exc}") from exc

    def get_control(self, control_name, fallback=None):
        try:
            result = self.run_v4l2([
                "-d",
                self.video_device,
                "--get-ctrl",
                control_name,
            ])
            raw_value = result.stdout.strip().split(":", 1)[1].strip()
            match = re.search(r"-?\d+", raw_value)
            if not match:
                raise PixyControlError(f"Could not parse {control_name} value: {raw_value}")
            return int(match.group(0))
        except Exception:
            if fallback is not None:
                return fallback
            raise

    def set_control(self, control_name, value):
        self.prepare_manual_control(control_name)
        self.ensure_control_is_active(control_name)
        self.run_v4l2([
            "-d",
            self.video_device,
            "-c",
            f"{control_name}={int(value)}",
        ])

    def prepare_manual_control(self, control_name):
        auto_control = {
            "exposure_time_absolute": ("auto_exposure", 1),
            "white_balance_temperature": ("white_balance_automatic", 0),
            "focus_absolute": ("focus_automatic_continuous", 0),
        }.get(control_name)

        if auto_control is None:
            return

        auto_name, manual_value = auto_control
        self.run_v4l2([
            "-d",
            self.video_device,
            "-c",
            f"{auto_name}={manual_value}",
        ])
        time.sleep(0.1)

    def ensure_control_is_active(self, control_name):
        if control_name == "exposure_time_absolute" and self.get_control("auto_exposure", 3) != 1:
            raise PixyControlError(
                "Exposure Time is inactive while Exposure Auto is enabled."
            )

        if control_name == "white_balance_temperature" and self.get_control("white_balance_automatic", 1) != 0:
            raise PixyControlError(
                "White Balance Temperature is inactive while Auto White Balance is enabled."
            )

        if control_name == "focus_absolute" and self.get_control("focus_automatic_continuous", 1) != 0:
            raise PixyControlError(
                "Manual Focus is inactive while Continuous Autofocus is enabled."
            )

    def step_control(self, control_name, delta, minimum, maximum):
        current = self.get_control(control_name, 0)
        self.set_control(control_name, self.clamp(current + delta, minimum, maximum))

    @staticmethod
    def clamp(value, minimum, maximum):
        return max(minimum, min(maximum, int(value)))

    @staticmethod
    def require_int(values, usage):
        if not values:
            raise PixyControlError(usage)

        try:
            return int(values[0])
        except ValueError as exc:
            raise PixyControlError(usage) from exc

    @staticmethod
    def run_v4l2(args):
        try:
            return subprocess.run(
                ["v4l2-ctl"] + args,
                text=True,
                capture_output=True,
                check=True,
            )
        except FileNotFoundError as exc:
            raise PixyControlError("v4l2-ctl is not installed") from exc
        except subprocess.CalledProcessError as exc:
            stderr = exc.stderr.strip()
            stdout = exc.stdout.strip()
            detail = stderr or stdout or str(exc)
            raise PixyControlError(detail) from exc

    def find_video_device(self):
        if self.video_device and os.path.exists(self.video_device):
            return self.video_device

        for uevent in pathlib.Path("/sys/class/video4linux").glob("video*/device/uevent"):
            try:
                text = uevent.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue

            if (
                "product=328f/00c0" in text
                or "product=328f/c0" in text
                or ("emeet" in text and "pixy" in text)
            ):
                return f"/dev/{uevent.parent.parent.name}"

        for path in sorted(pathlib.Path("/dev").glob("video*")):
            return str(path)

        return "not found"

    @staticmethod
    def find_hidraw():
        env_hidraw = os.environ.get("PIXY_HIDRAW")
        if env_hidraw and os.path.exists(env_hidraw):
            return env_hidraw

        if os.path.exists("/dev/emeet-pixy"):
            return "/dev/emeet-pixy"

        for uevent in pathlib.Path("/sys/class/hidraw").glob("hidraw*/device/uevent"):
            try:
                text = uevent.read_text(encoding="utf-8", errors="ignore").lower()
            except OSError:
                continue

            if (
                "hid_name=" in text
                and "emeet" in text
                and "pixy" in text
                and "hid_id=" in text
                and "328f" in text
                and ("00c0" in text or "c0" in text)
            ):
                return f"/dev/{uevent.parent.parent.name}"

        return None
