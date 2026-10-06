import asyncio
from buttplug.client import ButtplugClient
from buttplug import DeviceOutputCommand
from buttplug import OutputType
import cv2
import ctypes
import dxcam
import numpy as np


Reference_screen_width = 3440
Reference_screen_height = 1440
DETECTION_INSET = 4

CV_LIMB_REGIONS = {
    "head": (80, 30, 118, 72),
    "thorax": (75, 81, 111, 119),
}
PLACEHOLDER_LIMBS = ("stomach", "right_arm", "left_arm", "right_leg", "left_leg")
MIN_COLOUR_PERCENTAGE = 0.02
UNKNOWN_HOLD_SAMPLES = 3
COLOUR_CHANGE_SAMPLES = 2
DETECTED_COLOUR_RGB = {
    "green": (0, 255, 0),
    "yellow": (255, 255, 0),
    "red": (255, 0, 0),
    "black": (0, 0, 0),
}
COLOUR_LEVELS = {
    DETECTED_COLOUR_RGB["green"]: 0.0,
    DETECTED_COLOUR_RGB["yellow"]: 0.25,
    DETECTED_COLOUR_RGB["red"]: 0.75,
    DETECTED_COLOUR_RGB["black"]: 1.0,
}
_limb_detection_states = {}



#default vibration levels for each color
green_limb = 0.0
yellow_limb = 0.25
red_limb = 0.75
black_limb = 1.0

_last_vibration_level = 0.0
camera = dxcam.create(output_color="BGR")


def get_screen_resolution():
    return (
        ctypes.windll.user32.GetSystemMetrics(0),
        ctypes.windll.user32.GetSystemMetrics(1),
    )


def scale_region(region, actual_width, actual_height):
    left, top, right, bottom = region
    scale_x = actual_width / Reference_screen_width
    scale_y = actual_height / Reference_screen_height
    return (
        int(left * scale_x),
        int(top * scale_y),
        int(right * scale_x),
        int(bottom * scale_y),
    )


def inset_region(region, inset):
    left, top, right, bottom = region
    return left + inset, top + inset, right - inset, bottom - inset


def get_colour_percentages(frame):
    if frame is None or frame.size == 0:
        return {}

    if frame.shape[-1] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    r, g, b = cv2.split(rgb)
    hue, saturation, value = cv2.split(cv2.cvtColor(frame, cv2.COLOR_BGR2HSV))
    red_channel = r.astype(np.int16)
    green_channel = g.astype(np.int16)
    blue_channel = b.astype(np.int16)

    green = (
        (green_channel > red_channel + 20) &
        (green_channel > blue_channel + 20) &
        (green_channel >= 50)
    )
    yellow = (
        (hue >= 10) & (hue <= 35) &
        (saturation >= 60) & (value >= 80)
    )
    red = (
        ((hue < 10) | (hue > 170)) &
        (saturation >= 60) & (value >= 80)
    )
    black = (value < 130) & (saturation < 80)

    total_pixels = frame.shape[0] * frame.shape[1]
    return {
        "green": cv2.countNonZero(green.astype(np.uint8)) / total_pixels,
        "yellow": cv2.countNonZero(yellow.astype(np.uint8)) / total_pixels,
        "red": cv2.countNonZero(red.astype(np.uint8)) / total_pixels,
        "black": cv2.countNonZero(black.astype(np.uint8)) / total_pixels,
    }


def detect_limb_colour(frame, limb):
    percentages = get_colour_percentages(frame)

    detected_colour = None
    if percentages:
        colour, percentage = max(percentages.items(), key=lambda item: item[1])
        if percentage >= MIN_COLOUR_PERCENTAGE:
            detected_colour = colour

    state = _limb_detection_states.setdefault(
        limb,
        {"last": None, "unknown_samples": 0, "pending": None, "pending_samples": 0},
    )

    if detected_colour is None:
        state["unknown_samples"] += 1
        current_colour = (
            state["last"]
            if state["last"] is not None
            and state["unknown_samples"] <= UNKNOWN_HOLD_SAMPLES
            else None
        )
    else:
        state["unknown_samples"] = 0
        if detected_colour == state["last"]:
            state["pending"] = None
            state["pending_samples"] = 0
        else:
            if detected_colour == state["pending"]:
                state["pending_samples"] += 1
            else:
                state["pending"] = detected_colour
                state["pending_samples"] = 1

            if state["pending_samples"] >= COLOUR_CHANGE_SAMPLES:
                state["last"] = detected_colour
                state["pending"] = None
                state["pending_samples"] = 0

        current_colour = state["last"]

    return DETECTED_COLOUR_RGB.get(current_colour)


def get_all_limb_colors():
    screen_width, screen_height = get_screen_resolution()
    colors = {limb: None for limb in PLACEHOLDER_LIMBS}

    for limb, reference_region in CV_LIMB_REGIONS.items():
        scaled_region = scale_region(reference_region, screen_width, screen_height)
        detection_region = inset_region(
            scaled_region,
            DETECTION_INSET,
        )
        frame = camera.grab(region=detection_region)
        colors[limb] = detect_limb_colour(frame, limb)

    return colors

