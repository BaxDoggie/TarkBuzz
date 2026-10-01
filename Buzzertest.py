import asyncio
from buttplug.client import ButtplugClient
from buttplug import DeviceOutputCommand
from buttplug import OutputType
import cv2
import mss
import numpy as np


Reference_screen_width = 3440
Reference_screen_height = 1440

# Head and thorax use the tuned CV-test regions; other ROIs start from the old sample coordinates.
LIMB_REGIONS = {
    "head": (80, 30, 118, 72),
    "thorax": (75, 81, 111, 119),
    "stomach": (81, 179, 119, 221),
    "right_arm": (31, 129, 69, 171),
    "left_arm": (131, 129, 169, 171),
    "right_leg": (31, 229, 69, 271),
    "left_leg": (131, 229, 169, 271),
}
MIN_COLOUR_PERCENTAGE = 0.02
UNKNOWN_HOLD_SAMPLES = 3
COLOUR_CHANGE_SAMPLES = 2
DETECTED_COLOUR_RGB = {
    "green": (0, 255, 0),
    "yellow": (255, 255, 0),
    "red": (255, 0, 0),
    "black": (0, 0, 0),
}
UNKNOWN_COLOUR_RGB = (255, 255, 255)
_limb_detection_states = {}



#default vibration levels for each color
green_limb = 0.0
yellow_limb = 0.25
orange_limb = 0.5
red_limb = 0.75
black_limb = 1.0

# Track vibration level before inventory opened
_last_vibration_level = 0.0
_dead_override_active = True  # Start dead, assume player is not in raid when started (should figure itself out if player is in raid)
_inventory_override_active = False  # Track if inventory is open to pause vibration


