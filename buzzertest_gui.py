import threading
import asyncio
import tkinter as tk
from tkinter import ttk, messagebox
import sqlite3
import os

import Buzzertest as bt


# Default vibration levels (hardcoded)
DEFAULT_LEVELS = {
    "green_limb": 0.0,
    "yellow_limb": 0.25,
    "orange_limb": 0.5,
    "red_limb": 0.75,
    "black_limb": 1.0,
}

# Database helper functions
DB_PATH = os.path.expanduser("~/.buzz_levels.db")

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


class BuzzGUI:
    def __init__(self, root):
        self.root = root
        root.title("Buzz Test UI")

        # Buttplug client and device list
        self.client = None
        self.devices = []
        self.polling = False
        self.monitoring = False
        
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
        self.devices_list.grid(row=1, column=1, columnspan=3, sticky="w", pady=(8,0))

        # Sliders for color vibration levels
        sliders = ttk.LabelFrame(root, text="Color Vibration Levels", padding=10)
        sliders.grid(row=2, column=0, sticky="nsew", padx=10, pady=8)

        self.level_vars = {}
        color_keys = ["green_limb", "yellow_limb", "orange_limb", "red_limb", "black_limb"]
        color_labels = ["Green Limb", "Yellow Limb", "Orange Limb", "Red Limb", "Black Limb"]
        defaults = [DEFAULT_LEVELS[k] for k in color_keys]
        
        # Load saved levels from database
        saved_levels = load_levels()
        initial_values = [saved_levels.get(key, default) for key, default in zip(color_keys, defaults)]

        for i, (key, label, initial_val) in enumerate(zip(color_keys, color_labels, initial_values)):
            ttk.Label(sliders, text=label).grid(row=i, column=0, sticky="w")
            v = tk.DoubleVar(value=initial_val)
            s = ttk.Scale(sliders, from_=0.0, to=1.0, orient=tk.HORIZONTAL, variable=v)
            s.grid(row=i, column=1, sticky="we", padx=8)
            val_label = ttk.Label(sliders, textvariable=tk.StringVar(value=f"{initial_val:.2f}"))
            val_label.grid(row=i, column=2, sticky="e")

            def make_trace(var, lab):
                def on_change(*_):
                    lab.config(text=f"{var.get():.2f}")
                return on_change

            v.trace_add("write", make_trace(v, val_label))
            self.level_vars[key] = v

        # Limb toggle section
        limb_frame = ttk.LabelFrame(root, text="Limb Toggle", padding=10)
        limb_frame.grid(row=2, column=1, sticky="nsew", padx=10, pady=8)
        
        self.limb_enabled = {}
        self.vibrating = False  # Track vibration state
        limb_keys = ["head", "thorax", "stomach", "right_arm", "left_arm", "right_leg", "left_leg"]
        limb_labels = ["Head", "Thorax", "Stomach", "Right Arm", "Left Arm", "Right Leg", "Left Leg"]
        
        for i, (key, label) in enumerate(zip(limb_keys, limb_labels)):
            toggle_var = tk.BooleanVar(value=True)
            toggle_check = ttk.Checkbutton(limb_frame, text=label, variable=toggle_var)
            toggle_check.grid(row=i, column=0, sticky="w", pady=4)
            self.limb_enabled[key] = toggle_var
        
        # Dynamic buzzing toggle
        self.dynamic_buzz_enabled = tk.BooleanVar(value=False)
        dynamic_buzz_check = ttk.Checkbutton(
            limb_frame, 
            text="Dynamic Buzzing", 
            variable=self.dynamic_buzz_enabled
        )
        dynamic_buzz_check.grid(row=len(limb_keys), column=0, sticky="w", pady=(8, 0))

        # Buttons for save and reset
        button_frame = ttk.Frame(root, padding=10)
        button_frame.grid(row=3, column=0, sticky="ew")
        ttk.Button(button_frame, text="Save Levels", command=self.save_levels).pack(side=tk.LEFT, padx=6)
        ttk.Button(button_frame, text="Reset to Default", command=self.reset_to_default).pack(side=tk.LEFT, padx=6)

        # Action buttons
        actions = ttk.Frame(root, padding=10)
        actions.grid(row=4, column=0, sticky="ew")
        ttk.Button(actions, text="Vibrate Now (Head)", command=self.vibrate_head).grid(row=0, column=0, padx=6)
        ttk.Button(actions, text="Quit", command=self.on_quit).grid(row=0, column=1)

    def run_event_loop(self):
        """Run the asyncio event loop in a background thread"""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run_async(self, coro):
        """Schedule an async coroutine on the persistent event loop"""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def save_levels(self):
        levels_to_save = {
            "green_limb": float(self.level_vars["green_limb"].get()),
            "yellow_limb": float(self.level_vars["yellow_limb"].get()),
            "orange_limb": float(self.level_vars["orange_limb"].get()),
            "red_limb": float(self.level_vars["red_limb"].get()),
            "black_limb": float(self.level_vars["black_limb"].get()),
        }
        
        # Save to database
        save_levels_to_db(levels_to_save)
        
        # Update module variables
        bt.green_limb = levels_to_save["green_limb"]
        bt.yellow_limb = levels_to_save["yellow_limb"]
        bt.orange_limb = levels_to_save["orange_limb"]
        bt.red_limb = levels_to_save["red_limb"]
        bt.black_limb = levels_to_save["black_limb"]
        
        messagebox.showinfo("Saved", "Color vibration levels saved to database.")

    def reset_to_default(self):
        """Reset all sliders to default values and clear database"""
        color_keys = ["green_limb", "yellow_limb", "orange_limb", "red_limb", "black_limb"]
        
        # Reset sliders to hardcoded defaults
        for key in color_keys:
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

        async def do_connect():
            try:
                from buttplug.client import ButtplugClient
                client = ButtplugClient("Buzz GUI")
                await client.connect(url)
                
                # Success - update UI on main thread
                self.client = client
                self.devices = []
                self.devices_list.delete(0, tk.END)
                self.polling = True
                self.monitoring = True
                self.run_async(self.monitor_loop())
                self.root.after(0, lambda: messagebox.showinfo("Connected", "Successfully connected to Buttplug server"))
                self.poll_devices()
            except Exception as e:
                error_msg = str(e)
                self.root.after(0, lambda msg=error_msg: messagebox.showerror("Connection error", msg))

        self.run_async(do_connect())

    def poll_devices(self):
        """Poll for connected devices every 5 seconds"""
        if not self.polling or self.client is None:
            return

        async def do_poll():
            try:
                devices = list(self.client.devices.values())
                
                # Update UI on main thread
                self.devices = devices
                self.devices_list.delete(0, tk.END)
                for d in devices:
                    try:
                        self.devices_list.insert(tk.END, d.name)
                    except Exception:
                        self.devices_list.insert(tk.END, str(d))
                
                # Schedule next poll
                if self.polling:
                    self.root.after(5000, self.poll_devices)
            except Exception as e:
                print(f"Poll error: {e}")
                if self.polling:
                    self.root.after(5000, self.poll_devices)

        self.run_async(do_poll())


    async def monitor_loop(self):
        while self.monitoring:
            try:
                from buttplug import DeviceOutputCommand, OutputType

                # Get colors
                colors = bt.get_all_limb_colors()
                print(f"MONITOR COLORS: {colors}")

                dead_result = bt.dead(colors.get("dead"), colors.get("dead_alt"))
                if dead_result == 0.0:
                    print("Dead detector has precedence; other detectors will be skipped")
                    await asyncio.sleep(0.1)
                    continue

                # Enabled limbs
                enabled_limbs = [
                    k for k, v in self.limb_enabled.items()
                    if v.get()
                ]

                # Calculate levels
                limb_levels = {}

                for limb in enabled_limbs:
                    if limb in colors:
                        level = bt.get_vibration_for_limb(
                            limb,
                            colors[limb]
                        )

                        limb_levels[limb] = level

                print(f"MONITOR LEVELS: {limb_levels}")

                if limb_levels:
                    max_level = max(limb_levels.values())

                    if not self.devices:
                        await asyncio.sleep(0.5)
                        continue

                    for device in self.devices:
                        await device.run_output(
                            DeviceOutputCommand(
                                OutputType.VIBRATE,
                                float(max_level)
                            )
                        )

                await asyncio.sleep(0.1)

            except Exception as e:
                print(f"Monitor loop error: {e}")
                await asyncio.sleep(1)   



    def disconnect(self):
        if self.client is None:
            messagebox.showinfo("Info", "Not connected")
            return

        async def do_disconnect():
            try:
                self.polling = False
                self.monitoring = False
                await self.client.disconnect()
                
                # Update UI on main thread
                self.client = None
                self.devices = []
                self.devices_list.delete(0, tk.END)
                self.root.after(0, lambda: messagebox.showinfo("Disconnected", "Client disconnected"))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Disconnect error", str(e)))
                self.polling = False
                self.client = None

        self.run_async(do_disconnect())

    def vibrate_head(self):
        if self.vibrating:
            # Stop vibration
            async def stop_vibrate():
                try:
                    from buttplug import DeviceOutputCommand, OutputType
                    for device in self.devices:
                        await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, 0.0))
                    self.vibrating = False
                    self.root.after(0, lambda: messagebox.showinfo("Stopped", "Vibration stopped on all devices"))
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror("Error", str(e)))
            
            self.run_async(stop_vibrate())
            return
        
        # Start vibration
        if not self.devices:
            messagebox.showerror("Error", "No devices connected. Connect a device first.")
            return

        async def do_vibrate():
            try:
                from buttplug import DeviceOutputCommand, OutputType
                
                # Sample all limb colors
                colors = bt.get_all_limb_colors()
                print(f"DEBUG: Sampled colors: {colors}")

                dead_result = bt.dead(colors.get("dead"), colors.get("dead_alt"))
                if dead_result == 0.0:
                    print("Dead detector has precedence; other detectors will be skipped")
                    return
                
                # Get enabled limbs
                enabled_limbs = [k for k, v in self.limb_enabled.items() if v.get()]
                print(f"DEBUG: Enabled limbs: {enabled_limbs}")
                
                # Calculate vibration levels for enabled limbs
                limb_levels = {}
                for limb in enabled_limbs:
                    if limb in colors:
                        level = bt.get_vibration_for_limb(limb, colors[limb])
                        limb_levels[limb] = level
                        print(f"DEBUG: {limb} color {colors[limb]} -> level {level}")
                
                print(f"DEBUG: Final limb_levels: {limb_levels}")
                
                if not limb_levels:
                    raise RuntimeError("Could not sample any limb colors")
                
                # Check if dynamic buzzing is enabled
                if self.dynamic_buzz_enabled.get():
                    print("DEBUG: Using dynamic buzzing")
                    # Use dynamic buzzing pattern - send to all devices
                    pattern = bt.calculate_dynamic_buzz_pattern(limb_levels)
                    print(f"DEBUG: Pattern: {pattern}")
                    if pattern:
                        for i, device in enumerate(self.devices):
                            print(f"DEBUG: Sending pattern to device {i}: {device}")
                            await bt.run_dynamic_buzz_pattern(device, pattern)
                        status_msg = f"Sent dynamic buzz pattern to {len(self.devices)} device(s)"
                    else:
                        # No damage detected (all green), send stop command to all
                        for device in self.devices:
                            await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, 0.0))
                        status_msg = "No damage detected - no buzzing sent"
                else:
                    print("DEBUG: Using standard vibration")
                    # Standard vibration: use max damage level from enabled limbs
                    print("LIMB LEVELS:", limb_levels) #test, come back here
                    print("BLACK:", bt.black_limb)
                    max_level = max(limb_levels.values())
                    print(f"DEBUG: Max level calculated: {max_level}")
                    
                    # Send to all connected devices
                    for i, device in enumerate(self.devices):
                        print(f"DEBUG: Sending vibration {max_level} to device {i}: {device}")
                        await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, float(max_level)))
                        print(f"DEBUG: Sent to device {i}")
                    
                    status_msg = f"Sent vibration {max_level:.2f} to {len(self.devices)} device(s)"

                print(f"DEBUG: Status message: {status_msg}")
                self.vibrating = True
                self.root.after(0, lambda: messagebox.showinfo("Vibrate", status_msg + "\n\nPress 'Vibrate Now' again to stop"))
            except Exception as e:
                print(f"DEBUG: Exception occurred: {e}")
                import traceback
                traceback.print_exc()
                self.root.after(0, lambda: messagebox.showerror("Vibrate error", str(e)))

        self.run_async(do_vibrate())

    def on_quit(self):
        try:
            self.polling = False
            self.monitoring = False
            if self.client is not None:
                async def dd():
                    await self.client.disconnect()
                future = self.run_async(dd())
                future.result(timeout=5)
        except Exception:
            pass
        finally:
            # Stop the event loop
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.root.quit()


def main():
    root = tk.Tk()
    app = BuzzGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
