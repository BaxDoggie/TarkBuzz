import asyncio
import os
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest import mock

import _support
import TarkBuzz as tb
import TarkBuzz_gui as gui

GREEN = tb.DETECTED_COLOUR_RGB["green"]
RED = tb.DETECTED_COLOUR_RGB["red"]
MAIN = threading.main_thread()


class GuiTestCase(unittest.TestCase):
    """Runs the real GUI and monitor loop against fake detection and devices."""

    def setUp(self):
        self.phase = {
            "colors": GREEN,
            "outcome": None,
            "inventory": "Not in inventory",
            "delay": 0.02,
        }
        self.detect_threads = set()
        self.off_main_tk_writes = []
        tmp = tempfile.mkdtemp()

        def fake_colors():
            self.detect_threads.add(threading.current_thread().name)
            time.sleep(self.phase["delay"])
            return {limb: self.phase["colors"] for limb in tb.CV_LIMB_REGIONS}

        original_set = tk.Variable.set
        test = self

        def guarded_set(var, value):
            if threading.current_thread() is not MAIN:
                test.off_main_tk_writes.append(value)
            return original_set(var, value)

        self.info = mock.MagicMock()
        self.error = mock.MagicMock()
        patches = [
            mock.patch.object(gui, "DB_PATH", os.path.join(tmp, "levels.db")),
            mock.patch.object(gui.messagebox, "showinfo", self.info),
            mock.patch.object(gui.messagebox, "showerror", self.error),
            mock.patch.object(tk.Variable, "set", guarded_set),
            mock.patch.object(tb, "get_all_limb_colors", fake_colors),
            mock.patch.object(
                tb,
                "get_limb_detection_statuses",
                lambda: {limb: ("Green", "Green") for limb in tb.CV_LIMB_REGIONS},
            ),
            mock.patch.object(tb, "get_inventory_status", lambda: self.phase["inventory"]),
            mock.patch.object(tb, "get_raid_outcome", lambda: self.phase["outcome"]),
            mock.patch.object(tb, "reset_raid_outcome_detection", lambda: None),
            mock.patch.object(tb, "reset_limb_detection", lambda: None),
            mock.patch.object(tb, "reset_inventory_detection", lambda: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

        self.device = _support.FakeDevice()
        _support.FakeClient.devices = {0: self.device}
        _support.FakeClient.instances = []

        self.root = tk.Tk()
        self.root.withdraw()
        self.app = gui.TarkBuzzGUI(self.root)
        self.statuses = []
        self.app.raid_status_var.trace_add(
            "write", lambda *_: self.statuses.append(self.app.raid_status_var.get())
        )

    def tearDown(self):
        self.app.monitoring = False
        self.app.polling = False
        self.app.loop.call_soon_threadsafe(self.app.loop.stop)
        self.app.detection_executor.shutdown(wait=False)
        for after_id in self.root.tk.call("after", "info"):
            self.root.after_cancel(after_id)
        self.root.destroy()
        tb.set_colour_levels(gui.DEFAULT_LEVELS)

    def run_ui(self, steps, ticks, tick_ms=100):
        """Run the Tk main loop, calling steps[n] on the main thread at tick n."""

        def tick(n=0):
            if n in steps:
                steps[n]()
            if n >= ticks:
                self.root.quit()
                return
            self.root.after(tick_ms, tick, n + 1)

        self.root.after(0, tick)
        self.root.mainloop()
        self.assertEqual(self.off_main_tk_writes, [], "Tk touched off the main thread")

    def set_phase(self, **values):
        return lambda: self.phase.update(values)


class MonitorTests(GuiTestCase):
    def test_raid_flow_sends_each_level_once(self):
        self.app.raid_vibration_enabled["kia"].set(True)
        skip_60s = lambda: self.app.loop.call_soon_threadsafe(
            setattr, self.app, "raid_vibration_until", 0.0
        )
        self.run_ui(
            {
                0: self.app.connect_client,
                10: self.set_phase(colors=RED),
                25: self.set_phase(outcome="kia"),
                35: skip_60s,
            },
            ticks=45,
        )
        levels = self.device.levels
        self.assertEqual(levels.count(0.75), 1, levels)
        self.assertIn(1.0, levels)
        self.assertEqual(levels[-1], 0.0)
        self.assertEqual(
            self.statuses[-1],
            "Raid status: killed in action (60-second vibration complete); "
            "waiting for gameplay",
        )

    def test_slow_detection_does_not_block_event_loop(self):
        self.phase["delay"] = 0.43
        ticks = []

        async def heartbeat():
            while self.app.monitoring or not ticks:
                ticks.append(time.monotonic())
                await asyncio.sleep(0.02)

        self.run_ui(
            {
                0: self.app.connect_client,
                2: lambda: self.app.run_async(heartbeat()),
            },
            ticks=25,
        )
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        self.assertLess(max(gaps), 0.15)
        self.assertTrue(all(n.startswith("detection") for n in self.detect_threads))

    def test_pauses_while_in_inventory(self):
        self.run_ui(
            {
                0: self.app.connect_client,
                10: self.set_phase(colors=RED),
                18: self.set_phase(inventory="In inventory"),
                26: lambda: self.app.pause_in_inventory.set(False),
            },
            ticks=34,
        )
        self.assertEqual(self.device.levels, [0.0, 0.75, 0.0, 0.75])

    def test_dynamic_buzzing_runs_during_monitoring(self):
        self.app.dynamic_buzz_enabled.set(True)
        self.run_ui(
            {0: self.app.connect_client, 10: self.set_phase(colors=RED)},
            ticks=30,
        )
        self.assertGreaterEqual(self.device.levels.count(0.75), 3)

    def test_dynamic_buzzing_ignores_green_even_with_green_level_set(self):
        self.app.dynamic_buzz_enabled.set(True)
        self.app.level_vars["green_limb"].set(0.5)
        self.run_ui({0: self.app.connect_client}, ticks=15)
        self.assertNotIn(0.5, self.device.levels)


class ConnectionTests(GuiTestCase):
    def test_second_connect_is_refused(self):
        self.run_ui(
            {0: self.app.connect_client, 5: self.app.connect_client},
            ticks=10,
        )
        self.assertEqual(len(_support.FakeClient.instances), 1)
        self.assertEqual(self.app.monitor_generation, 1)
        self.assertIn("Already connected", self.info.call_args_list[-1].args[1])

    def test_disconnect_stops_devices_and_monitor(self):
        self.run_ui(
            {
                0: self.app.connect_client,
                10: self.set_phase(colors=RED),
                18: self.app.disconnect,
            },
            ticks=28,
        )
        client = _support.FakeClient.instances[0]
        self.assertTrue(client.disconnected)
        self.assertEqual(self.device.levels[-1], 0.0)
        self.assertTrue(self.app.monitor_task.done())

    def test_reconnect_after_disconnect_runs_one_loop(self):
        tasks = []
        self.run_ui(
            {
                0: self.app.connect_client,
                4: lambda: tasks.append(self.app.monitor_task),
                5: self.app.disconnect,
                7: self.app.connect_client,
            },
            ticks=15,
        )
        self.assertEqual(len(_support.FakeClient.instances), 2)
        self.assertTrue(tasks[0].done(), "old monitor loop still running")
        self.assertIsNot(self.app.monitor_task, tasks[0])
        self.assertFalse(self.app.monitor_task.done())


class SliderTests(GuiTestCase):
    def test_slider_changes_output_level_immediately(self):
        self.app.level_vars["red_limb"].set(0.9)
        self.assertEqual(tb.get_vibration_for_limb("head", RED), 0.9)

    def test_reset_restores_default_levels(self):
        self.app.level_vars["red_limb"].set(0.9)
        self.app.reset_to_default()
        self.assertEqual(tb.get_vibration_for_limb("head", RED), 0.75)


if __name__ == "__main__":
    unittest.main()
