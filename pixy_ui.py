#!/usr/bin/env python3

import argparse
import tkinter as tk
from tkinter import ttk, messagebox

from pixy_control import PixyControlError, PixyController


V4L2_SLIDERS = [
    # name, label, min, max, step, default
    ("pan_absolute", "Pan", -540000, 540000, 3600, 0),
    ("tilt_absolute", "Tilt", -324000, 324000, 3600, 0),
    ("zoom_absolute", "Zoom", 100, 150, 1, 100),

    ("brightness", "Brightness", 0, 255, 1, 128),
    ("contrast", "Contrast", 0, 255, 1, 128),
    ("saturation", "Saturation", 0, 255, 1, 128),
    ("hue", "Hue", 0, 255, 1, 128),
    ("gamma", "Gamma", 0, 255, 1, 128),
    ("gain", "Gain", 0, 100, 1, 0),
    ("sharpness", "Sharpness", 0, 255, 1, 128),
    ("backlight_compensation", "Backlight Compensation", 1, 2, 1, 1),

    # These are inactive unless their auto modes are disabled
    ("white_balance_temperature", "White Balance Temperature", 2300, 7500, 1, 5000),
    ("exposure_time_absolute", "Exposure Time", 1, 5000, 1, 300),
    ("focus_absolute", "Manual Focus", 0, 1023, 1, 192),
]

BOOL_CONTROLS = [
    ("white_balance_automatic", "Auto White Balance"),
    ("focus_automatic_continuous", "Continuous Autofocus"),
]

HID_ACTIONS = [
    # Tracking / privacy
    ("Tracking ON", "track"),
    ("Tracking OFF / Idle", "idle"),
    ("Privacy Mode ON", "privacy"),
    ("Toggle Privacy", "toggle-privacy"),

    # Gesture control
    ("Gesture ON", "gesture-on"),
    ("Gesture OFF", "gesture-off"),
    ("Toggle Gesture", "toggle-gesture"),

    # Audio modes
    ("Audio: Noise Cancel", "audio nc"),
    ("Audio: Live", "audio live"),
    ("Audio: Original", "audio org"),
    ("Audio: Cycle", "audio"),

    # Auto privacy timeout
    ("Auto Privacy OFF", "auto-privacy 0"),
    ("Auto Privacy 10s", "auto-privacy 10"),
    ("Auto Privacy 60s", "auto-privacy 60"),

    # Anti-flicker / power-line frequency
    ("Flicker OFF", "flicker off"),
    ("Flicker 50 Hz", "flicker 50"),
    ("Flicker 60 Hz", "flicker 60"),

    # Step movement
    ("Step Left", "left"),
    ("Step Right", "right"),
    ("Step Up", "up"),
    ("Step Down", "down"),
    ("Zoom In Step", "zoom-in"),
    ("Zoom Out Step", "zoom-out"),

    # Misc
    ("Center / Home", "center"),
    ("Status", "status"),
    ("Sync State", "sync"),
    ("Probe Devices", "probe"),
    ("Show Device", "device"),
    ("Version", "version"),
]

AUTOMATION_ACTIONS = [
    ("Auto: Full", "auto full"),
    ("Auto: Tracking Only", "auto tracking-only"),
    ("Auto: Privacy Only", "auto privacy-only"),
    ("Auto: Off", "auto off"),
    ("Auto ON", "auto-on"),
    ("Auto OFF", "auto-off"),
    ("Toggle Auto", "toggle-auto"),
]