def get_screen_resolution():
    """Get the current screen resolution."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]  # Primary monitor
        return monitor["width"], monitor["height"]

def scale_coordinates(x, y, actual_width=None, actual_height=None):
    if actual_width is None or actual_height is None:
        actual_width, actual_height = get_screen_resolution()
    scaled_x = int(x * actual_width / Reference_screen_width)
    scaled_y = int(y * actual_height / Reference_screen_height)
    return {"left": round(scaled_x), "top": round(scaled_y)}

def get_scaled_limb_locations(actual_width=None, actual_height=None):
    """Return a dictionary of limb locations scaled to the current screen resolution."""
    if actual_width is None or actual_height is None:
        actual_width, actual_height = get_screen_resolution()

    limb_locations = {
        "head": {"left": 96, "top": 31},            
        "thorax": {"left": 100, "top": 150},        
        "stomach": {"left": 100, "top": 200},        
        "right_arm": {"left": 50, "top": 150},       
        "left_arm": {"left": 150, "top": 150},      
        "right_leg": {"left": 50, "top": 250},       
        "left_leg": {"left": 150, "top": 250},
        "inventory": {"left": 1074, "top": 875},
        "inventory_alt": {"left": 40, "top": 1357},
        "dead": {"left": 1713, "top": 52},
        "dead_alt": {"left": 1770, "top": 1328},
    }
    
    scaled_locations = {
        limb: scale_coordinates(pos["left"], pos["top"], actual_width, actual_height)
        for limb, pos in limb_locations.items()
    }
    return scaled_locations

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

    return DETECTED_COLOUR_RGB.get(current_colour, UNKNOWN_COLOUR_RGB)


def get_all_limb_colors():
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        screenshot = sct.grab(monitor)
        screen = np.frombuffer(screenshot.bgra, dtype=np.uint8).reshape(
            screenshot.height,
            screenshot.width,
            4,
        )
        screen = cv2.cvtColor(screen, cv2.COLOR_BGRA2BGR)
        limb_locations = get_scaled_limb_locations(
            monitor["width"],
            monitor["height"],
        )
        colors = {}

        for limb, reference_region in LIMB_REGIONS.items():
            left, top, right, bottom = scale_region(
                reference_region,
                monitor["width"],
                monitor["height"],
            )
            roi = screen[top:bottom, left:right]
            colors[limb] = detect_limb_colour(roi, limb)

        control_locations = ("inventory", "inventory_alt", "dead", "dead_alt")
        for control in control_locations:
            position = limb_locations[control]
            left = position["left"]
            top = position["top"]
            sample = screen[top:top + 3, left:left + 3]
            if sample.size:
                rgb_sample = cv2.cvtColor(sample, cv2.COLOR_BGR2RGB)
                colors[control] = tuple(
                    int(channel)
                    for channel in rgb_sample.mean(axis=(0, 1))
                )
            else:
                colors[control] = UNKNOWN_COLOUR_RGB

        return colors

def is_red(rgb_color, red_threshold=150, tolerance=100):
    """
    Detect if a color is red.
    Red should have high R value, low G and B values.
    red_threshold: minimum value for red channel (0-255)
    tolerance: how much G and B can deviate from 0
    """
    r, g, b = rgb_color
    
    # Red detection: R is high, G and B are low
    return (r > red_threshold and 
            g < tolerance and 
            b < tolerance)

def is_yellow(rgb_color, red_threshold=150, green_threshold=150, tolerance=100):
    """
    Detect if a color is yellow.
    Yellow should have high R and G values, low B value.
    red_threshold: minimum value for red channel (0-255)
    green_threshold: minimum value for green channel (0-255)
    tolerance: how much B can deviate from 0
    """
    r, g, b = rgb_color
    
    # Yellow detection: R and G are high, B is low
    return (r > red_threshold and 
            g > green_threshold and 
            b < tolerance)

def is_green(rgb_color, green_threshold=145, tolerance=45):
    r, g, b = rgb_color
    return (g > green_threshold and 
            r < tolerance and 
            b < tolerance)

def is_orange(rgb_color, red_threshold=150, green_threshold=100, tolerance=100):
    r, g, b = rgb_color
    # Orange detection: R is high, G is moderate, B is low
    return (r > red_threshold and 
            g > green_threshold and 
            b < tolerance)

def is_black(rgb_color, threshold=50):
    r, g, b = rgb_color
    # Black detection: R, G, and B are all low
    return (r < threshold and 
            g < threshold and 
            b < threshold)

def is_white(rgb_color, white_threshold=225):
    r, g, b = rgb_color
    return (r > white_threshold and 
            g > white_threshold and 
            b > white_threshold)





    



def get_damage_level_for_color(color):
    if is_green(color):
        print("Green detected - stopping vibration")
        return green_limb
    if is_yellow(color):
        print("Yellow detected - low vibration")
        return yellow_limb
    if is_orange(color):
        print("Orange detected - medium vibration")
        return orange_limb
    if is_red(color):
        print("Red detected - high vibration")
        return red_limb
    if is_black(color):
        print("Black detected - critical vibration")
        return black_limb
    print("No damage detected")
    return 0.0


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
        

def inventory_open(color, inventory_alt_color):
    return is_white(color) and not is_white(inventory_alt_color)






def handle_inventory(inventory_color, inventory_alt_color):
    global _inventory_override_active
    if inventory_open(inventory_color, inventory_alt_color):
        _inventory_override_active = True
        return _last_vibration_level
    _inventory_override_active = False
    return None








def dead(color, secondary_color, head_color):
    global _dead_override_active
    if is_white(color) and is_white(secondary_color) and head(head_color) == black_limb:
        print("Dead detected - stopping vibration")
        _dead_override_active = True
        return 0.0
    if _dead_override_active:
        return 0.0
    return None





def alive(head_color, left_leg_color, right_leg_color):
    global _dead_override_active

    if not _dead_override_active:
        return False  # Already alive

    if head(head_color) != black_limb and LeftLeg(left_leg_color) != black_limb and RightLeg(right_leg_color) != black_limb:
        print("Player alive - resuming normal detector flow")
        _dead_override_active = False
        return True
    
    return False


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

            # If we are in dead override, only check alive
            if _dead_override_active:
                if alive(colors["head"], colors["left_leg"], colors["right_leg"]):
                    print("Alive detected, resuming normal detection")
                else:
                    print("Still dead; skipping limb detection")
                    await asyncio.sleep(0.5)
                    continue


            inventory_result = handle_inventory(colors["inventory"], colors["inventory_alt"])
            if inventory_result is not None:
                print("Inventory open - pausing limb/vibration detection")
                await asyncio.sleep(0.5)
                continue



            # Normal mode: check if we died again
            dead_result = dead(colors["dead"], colors["dead_alt"], colors["head"])
            print(f"Dead samples: {colors['dead']} / {colors['dead_alt']}")
            print(f"Dead detector result: {dead_result}")

            if dead_result == 0.0:
                print("Dead detected - pausing limb/vibration detection")
                await asyncio.sleep(0.5)
                continue

            # Normal limb detection here
            damage_level = calculate_damage_level(colors)
            print(f"Calculated normal vibration level: {damage_level:.2f}")
            print(f"Sampled limb colors: {colors}")

            await asyncio.sleep(0.5)
            
            
    except KeyboardInterrupt:
        print("\nTest stopped by user")
    
    # # COMMENTED OUT CLEANUP
    # await selected_device.run_output(DeviceOutputCommand(OutputType.VIBRATE, 0.0))
    # await client.disconnect()
    print("Test ended - goodbye!")
if __name__ == "__main__":    asyncio.run(main())


# Helper for external modules (GUI) to evaluate vibration level from limb name and sampled color
def get_vibration_for_limb(limb_name, color):
    """Return vibration level (0.0-1.0) for a limb given its RGB color tuple.

    limb_name: one of 'head','thorax','stomach','right_arm','left_arm','right_leg','left_leg'
    color: (r,g,b)
    """
    global _dead_override_active, _inventory_override_active
    if _dead_override_active:
        return 0.0
    if _inventory_override_active:
        return _last_vibration_level

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



