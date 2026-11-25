import threading
import asyncio
import ctypes
import logging
import queue
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from tkinter import ttk, messagebox
import sqlite3
import os
import time

from buttplug import DeviceOutputCommand, OutputType

import TarkBuzz as tarkbuzz

log = logging.getLogger("tarkbuzz")

TEST_VIBRATION_SECONDS = 3

RAID_OUTCOME_LABELS = {
    "kia": "killed in action",
    "survived": "survived",
    "run_through": "run through",
}


# Default vibration levels (hardcoded)
DEFAULT_LEVELS = {
    "green_limb": 0.0,
    "yellow_limb": 0.25,
    "red_limb": 0.75,
    "black_limb": 1.0,
}
COLOR_KEYS = list(DEFAULT_LEVELS)
COLOR_LABELS = ["Green Limb", "Yellow Limb", "Red Limb", "Black Limb"]

# Database helper functions
DB_PATH = os.path.expanduser("~/.buzz_levels.db")

DARK_THEME = {
    "window": "#15181e",
    "panel": "#1e232b",
    "input": "#11151b",
    "text": "#e7eaf0",
    "muted": "#aab3c2",
    "accent": "#5b9cff",
    "button": "#2a313c",
    "button_active": "#374252",
    "border": "#343c48",
    "selection": "#365a8a",
}


def init_db():
    """Initialize database if it doesn't exist"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS levels
                 (color_name TEXT PRIMARY KEY, level REAL)''')
    conn.commit()
    conn.close()

def load_levels():
    """Load saved levels from database"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    levels = {}
    c.execute("SELECT color_name, level FROM levels")
    for row in c.fetchall():
        levels[row[0]] = row[1]
    conn.close()
    return levels

def save_levels_to_db(levels_dict):
    """Save levels to database"""
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for key, val in levels_dict.items():
        c.execute("INSERT OR REPLACE INTO levels (color_name, level) VALUES (?, ?)", (key, val))
    conn.commit()
    conn.close()