class ScrollableFrame(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)

        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.scrollable = ttk.Frame(self.canvas)

        self.window_id = self.canvas.create_window(
            (0, 0),
            window=self.scrollable,
            anchor="nw",
        )

        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollable.bind("<Configure>", self._update_scroll_region)
        self.canvas.bind("<Configure>", self._update_canvas_width)

        self.canvas.bind("<Enter>", self._bind_mousewheel)
        self.canvas.bind("<Leave>", self._unbind_mousewheel)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

    def _update_scroll_region(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _update_canvas_width(self, event):
        self.canvas.itemconfigure(self.window_id, width=event.width)

    def _bind_mousewheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind_all("<Button-4>", self._on_mousewheel)
        self.canvas.bind_all("<Button-5>", self._on_mousewheel)

    def _unbind_mousewheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_mousewheel(self, event):
        if event.num == 4:
            self.canvas.yview_scroll(-3, "units")
        elif event.num == 5:
            self.canvas.yview_scroll(3, "units")
        else:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class PixyUI:
    def __init__(self, root, device):
        self.root = root
        self.device = device
        self.controller = PixyController(video_device=device)
        self.pending_jobs = {}
        self.slider_vars = {}
        self.slider_widgets = {}
        self.bool_vars = {}
        self.custom_hid_command = tk.StringVar(value="status")

        root.title("EMEET PIXY Linux Control Panel")
        root.geometry("900x720")
        root.minsize(760, 560)

        self.build_ui()

    def v4l2_get(self, control_name, fallback):
        return self.controller.get_control(control_name, fallback)

    def v4l2_set(self, control_name, value):
        try:
            self.controller.set_control(control_name, value)
            self.refresh_manual_control_states()
        except PixyControlError as e:
            messagebox.showerror(
                "V4L2 error",
                f"Failed to set {control_name}={value}\n\n{e}",
            )

    def schedule_v4l2_set(self, control_name, value):
        if control_name in self.pending_jobs:
            self.root.after_cancel(self.pending_jobs[control_name])

        self.pending_jobs[control_name] = self.root.after(
            120,
            lambda: self.v4l2_set(control_name, value),
        )

    def set_many(self, values):
        for name, value in values.items():
            self.v4l2_set(name, value)
            if name in self.slider_vars:
                self.slider_vars[name].set(value)

    def run_pixy_action(self, action):
        action = action.strip()
        if not action:
            return

        try:
            output = self.controller.run_command(action).strip()
            if action == "status" or output:
                messagebox.showinfo("PIXY output", output or "Command completed.")
        except PixyControlError as e:
            messagebox.showerror(
                "PIXY control error",
                f"Command failed:\n\n{action}\n\n{e}",
            )

    def build_ui(self):
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill="both", expand=True)

        status = ttk.LabelFrame(outer, text="Detected devices", padding=10)
        status.pack(fill="x", pady=(0, 10))

        ttk.Label(status, text=f"V4L2 video device: {self.device}").pack(anchor="w")
        ttk.Label(status, text=f"PIXY HID device: {self.controller.hidraw or 'not found'}").pack(anchor="w")

        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)

        v4l2_tab = ttk.Frame(notebook)
        hid_tab = ttk.Frame(notebook)

        notebook.add(v4l2_tab, text="V4L2 / PTZ Controls")
        notebook.add(hid_tab, text="PIXY HID Commands")

        self.build_v4l2_tab(v4l2_tab)
        self.build_hid_tab(hid_tab)

    def build_v4l2_tab(self, parent):
        scroll = ScrollableFrame(parent)
        scroll.pack(fill="both", expand=True)

        page = scroll.scrollable

        presets = ttk.LabelFrame(page, text="PTZ presets", padding=10)
        presets.pack(fill="x", pady=(0, 10), padx=4)

        ttk.Button(
            presets,
            text="Center",
            command=lambda: self.set_many({
                "pan_absolute": 0,
                "tilt_absolute": 0,
            }),
        ).pack(side="left", padx=4)

        ttk.Button(
            presets,
            text="Home / Reset PTZ",
            command=lambda: self.set_many({
                "pan_absolute": 0,
                "tilt_absolute": 0,
                "zoom_absolute": 100,
            }),
        ).pack(side="left", padx=4)

        ttk.Button(
            presets,
            text="Zoom Min",
            command=lambda: self.set_many({"zoom_absolute": 100}),
        ).pack(side="left", padx=4)

        ttk.Button(
            presets,
            text="Zoom Max",
            command=lambda: self.set_many({"zoom_absolute": 150}),
        ).pack(side="left", padx=4)

        mode_box = ttk.LabelFrame(page, text="Mode toggles", padding=10)
        mode_box.pack(fill="x", pady=(0, 10), padx=4)

        for name, label in BOOL_CONTROLS:
            value = self.v4l2_get(name, 1)
            var = tk.IntVar(value=value)
            self.bool_vars[name] = var

            cb = ttk.Checkbutton(
                mode_box,
                text=label,
                variable=var,
                command=lambda n=name, v=var: self.v4l2_set(n, v.get()),
            )
            cb.pack(side="left", padx=8)

        ttk.Button(
            mode_box,
            text="Exposure Auto",
            command=lambda: self.v4l2_set("auto_exposure", 3),
        ).pack(side="left", padx=8)

        ttk.Button(
            mode_box,
            text="Exposure Manual",
            command=lambda: self.v4l2_set("auto_exposure", 1),
        ).pack(side="left", padx=8)

        sliders = ttk.LabelFrame(page, text="V4L2 sliders", padding=10)
        sliders.pack(fill="both", expand=True, pady=(0, 10), padx=4)

        for row, (name, label, minv, maxv, step, default) in enumerate(V4L2_SLIDERS):
            value = self.v4l2_get(name, default)
            var = tk.IntVar(value=value)
            self.slider_vars[name] = var

            ttk.Label(sliders, text=label, width=28).grid(
                row=row,
                column=0,
                sticky="w",
                padx=4,
                pady=3,
            )

            scale = tk.Scale(
                sliders,
                from_=minv,
                to=maxv,
                resolution=step,
                orient="horizontal",
                length=440,
                variable=var,
                showvalue=True,
                command=lambda raw, n=name: self.schedule_v4l2_set(n, int(float(raw))),
            )
            scale.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
            self.slider_widgets[name] = scale

            ttk.Button(
                sliders,
                text="Reset",
                command=lambda n=name, d=default: self.set_many({n: d}),
            ).grid(row=row, column=2, sticky="e", padx=4, pady=3)

        sliders.columnconfigure(1, weight=1)
        self.refresh_manual_control_states()

    def refresh_manual_control_states(self):
        exposure_auto = self.v4l2_get("auto_exposure", 3)
        auto_white_balance = self.bool_vars.get("white_balance_automatic")
        auto_focus = self.bool_vars.get("focus_automatic_continuous")

        states = {
            "exposure_time_absolute": "normal" if exposure_auto == 1 else "disabled",
            "white_balance_temperature": (
                "normal" if auto_white_balance is not None and auto_white_balance.get() == 0 else "disabled"
            ),
            "focus_absolute": (
                "normal" if auto_focus is not None and auto_focus.get() == 0 else "disabled"
            ),
        }

        for name, state in states.items():
            if name in self.slider_widgets:
                self.slider_widgets[name].configure(state=state)

    def build_hid_tab(self, parent):
        page = ttk.Frame(parent, padding=10)
        page.pack(fill="both", expand=True)

        info = ttk.LabelFrame(page, text="Notes", padding=10)
        info.pack(fill="x", pady=(0, 10))

        ttk.Label(
            info,
            text=(
                "These buttons use native Python control. They control PIXY-specific HID features "
                "such as tracking, privacy, gestures, audio modes, auto-privacy, flicker, "
                "device discovery, and local automation helpers."
            ),
            wraplength=820,
        ).pack(anchor="w")

        buttons = ttk.LabelFrame(page, text="PIXY control buttons", padding=10)
        buttons.pack(fill="x", pady=(0, 10))

        columns = 3
        for i, (label, action) in enumerate(HID_ACTIONS):
            ttk.Button(
                buttons,
                text=label,
                command=lambda a=action: self.run_pixy_action(a),
            ).grid(row=i // columns, column=i % columns, sticky="ew", padx=4, pady=4)

        for col in range(columns):
            buttons.columnconfigure(col, weight=1)

        automation = ttk.LabelFrame(page, text="Automation", padding=10)
        automation.pack(fill="x", pady=(0, 10))

        for i, (label, action) in enumerate(AUTOMATION_ACTIONS):
            ttk.Button(
                automation,
                text=label,
                command=lambda a=action: self.run_pixy_action(a),
            ).grid(row=i // columns, column=i % columns, sticky="ew", padx=4, pady=4)

        for col in range(columns):
            automation.columnconfigure(col, weight=1)

        custom = ttk.LabelFrame(page, text="Custom PIXY command", padding=10)
        custom.pack(fill="x", pady=(0, 10))

        ttk.Label(custom, text="Command arguments:").grid(
            row=0,
            column=0,
            sticky="w",
            padx=4,
            pady=4,
        )

        entry = ttk.Entry(custom, textvariable=self.custom_hid_command)
        entry.grid(row=0, column=1, sticky="ew", padx=4, pady=4)
        entry.bind("<Return>", lambda _event: self.run_pixy_action(self.custom_hid_command.get()))

        ttk.Button(
            custom,
            text="Run",
            command=lambda: self.run_pixy_action(self.custom_hid_command.get()),
        ).grid(row=0, column=2, sticky="ew", padx=4, pady=4)

        custom.columnconfigure(1, weight=1)

        examples = ttk.LabelFrame(page, text="Examples", padding=10)
        examples.pack(fill="both", expand=True)

        example_text = (
            "status\n"
            "track\n"
            "idle\n"
            "privacy\n"
            "toggle-privacy\n"
            "gesture-on\n"
            "gesture-off\n"
            "toggle-gesture\n"
            "audio nc\n"
            "audio live\n"
            "audio org\n"
            "audio\n"
            "auto-privacy 0\n"
            "auto-privacy 10\n"
            "auto-privacy 60\n"
            "flicker off\n"
            "flicker 50\n"
            "flicker 60\n"
            "left\n"
            "right\n"
            "up\n"
            "down\n"
            "zoom-in\n"
            "zoom-out\n"
            "center\n"
            "sync\n"
            "probe\n"
            "device\n"
            "version\n"
            "auto full\n"
            "auto tracking-only\n"
            "auto privacy-only\n"
            "auto off\n"
            "auto-on\n"
            "auto-off\n"
            "toggle-auto\n"
            "pan -30\n"
            "tilt 15\n"
            "zoom 125"
        )

        text = tk.Text(examples, height=12, wrap="none")
        text.insert("1.0", example_text)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)


def main():
    parser = argparse.ArgumentParser(description="EMEET PIXY Linux control UI")
    parser.add_argument(
        "--device",
        default="/dev/video0",
        help="V4L2 video device, default: /dev/video0",
    )
    args = parser.parse_args()

    root = tk.Tk()
    PixyUI(root, args.device)
    root.mainloop()


if __name__ == "__main__":
    main()
