"""Shared test setup: stub out screen capture and Buttplug before import.

Import this module before TarkBuzz or TarkBuzz_gui in every test module.
"""
import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

camera_creations = []


class DeviceOutputCommand:
    def __init__(self, output_type, level):
        self.output_type = output_type
        self.level = level


class FakeDevice:
    """Records every vibration level it is sent."""

    def __init__(self, name="Fake device"):
        self.name = name
        self.levels = []

    async def run_output(self, command):
        self.levels.append(command.level)


class FakeClient:
    """Stands in for ButtplugClient; tests set FakeClient.devices first."""

    devices = {}
    instances = []

    def __init__(self, name):
        self.devices = dict(FakeClient.devices)
        self.connected_to = None
        self.disconnected = False
        FakeClient.instances.append(self)

    async def connect(self, url):
        self.connected_to = url

    async def disconnect(self):
        self.disconnected = True
        self.levels_at_disconnect = {
            key: len(device.levels) for key, device in self.devices.items()
        }


if "TarkBuzz" not in sys.modules:
    dxcam = types.ModuleType("dxcam")

    def _create(**kwargs):
        camera_creations.append(kwargs)
        return None

    dxcam.create = _create
    buttplug = types.ModuleType("buttplug")
    buttplug.DeviceOutputCommand = DeviceOutputCommand
    buttplug.OutputType = types.SimpleNamespace(VIBRATE="vibrate")
    buttplug_client = types.ModuleType("buttplug.client")
    buttplug_client.ButtplugClient = FakeClient
    buttplug.client = buttplug_client
    sys.modules["dxcam"] = dxcam
    sys.modules["buttplug"] = buttplug
    sys.modules["buttplug.client"] = buttplug_client