def get_damage_level_for_color(color):
    if color is None:
        return 0.0
    return COLOUR_LEVELS.get(tuple(color), 0.0)


def head(color):
    return get_damage_level_for_color(color)


def Thorax(color):
    return get_damage_level_for_color(color)


def stomach(color):
    return get_damage_level_for_color(color)


def RightArm(color):
    return get_damage_level_for_color(color)


def LeftArm(color):
    return get_damage_level_for_color(color)


def RightLeg(color):
    return get_damage_level_for_color(color)


def LeftLeg(color):
    return get_damage_level_for_color(color)
        











def calculate_damage_level(colors):
    global _last_vibration_level
    mapping = {
        "head": head,
        "thorax": Thorax,
        "stomach": stomach,
        "right_arm": RightArm,
        "left_arm": LeftArm,
        "right_leg": RightLeg,
        "left_leg": LeftLeg,
    }
    levels = [fn(colors[limb]) for limb, fn in mapping.items() if limb in colors]
    _last_vibration_level = max(levels) if levels else 0.0
    return _last_vibration_level


async def main():
    print("CV LIMB COLOR DETECTION TEST")
    print("Press Ctrl+C to stop")
    
    

    try:
        while True:
            colors = get_all_limb_colors()
            damage_level = calculate_damage_level(colors)
            print(f"Calculated normal vibration level: {damage_level:.2f}")
            print(f"CV limb colors: {colors}")

            await asyncio.sleep(0.5)
            
            
    except KeyboardInterrupt:
        print("\nTest stopped by user")
    
    # # COMMENTED OUT CLEANUP
    # await selected_device.run_output(DeviceOutputCommand(OutputType.VIBRATE, 0.0))
    # await client.disconnect()
    print("Test ended - goodbye!")
if __name__ == "__main__":    asyncio.run(main())


# Helper for external modules to map CV-classified limb colours to vibration levels.
def get_vibration_for_limb(limb_name, color):
    """Return the vibration level for a CV-classified limb colour.

    limb_name: one of 'head','thorax','stomach','right_arm','left_arm','right_leg','left_leg'
    color: canonical RGB tuple, or None when unknown/unimplemented
    """
    mapping = {
        "head": head,
        "thorax": Thorax,
        "stomach": stomach,
        "right_arm": RightArm,
        "left_arm": LeftArm,
        "right_leg": RightLeg,
        "left_leg": LeftLeg,
    }
    fn = mapping.get(limb_name.lower())
    if fn is None:
        raise ValueError(f"Unknown limb: {limb_name}")
    return fn(color)

# Expose available limb keys for a UI
LIMB_KEYS = ["head", "thorax", "stomach", "right_arm", "left_arm", "right_leg", "left_leg"]


def calculate_dynamic_buzz_pattern(limb_levels_dict):
    """
    Generate a dynamic buzz pattern based on multiple limbs' damage levels.
    
    limb_levels_dict: dict of {limb_name: vibration_level} where vibration_level is 0.0-1.0
                     green = 0.0 (no damage, ignored in pattern)
    
    Returns: list of (intensity, duration_ms) tuples representing pulse pattern
    
    Pattern logic:
    - Green-only damage produces empty pattern (no buzzing)
    - Single non-green damage: simple pulse
    - Multiple damage: intensity and frequency based on severity and count
    """
    # Filter out green (0.0) damage - we don't create patterns for it
    non_green_levels = {k: v for k, v in limb_levels_dict.items() if v > 0.0}
    
    if not non_green_levels:
        # No damage, return empty pattern
        return []
    
    # Calculate stats from non-green damages
    num_damages = len(non_green_levels)
    max_level = max(non_green_levels.values())
    avg_level = sum(non_green_levels.values()) / num_damages
    
    # Base pulse cycle times (in milliseconds)
    base_on_time = 150
    base_off_time = 100
    
    # Adjust pulse frequency based on damage count and severity
    # More damage = faster pulsing; higher severity = longer on-time
    on_time = base_on_time + int(max_level * 100)  # Critical damage has longer pulses
    off_time = max(50, base_off_time - int(num_damages * 15))  # More damage = shorter gaps
    
    # Number of pulses based on severity
    num_pulses = 2 + num_damages  # At least 2 pulses, more for multiple damages
    
    # Build pattern: alternate on and off
    pattern = []
    for i in range(num_pulses):
        pattern.append((max_level, on_time))  # Pulse on at max damage level
        if i < num_pulses - 1:  # Don't add final off-time
            pattern.append((0.0, off_time))  # Pulse off
    
    return pattern


async def run_dynamic_buzz_pattern(device, pattern):
    """
    Send a dynamic buzz pattern to a device.
    
    device: ButtplugClient device
    pattern: list of (intensity, duration_ms) tuples
    """
    from buttplug import DeviceOutputCommand, OutputType
    
    if not pattern:
        # No pattern, send stop command
        await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, 0.0))
        return
    
    for intensity, duration in pattern:
        await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, float(intensity)))
        await asyncio.sleep(duration / 1000.0)  # Convert ms to seconds
    
    # Ensure device stops after pattern
    await device.run_output(DeviceOutputCommand(OutputType.VIBRATE, 0.0))