class TarkBuzzGUI:
    def __init__(self, root):
        self.root = root
        root.title("TarkBuzz UI")
        self.configure_dark_theme()

        # Buttplug client and device list
        self.client = None
        self.devices = []
        self.polling = False
        self.monitoring = False
        self.raid_started = False
        self.raid_end_outcome = None
        self.raid_vibration_until = None
        self.raid_vibration_enabled = {}
        self.detection_overlay = None
        self._last_inventory_status = None

        # Tkinter is not thread-safe: the asyncio thread queues UI work here
        # and the Tk main thread runs it (see call_in_ui / _drain_ui_queue).
        self.ui_queue = queue.Queue()
        # Screen detection blocks (dxcam grabs, sleeps between limb samples),
        # so it runs on one worker thread, which also serialises access to
        # TarkBuzz's detection state.
        self.detection_executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="detection",
        )
        # Plain-Python copies of the checkboxes for the asyncio thread to read.
        self.limb_enabled_state = {}
        self.raid_vibration_state = {}
        self.dynamic_buzz_state = False
        self.pause_in_inventory_state = True
        # Connection bookkeeping (touched only on the asyncio thread,
        # except `connecting`, which the Connect button sets first).
        self.connecting = False
        self.monitor_generation = 0
        self.monitor_task = None
        self.testing_vibration = False
        self._sent_levels = {}

        # Create a persistent event loop for async operations
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.run_event_loop, daemon=True)
        self.loop_thread.start()

        top = ttk.Frame(root, padding=10)
        top.grid(row=0, column=0, sticky="nsew")

        ttk.Label(top, text="Buttplug Server URL:").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value="ws://localhost:12345")
        ttk.Entry(top, textvariable=self.url_var, width=30).grid(row=0, column=1, sticky="w")
        ttk.Button(top, text="Connect", command=self.connect_client).grid(row=0, column=2, padx=6)
        ttk.Button(top, text="Disconnect", command=self.disconnect).grid(row=0, column=3)

        ttk.Label(top, text="Devices:").grid(row=1, column=0, sticky="nw", pady=(8,0))
        self.devices_list = tk.Listbox(top, height=6, width=40)
        self.devices_list.configure(
            background=DARK_THEME["input"],
            foreground=DARK_THEME["text"],
            selectbackground=DARK_THEME["selection"],
            selectforeground=DARK_THEME["text"],
            highlightbackground=DARK_THEME["border"],
            highlightcolor=DARK_THEME["accent"],
            relief=tk.FLAT,
        )
        self.devices_list.grid(row=1, column=1, columnspan=3, sticky="w", pady=(8,0))

        # Sliders for color vibration levels
        sliders = ttk.LabelFrame(root, text="Color Vibration Levels", padding=10)
        sliders.grid(row=2, column=0, sticky="nsew", padx=10, pady=8)

        self.level_vars = {}

        # Load saved levels from database (ignores keys no longer used)
        saved_levels = load_levels()
        initial_values = {
            key: saved_levels.get(key, DEFAULT_LEVELS[key]) for key in COLOR_KEYS
        }
        tarkbuzz.set_colour_levels(initial_values)

        for i, (key, label) in enumerate(zip(COLOR_KEYS, COLOR_LABELS)):
            initial_val = initial_values[key]
            ttk.Label(sliders, text=label).grid(row=i, column=0, sticky="w")
            v = tk.DoubleVar(value=initial_val)
            s = ttk.Scale(sliders, from_=0.0, to=1.0, orient=tk.HORIZONTAL, variable=v)
            s.grid(row=i, column=1, sticky="we", padx=8)
            val_label = ttk.Label(sliders, text=f"{initial_val:.2f}", width=5, anchor="e")
            val_label.grid(row=i, column=2, sticky="e")

            def make_trace(color_key, var, lab):
                def on_change(*_):
                    value = float(var.get())
                    lab.config(text=f"{value:.2f}")
                    # Apply immediately; "Save Levels" only persists to disk.
                    tarkbuzz.set_colour_levels({color_key: value})
                return on_change

            v.trace_add("write", make_trace(key, v, val_label))
            self.level_vars[key] = v

        # Limb toggle section
        limb_frame = ttk.LabelFrame(root, text="Limb Toggle", padding=10)
        limb_frame.grid(row=2, column=1, sticky="nsew", padx=10, pady=8)
        
        self.limb_enabled = {}
        self.limb_status_vars = {}
        self.limb_status_labels = {}
        limb_keys = ["head", "thorax", "stomach", "right_arm", "left_arm", "right_leg", "left_leg"]
        limb_labels = ["Head", "Thorax", "Stomach", "Right Arm", "Left Arm", "Right Leg", "Left Leg"]
        ttk.Label(
            limb_frame,
            text="Track",
            style="LimbStatusHeader.TLabel",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            limb_frame,
            text="Detected",
            style="LimbStatusHeader.TLabel",
        ).grid(row=0, column=1, sticky="e")

        for i, (key, label) in enumerate(zip(limb_keys, limb_labels), start=1):
            toggle_var = tk.BooleanVar(value=True)
            toggle_check = ttk.Checkbutton(limb_frame, text=label, variable=toggle_var)
            toggle_check.grid(row=i, column=0, sticky="w", pady=4)
            self.limb_enabled[key] = toggle_var
            self._mirror_bool(
                toggle_var,
                lambda on, limb=key: self.limb_enabled_state.__setitem__(limb, on),
            )
            status_var = tk.StringVar(value="Waiting")
            status_label = ttk.Label(
                limb_frame,
                textvariable=status_var,
                style="LimbStatus.Unknown.TLabel",
                width=15,
                anchor="e",
            )
            status_label.grid(row=i, column=1, sticky="e", padx=(12, 0), pady=4)
            self.limb_status_vars[key] = status_var
            self.limb_status_labels[key] = status_label

        # Dynamic buzzing toggle
        self.dynamic_buzz_enabled = tk.BooleanVar(value=False)
        self._mirror_bool(
            self.dynamic_buzz_enabled,
            lambda on: setattr(self, "dynamic_buzz_state", on),
        )
        dynamic_buzz_check = ttk.Checkbutton(
            limb_frame, 
            text="Dynamic Buzzing", 
            variable=self.dynamic_buzz_enabled
        )
        dynamic_buzz_check.grid(row=len(limb_keys) + 1, column=0, sticky="w", pady=(8, 0))

        self.pause_in_inventory = tk.BooleanVar(value=True)
        self._mirror_bool(
            self.pause_in_inventory,
            lambda on: setattr(self, "pause_in_inventory_state", on),
        )
        ttk.Checkbutton(
            limb_frame,
            text="Pause While In Inventory",
            variable=self.pause_in_inventory,
        ).grid(row=len(limb_keys) + 2, column=0, columnspan=2, sticky="w")

        outcome_frame = ttk.LabelFrame(
            root,
            text="Raid Outcome Vibration (60 seconds at maximum)",
            padding=10,
        )
        outcome_frame.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky="ew",
            padx=10,
            pady=8,
        )
        outcome_options = (
            ("kia", "Death / Killed in Action", False),
            ("survived", "Survived", True),
            ("run_through", "Run Through", False),
        )
        for column, (outcome, label, enabled_by_default) in enumerate(outcome_options):
            enabled = tk.BooleanVar(value=enabled_by_default)
            ttk.Checkbutton(
                outcome_frame,
                text=label,
                variable=enabled,
            ).grid(row=0, column=column, sticky="w", padx=8)
            self.raid_vibration_enabled[outcome] = enabled
            self._mirror_bool(
                enabled,
                lambda on, o=outcome: self.raid_vibration_state.__setitem__(o, on),
            )

        # Buttons for save and reset
        button_frame = ttk.Frame(root, padding=10)
        button_frame.grid(row=4, column=0, sticky="ew")
        ttk.Button(button_frame, text="Save Levels", command=self.save_levels).pack(side=tk.LEFT, padx=6)
        ttk.Button(button_frame, text="Reset to Default", command=self.reset_to_default).pack(side=tk.LEFT, padx=6)

        # Action buttons
        actions = ttk.Frame(root, padding=10)
        actions.grid(row=5, column=0, sticky="ew")
        self.test_button = ttk.Button(
            actions,
            text=f"Test Vibration ({TEST_VIBRATION_SECONDS:g} s)",
            command=self.test_vibration,
        )
        self.test_button.grid(row=0, column=0, padx=6)
        ttk.Button(actions, text="Quit", command=self.on_quit).grid(row=0, column=1)
        self.detection_overlay_button = ttk.Button(
            actions,
            text="Show Detection Areas",
            command=self.toggle_detection_overlay,
        )
        self.detection_overlay_button.grid(row=0, column=2, padx=6)
        self.raid_status_var = tk.StringVar(
            value="Raid status: waiting for raid start (all health regions green)"
        )
        ttk.Label(actions, textvariable=self.raid_status_var).grid(
            row=1,
            column=0,
            columnspan=2,
            sticky="w",
            padx=6,
            pady=(8, 0),
        )
        self.inventory_status_var = tk.StringVar(value="Inventory: Checking")
        ttk.Label(actions, textvariable=self.inventory_status_var).grid(
            row=2,
            column=0,
            columnspan=2,
            sticky="w",
            padx=6,
            pady=(4, 0),
        )

        self.root.after(50, self._drain_ui_queue)

    @staticmethod
    def _mirror_bool(var, apply):
        """Copy a Tk BooleanVar into plain Python state now and on every change."""
        apply(bool(var.get()))
        var.trace_add("write", lambda *_: apply(bool(var.get())))

    def call_in_ui(self, fn, *args):
        """Run fn(*args) on the Tk main thread. Safe to call from any thread."""
        self.ui_queue.put((fn, args))

    def _drain_ui_queue(self):
        # Reschedule first so updates keep flowing while a messagebox is open.
        self.root.after(50, self._drain_ui_queue)
        while True:
            try:
                fn, args = self.ui_queue.get_nowait()
            except queue.Empty:
                return
            try:
                fn(*args)
            except Exception as e:
                log.warning("UI update error: %s", e)

    def toggle_detection_overlay(self):
        if self.detection_overlay is not None:
            self.detection_overlay.destroy()
            self.detection_overlay = None
            self.detection_overlay_button.configure(text="Show Detection Areas")
            return

        screen_width, screen_height = tarkbuzz.get_screen_resolution()
        overlay = tk.Toplevel(self.root)
        overlay.overrideredirect(True)
        overlay.attributes("-topmost", True)
        overlay.attributes("-transparentcolor", "magenta")
        overlay.geometry(f"{screen_width}x{screen_height}+0+0")

        canvas = tk.Canvas(
            overlay,
            width=screen_width,
            height=screen_height,
            bg="magenta",
            highlightthickness=0,
        )
        canvas.pack()

        region_colors = {
            "head": "#43d9ff",
            "thorax": "#55e6a5",
            "stomach": "#f0d44a",
            "right_arm": "#ff9d45",
            "left_arm": "#d68aff",
            "right_leg": "#ff667a",
            "left_leg": "#68a0ff",
        }
        for limb, (reference_region, angle_degrees) in tarkbuzz.CV_LIMB_REGIONS.items():
            scaled_region = tarkbuzz.scale_region(
                reference_region,
                screen_width,
                screen_height,
            )
            sample_region = tarkbuzz.inset_region(
                scaled_region,
                tarkbuzz.DETECTION_INSET,
            )
            points = tarkbuzz._rotated_region_points(
                sample_region,
                angle_degrees,
            )
            color = region_colors[limb]
            canvas.create_polygon(
                *[coordinate for point in points for coordinate in point],
                outline=color,
                fill="",
                width=3,
            )
            canvas.create_text(
                points[0][0] + 4,
                points[0][1] + 4,
                text=f"{limb.replace('_', ' ').title()} ({angle_degrees} deg)",
                fill=color,
                anchor="nw",
                font=("TkDefaultFont", 11, "bold"),
            )

        for tab, reference_region in tarkbuzz.INVENTORY_TAB_REGIONS.items():
            tab_region = tarkbuzz.inset_region(
                tarkbuzz.scale_region(
                    reference_region,
                    screen_width,
                    screen_height,
                ),
                tarkbuzz.DETECTION_INSET,
            )
            canvas.create_rectangle(
                *tab_region,
                outline="#ff55d8",
                width=2,
            )
            canvas.create_text(
                tab_region[0] + 4,
                tab_region[1] + 4,
                text=f"Inventory {tab}",
                fill="#ff55d8",
                anchor="nw",
                font=("TkDefaultFont", 10, "bold"),
            )

        raid_region = tarkbuzz.inset_region(
            tarkbuzz.scale_region(
                tarkbuzz.RAID_OUTCOME_REGION,
                screen_width,
                screen_height,
            ),
            tarkbuzz.DETECTION_INSET,
        )
        raid_points = (
            (raid_region[0], raid_region[1]),
            (raid_region[2], raid_region[1]),
            (raid_region[2], raid_region[3]),
            (raid_region[0], raid_region[3]),
        )
        canvas.create_polygon(
            *[coordinate for point in raid_points for coordinate in point],
            outline="#ffffff",
            fill="",
            width=3,
        )
        canvas.create_text(
            raid_region[0] + 4,
            raid_region[1] + 4,
            text="Raid outcome",
            fill="#ffffff",
            anchor="nw",
            font=("TkDefaultFont", 11, "bold"),
        )

        overlay.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(overlay.winfo_id())
        styles = ctypes.windll.user32.GetWindowLongW(hwnd, -20)
        ctypes.windll.user32.SetWindowLongW(
            hwnd,
            -20,
            styles | 0x20 | 0x80 | 0x8000000,
        )
        self.detection_overlay = overlay
        self.detection_overlay_button.configure(text="Hide Detection Areas")

    def configure_dark_theme(self):
        palette = DARK_THEME
        self.root.configure(background=palette["window"])

        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            ".",
            background=palette["window"],
            foreground=palette["text"],
            bordercolor=palette["border"],
            focuscolor=palette["accent"],
        )
        style.configure("TFrame", background=palette["window"])
        style.configure(
            "TLabel",
            background=palette["window"],
            foreground=palette["text"],
        )
        style.configure(
            "TLabelframe",
            background=palette["panel"],
            foreground=palette["text"],
            bordercolor=palette["border"],
        )
        style.configure(
            "TLabelframe.Label",
            background=palette["panel"],
            foreground=palette["text"],
        )
        style.configure(
            "TButton",
            background=palette["button"],
            foreground=palette["text"],
            bordercolor=palette["border"],
            padding=(10, 5),
        )
        style.map(
            "TButton",
            background=[
                ("pressed", palette["selection"]),
                ("active", palette["button_active"]),
            ],
            foreground=[("disabled", palette["muted"])],
        )
        style.configure(
            "TCheckbutton",
            background=palette["panel"],
            foreground=palette["text"],
            indicatorcolor=palette["input"],
            padding=3,
        )
        style.map(
            "TCheckbutton",
            background=[("active", palette["panel"])],
            foreground=[("disabled", palette["muted"])],
            indicatorcolor=[
                ("selected", palette["accent"]),
                ("active", palette["button_active"]),
            ],
        )
        style.configure(
            "TEntry",
            fieldbackground=palette["input"],
            foreground=palette["text"],
            insertcolor=palette["text"],
            bordercolor=palette["border"],
        )
        style.configure(
            "Horizontal.TScale",
            background=palette["panel"],
            troughcolor=palette["input"],
            bordercolor=palette["border"],
        )
        for status, foreground in {
            "Unknown": palette["muted"],
            "Green": "#69d391",
            "Yellow": "#f2cd63",
            "Red": "#ff7474",
            "Black": "#e7eaf0",
        }.items():
            style.configure(
                f"LimbStatus.{status}.TLabel",
                background=palette["panel"],
                foreground=foreground,
                font=("TkDefaultFont", 9, "bold"),
            )
        style.configure(
            "LimbStatus.Checking.TLabel",
            background=palette["panel"],
            foreground="#f2cd63",
            font=("TkDefaultFont", 9, "bold"),
        )
        style.configure(
            "LimbStatusHeader.TLabel",
            background=palette["panel"],
            foreground=palette["muted"],
        )

    def _apply_limb_status(self, statuses):
        for limb, (text, style) in statuses.items():
            if limb not in self.limb_status_vars:
                continue
            self.limb_status_vars[limb].set(text)
            self.limb_status_labels[limb].configure(
                style=f"LimbStatus.{style}.TLabel"
            )

    def run_event_loop(self):
        """Run the asyncio event loop in a background thread"""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_async(self, coro):
        """Schedule an async coroutine on the persistent event loop"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def detect(self, fn, *args):
        """Run a blocking TarkBuzz detection call on the detection thread."""
        return await asyncio.get_running_loop().run_in_executor(
            self.detection_executor, fn, *args
        )

    @staticmethod
    def _sample_limbs_blocking():
        colors = tarkbuzz.get_all_limb_colors()
        return colors, tarkbuzz.get_limb_detection_statuses()

    @staticmethod
    def _reset_detection_blocking():
        tarkbuzz.reset_limb_detection()
        tarkbuzz.reset_inventory_detection()
        tarkbuzz.reset_raid_outcome_detection()

    async def sample_limbs(self):
        """Sample limb colours off the event loop and refresh the limb labels."""
        colors, statuses = await self.detect(self._sample_limbs_blocking)
        self.call_in_ui(self._apply_limb_status, statuses)
        return colors

    def enabled_limb_levels(self, colors):
        return {
            limb: tarkbuzz.get_vibration_for_limb(limb, colors[limb])
            for limb in tarkbuzz.LIMB_KEYS
            if self.limb_enabled_state.get(limb) and limb in colors
        }

    def damaged_limb_levels(self, colors):
        """Levels for enabled limbs that are detected and not green."""
        green = tarkbuzz.DETECTED_COLOUR_RGB["green"]
        return {
            limb: level
            for limb, level in self.enabled_limb_levels(colors).items()
            if colors[limb] is not None and colors[limb] != green
        }

    def _monitor_active(self, generation):
        return self.monitoring and generation == self.monitor_generation

    async def set_all_devices(self, level, force=False, generation=None):
        """Send a vibration level to every device.

        Skips devices already at that level unless force is set, so the
        monitor loop does not resend the same command every pass. With a
        generation, stops sending as soon as that monitor loop is stale.
        """
        level = float(level)
        for device in list(self.devices):
            if generation is not None and not self._monitor_active(generation):
                return
            key = id(device)
            if not force and self._sent_levels.get(key) == level:
                continue
            await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, level))
            self._sent_levels[key] = level

    async def play_pattern(self, pattern, generation, is_test=False):
        """Play a dynamic buzz pattern on every device, then stop them.

        Checks the monitor generation before every pulse, so a pattern
        stops at once when monitoring is disconnected or quit instead of
        pulsing on after the stop command. A monitor pattern also yields
        as soon as Test Vibration starts.
        """
        for intensity, duration_ms in pattern:
            if not self._monitor_active(generation):
                return
            if self.testing_vibration and not is_test:
                return
            await self.set_all_devices(intensity, generation=generation)
            await asyncio.sleep(duration_ms / 1000.0)
        await self.set_all_devices(0.0, generation=generation)

    async def stop_all_devices(self):
        try:
            await self.set_all_devices(0.0, force=True)
        except Exception as e:
            log.warning("Could not stop devices: %s", e)

    def set_raid_status(self, text):
        self.call_in_ui(self.raid_status_var.set, text)

    def save_levels(self):
        levels_to_save = {
            key: float(self.level_vars[key].get()) for key in COLOR_KEYS
        }
        save_levels_to_db(levels_to_save)
        tarkbuzz.set_colour_levels(levels_to_save)

        messagebox.showinfo("Saved", "Color vibration levels saved to database.")

    def reset_to_default(self):
        """Reset all sliders to default values and clear database"""
        # Reset sliders to hardcoded defaults (the slider trace applies them)
        for key in COLOR_KEYS:
            self.level_vars[key].set(DEFAULT_LEVELS[key])

        # Clear database
        init_db()
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("DELETE FROM levels")
        conn.commit()
        conn.close()

        messagebox.showinfo("Reset", "All levels reset to default values.")

    def connect_client(self):
        url = self.url_var.get().strip()
        if not url:
            messagebox.showerror("Error", "Please enter server URL")
            return
        if self.connecting or self.client is not None:
            messagebox.showinfo(
                "Info",
                "Already connected. Disconnect first to connect again.",
            )
            return
        self.connecting = True

        async def do_connect():
            try:
                from buttplug.client import ButtplugClient
                client = ButtplugClient("TarkBuzz GUI")
                await client.connect(url)

                self.client = client
                self.devices = []
                self._sent_levels = {}
                self.polling = True
                self.monitoring = True
                self.raid_started = False
                self.raid_end_outcome = None
                self.raid_vibration_until = None
                await self.detect(self._reset_detection_blocking)
                self._last_inventory_status = None
                # A new generation makes any older monitor loop exit.
                self.monitor_generation += 1
                self.monitor_task = asyncio.create_task(
                    self.monitor_loop(self.monitor_generation)
                )
                self.call_in_ui(self.devices_list.delete, 0, tk.END)
                self.call_in_ui(
                    messagebox.showinfo,
                    "Connected",
                    "Successfully connected to Buttplug server",
                )
                self.call_in_ui(self.poll_devices)
            except Exception as e:
                self.call_in_ui(messagebox.showerror, "Connection error", str(e))
            finally:
                self.connecting = False

        self.run_async(do_connect())

    def poll_devices(self):
        """Poll for connected devices every 5 seconds (call on the Tk thread)."""
        if not self.polling or self.client is None:
            return

        async def do_poll():
            try:
                devices = list(self.client.devices.values())
                self.devices = devices
                current = {id(d) for d in devices}
                self._sent_levels = {
                    k: v for k, v in self._sent_levels.items() if k in current
                }
                names = []
                for d in devices:
                    try:
                        names.append(d.name)
                    except Exception:
                        names.append(str(d))
                self.call_in_ui(self._show_devices, names)
            except Exception as e:
                log.warning("Poll error: %s", e)
            if self.polling:
                self.call_in_ui(self.root.after, 5000, self.poll_devices)

        self.run_async(do_poll())

    def _show_devices(self, names):
        self.devices_list.delete(0, tk.END)
        for name in names:
            self.devices_list.insert(tk.END, name)

    async def monitor_loop(self, generation):
        last_logged_levels = None
        while self._monitor_active(generation):
            try:
                inventory_status = await self.detect(tarkbuzz.get_inventory_status)
                if not self._monitor_active(generation):
                    break
                if inventory_status != self._last_inventory_status:
                    self._last_inventory_status = inventory_status
                    log.info("Inventory: %s", inventory_status)
                    self.call_in_ui(
                        self.inventory_status_var.set,
                        f"Inventory: {inventory_status}",
                    )

                colors = None
                if not self.raid_started:
                    colors = await self.sample_limbs()
                    all_regions_green = all(
                        colors.get(limb)
                        == tarkbuzz.DETECTED_COLOUR_RGB["green"]
                        for limb in tarkbuzz.CV_LIMB_REGIONS
                    )
                    if not all_regions_green:
                        await asyncio.sleep(0.1)
                        continue

                    self.raid_started = True
                    log.info("All health regions are green; raid monitoring enabled")
                    self.set_raid_status("Raid status: in raid; monitoring")

                now = time.monotonic()
                if self.raid_end_outcome is not None:
                    colors = await self.sample_limbs()
                    all_regions_green = all(
                        colors.get(limb)
                        == tarkbuzz.DETECTED_COLOUR_RGB["green"]
                        for limb in tarkbuzz.CV_LIMB_REGIONS
                    )
                    if all_regions_green:
                        log.info("Gameplay resumed: all health regions are green")
                        if self.raid_vibration_until is not None:
                            await self.set_all_devices(0.0, generation=generation)
                        self.raid_end_outcome = None
                        self.raid_vibration_until = None
                        await self.detect(tarkbuzz.reset_raid_outcome_detection)
                        self.set_raid_status("Raid status: monitoring")

                    if self.raid_end_outcome is not None:
                        if (
                            self.raid_vibration_until is not None
                            and now >= self.raid_vibration_until
                        ):
                            await self.set_all_devices(0.0, generation=generation)
                            self.raid_vibration_until = None
                            outcome_label = RAID_OUTCOME_LABELS[
                                self.raid_end_outcome
                            ]
                            self.set_raid_status(
                                f"Raid status: {outcome_label} (60-second "
                                "vibration complete); waiting for gameplay"
                            )
                        await asyncio.sleep(0.1)
                        continue

                outcome = await self.detect(tarkbuzz.get_raid_outcome)
                if outcome in RAID_OUTCOME_LABELS:
                    self.raid_end_outcome = outcome
                    outcome_label = RAID_OUTCOME_LABELS[outcome]
                    if self.raid_vibration_state.get(outcome):
                        self.raid_vibration_until = now + 60.0
                        await self.set_all_devices(1.0, generation=generation)
                        status = (
                            f"Raid status: {outcome_label} "
                            "(maximum vibration for up to 60 seconds)"
                        )
                    else:
                        self.raid_vibration_until = None
                        await self.set_all_devices(0.0, generation=generation)
                        status = (
                            f"Raid status: {outcome_label}; vibration disabled, "
                            "waiting for gameplay"
                        )
                    log.info("Confirmed raid outcome: %s", outcome)
                    self.set_raid_status(status)
                    await asyncio.sleep(0.1)
                    continue

                if colors is None:
                    colors = await self.sample_limbs()

                limb_levels = self.enabled_limb_levels(colors)
                if limb_levels != last_logged_levels:
                    last_logged_levels = limb_levels
                    log.debug("Limb colours: %s", colors)
                    log.debug("Limb levels: %s", limb_levels)

                if not self.devices:
                    await asyncio.sleep(0.5)
                    continue

                if self.testing_vibration:
                    # Test Vibration owns the devices until it finishes.
                    await asyncio.sleep(0.1)
                    continue

                if (
                    self.pause_in_inventory_state
                    and inventory_status == "In inventory"
                ):
                    await self.set_all_devices(0.0, generation=generation)
                elif self.dynamic_buzz_state:
                    pattern = tarkbuzz.calculate_dynamic_buzz_pattern(
                        self.damaged_limb_levels(colors)
                    )
                    if pattern:
                        await self.play_pattern(pattern, generation)
                        continue
                    await self.set_all_devices(0.0, generation=generation)
                else:
                    await self.set_all_devices(
                        max(limb_levels.values(), default=0.0),
                        generation=generation,
                    )

                await asyncio.sleep(0.1)

            except Exception as e:
                log.warning("Monitor loop error: %s", e)
                await asyncio.sleep(1)

    def disconnect(self):
        if self.client is None:
            messagebox.showinfo("Info", "Not connected")
            return

        async def do_disconnect():
            self.polling = False
            self.monitoring = False
            self.monitor_generation += 1
            await self.stop_all_devices()
            try:
                await self.client.disconnect()
                self.call_in_ui(
                    messagebox.showinfo, "Disconnected", "Client disconnected"
                )
            except Exception as e:
                self.call_in_ui(messagebox.showerror, "Disconnect error", str(e))
            finally:
                self.client = None
                self.devices = []
                self._sent_levels = {}
                self.call_in_ui(self.devices_list.delete, 0, tk.END)

        self.run_async(do_disconnect())

    def test_vibration(self):
        """Vibrate for a few seconds at what the ticked limbs currently give.

        The monitor loop holds off while the test runs, so the test level is
        not overwritten, and the devices stop on their own afterwards.
        """
        if not self.devices:
            messagebox.showerror("Error", "No devices connected. Connect a device first.")
            return
        if self.testing_vibration:
            return
        self.testing_vibration = True
        self.test_button.state(["disabled"])
        generation = self.monitor_generation

        async def run_test():
            try:
                colors = await self.sample_limbs()
                limb_levels = self.enabled_limb_levels(colors)
                log.debug("Test vibration: colours %s, levels %s", colors, limb_levels)

                if self.dynamic_buzz_state:
                    pattern = tarkbuzz.calculate_dynamic_buzz_pattern(
                        self.damaged_limb_levels(colors)
                    )
                    if pattern:
                        await self.play_pattern(pattern, generation, is_test=True)
                        message = "Played one dynamic buzz pattern."
                    else:
                        message = "No damage detected, so dynamic buzzing has nothing to play."
                else:
                    level = max(limb_levels.values(), default=0.0)
                    await self.set_all_devices(level, force=True, generation=generation)
                    await asyncio.sleep(TEST_VIBRATION_SECONDS)
                    await self.set_all_devices(0.0, force=True, generation=generation)
                    message = (
                        f"Vibrated at {level:.2f} for {TEST_VIBRATION_SECONDS:g} seconds."
                    )
                log.info("Test vibration: %s", message)
                self.call_in_ui(messagebox.showinfo, "Test Vibration", message)
            except Exception as e:
                log.exception("Test vibration failed")
                self.call_in_ui(messagebox.showerror, "Test Vibration error", str(e))
            finally:
                self.testing_vibration = False
                self.call_in_ui(self.test_button.state, ["!disabled"])

        self.run_async(run_test())

    def on_quit(self):
        if self.detection_overlay is not None:
            self.detection_overlay.destroy()
            self.detection_overlay = None
        try:
            self.polling = False
            self.monitoring = False
            if self.client is not None:
                async def stop_and_disconnect():
                    self.monitor_generation += 1
                    await self.stop_all_devices()
                    await self.client.disconnect()
                future = self.run_async(stop_and_disconnect())
                future.result(timeout=5)
        except Exception:
            pass
        finally:
            # Stop the event loop and the detection worker
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.detection_executor.shutdown(wait=False, cancel_futures=True)
        self.root.quit()


def main():
    logging.basicConfig(
        level=logging.DEBUG if os.environ.get("TARKBUZZ_DEBUG") else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )
    root = tk.Tk()
    app = TarkBuzzGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_quit)
    root.mainloop()


if __name__ == "__main__":
    main()
