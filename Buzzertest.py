import asyncio
from buttplug.client import ButtplugClient
from buttplug import DeviceOutputCommand
from buttplug import OutputType
import mss
from PIL import Image
import io


#default vibration levels for each color
green_limb = 0.0
yellow_limb = 0.25
orange_limb = 0.5
red_limb = 0.75
black_limb = 1.0

# Track vibration level before inventory opened
_last_vibration_level = 0.0
_dead_override_active = True  # Start dead, assume player is not in raid when started (should figure itself out if player is in raid)



def get_all_limb_colors():
    """Capture colors from each limb location on screen and return RGB values."""
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        
        
        limb_locations = {
            "head": {"left": 96, "top": 31},            
            "thorax": {"left": 100, "top": 150},        
            "stomach": {"left": 100, "top": 200},        
            "right_arm": {"left": 50, "top": 150},       
            "left_arm": {"left": 150, "top": 150},      
            "right_leg": {"left": 50, "top": 250},       
            "left_leg": {"left": 150, "top": 250},
            "inventory": {"left": 1074, "top": 875},
            "dead": {"left": 1713, "top": 52},
            "dead_alt": {"left": 1703, "top": 1252},
            
        }
        
        colors = {}
        
        for limb, position in limb_locations.items():
            box = {
                "left": position["left"],
                "top": position["top"],
                "width": 3,
                "height": 3
            }
            
            # Capture screenshot
            screenshot = sct.grab(box)
            img = Image.frombytes("RGB", screenshot.size, screenshot.rgb)
            
            # Calculate average color
            pixels = list(img.getdata())
            avg_r = sum(p[0] for p in pixels) // len(pixels)
            avg_g = sum(p[1] for p in pixels) // len(pixels)
            avg_b = sum(p[2] for p in pixels) // len(pixels)
            
            colors[limb] = (avg_r, avg_g, avg_b)
        
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

def is_white(rgb_color, white_threshold=240):
    r, g, b = rgb_color
    return (r > white_threshold and 
            g > white_threshold and 
            b > white_threshold)


def inventory_color(rgb_color, white_threshold=240):
    return is_white(rgb_color, white_threshold)



    



def head(color):
       
        if is_green(color):
            print("Green detected - stopping vibration")
            return green_limb
        elif is_yellow(color):
            print("Yellow detected - low vibration")
            return yellow_limb
        elif is_orange(color):
            print("Orange detected - medium vibration")
            return orange_limb
        elif is_red(color):
            print("Red detected - high vibration")
            return red_limb
        elif is_black(color):
            print("Black detected - critical vibration")
            return black_limb
        else:
            print("No damage detected")
            return 0.0
        
        
def Thorax(color):
        if is_green(color):
            print("Green detected - stopping vibration")
            return green_limb
        elif is_yellow(color):
            print("Yellow detected - low vibration")
            return yellow_limb
        elif is_orange(color):
            print("Orange detected - medium vibration")
            return orange_limb
        elif is_red(color):
            print("Red detected - high vibration")
            return red_limb
        elif is_black(color):
            print("Black detected - critical vibration")
            return black_limb
        
def stomach(color):
        if is_green(color):
            print("Green detected - stopping vibration")
            return green_limb
        elif is_yellow(color):
            print("Yellow detected - low vibration")
            return yellow_limb
        elif is_orange(color):
            print("Orange detected - medium vibration")
            return orange_limb
        elif is_red(color):
            print("Red detected - high vibration")
            return red_limb
        elif is_black(color):
            print("Black detected - critical vibration")
            return black_limb
        
def RightArm(color):
        if is_green(color):
            print("Green detected - stopping vibration")
            return green_limb
        elif is_yellow(color):
            print("Yellow detected - low vibration")
            return yellow_limb
        elif is_orange(color):
            print("Orange detected - medium vibration")
            return orange_limb
        elif is_red(color):
            print("Red detected - high vibration")
            return red_limb
        elif is_black(color):
            print("Black detected - critical vibration")
            return black_limb
        
def LeftArm(color):
        if is_green(color):
            print("Green detected - stopping vibration")
            return green_limb
        elif is_yellow(color):
            print("Yellow detected - low vibration")
            return yellow_limb
        elif is_orange(color):
            print("Orange detected - medium vibration")
            return orange_limb
        elif is_red(color):
            print("Red detected - high vibration")
            return red_limb
        elif is_black(color):
            print("Black detected - critical vibration")
            return black_limb
      
def RightLeg(color):
        if is_green(color):
            print("Green detected - stopping vibration")
            return green_limb
        elif is_yellow(color):
            print("Yellow detected - low vibration")
            return yellow_limb
        elif is_orange(color):
            print("Orange detected - medium vibration")
            return orange_limb
        elif is_red(color):
            print("Red detected - high vibration")
            return red_limb
        elif is_black(color):
            print("Black detected - critical vibration")
            return black_limb
        
def LeftLeg(color):
        if is_green(color):
            print("Green detected - stopping vibration")
            return green_limb
        elif is_yellow(color):
            print("Yellow detected - low vibration")
            return yellow_limb
        elif is_orange(color):
            print("Orange detected - medium vibration")
            return orange_limb
        elif is_red(color):
            print("Red detected - high vibration")
            return red_limb
        elif is_black(color):
            print("Black detected - critical vibration")
            return black_limb
        

def inventory(color):    #Fix this its jank
    global _last_vibration_level
    
    if inventory_color(color) and head(color) == 0.0:
        # Inventory is open - health bar is hidden, return last detected vibration level
        print(f"Inventory open - maintaining previous vibration level: {_last_vibration_level}")
        return _last_vibration_level
    else:
        # Inventory is closed - detect health from visible indicator and update stored level
        print("Inventory closed - detecting health from visible bar")
        
        if is_red(color):
            print("Red detected - high vibration")
            _last_vibration_level = red_limb
            return red_limb
        elif is_orange(color):
            print("Orange detected - medium vibration")
            _last_vibration_level = orange_limb
            return orange_limb
        elif is_yellow(color):
            print("Yellow detected - low vibration")
            _last_vibration_level = yellow_limb
            return yellow_limb
        elif is_black(color):
            print("Black detected - critical vibration")
            _last_vibration_level = black_limb
            return black_limb
        elif is_green(color):
            print("Green detected - no vibration")
            _last_vibration_level = green_limb
            return green_limb
        else:
            print("No damage detected")
            _last_vibration_level = 0.0
            return 0.0

def dead(color, secondary_color, head_color):
    global _dead_override_active
    if _dead_override_active:
        return None
    if is_white(color) and is_white(secondary_color) and head(head_color) == black_limb:
        print("Dead detected - stopping vibration")
        _dead_override_active = True
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
    
    return None
        
    


async def main():
    # Test mode - just testing head color detection
    print("HEAD COLOR DETECTION TEST")
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

            # Normal mode: check if we died again
            dead_result = dead(colors["dead"], colors["dead_alt"], colors["head"])
            print(f"Dead samples: {colors['dead']} / {colors['dead_alt']}")
            print(f"Dead detector result: {dead_result}")

            if dead_result == 0.0:
                print("Dead detected - pausing limb/vibration detection")
                await asyncio.sleep(0.5)
                continue

            # Normal limb detection here
            head_color = colors["head"]
            print(f"Head RGB: {head_color}")
            damage_level = head(head_color)

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
    global _dead_override_active
    if _dead_override_active:
        return 0.0

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



