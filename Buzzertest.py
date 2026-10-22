import asyncio
import time
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
RAID_OUTCOME_REGION = (1440, 845, 1775, 930)
RAID_OUTCOME_CONFIRM_SAMPLES = 3
RAID_OUTCOME_SAMPLE_INTERVAL = 0.5
RAID_RED_MIN_SATURATION = 200
RAID_RED_MIN_VALUE = 140
RAID_MIN_RED_RATIO = 0.25
SURVIVED_GREEN_HUE_RANGE = (35, 85)
SURVIVED_GREEN_MIN_SATURATION = 100
SURVIVED_GREEN_MIN_VALUE = 100
SURVIVED_MIN_GREEN_RATIO = 0.25
RUN_THROUGH_WHITE_MAX_SATURATION = 70
RUN_THROUGH_WHITE_MIN_VALUE = 200
RUN_THROUGH_MIN_WHITE_RATIO = 0.25
RAID_MIN_BADGE_WIDTH_RATIO = 0.60
RAID_MIN_BADGE_HEIGHT_RATIO = 0.35
RAID_BADGE_ASPECT_RANGE = (3.7, 5.2)
RAID_MIN_BADGE_FILL_RATIO = 0.85
RUN_THROUGH_MIN_BADGE_FILL_RATIO = 0.65
RAID_MIN_BADGE_DETAIL_RATIO = 0.005
RAID_MAX_BADGE_CENTER_OFFSET = 0.15

HEALTH_SAMPLE_REGIONS = {
    "head": ((80, 27, 118, 72), 0),
    "thorax": ((75, 75, 111, 113), 0),
    "stomach": ((70, 125, 116, 163), 0),
    "right_arm": ((127, 75, 147, 168), -21),
    "left_arm": ((46, 75, 62, 168), 15),
    "right_leg": ((110, 165, 130, 285), -12),
    "left_leg": ((59, 165, 79, 285), 3),
}
HEALTH_MIN_COLOUR_PERCENTAGE = 0.02
HEALTH_TORSO_MIN_COLOUR_PERCENTAGE = 0.003

CV_LIMB_REGIONS = {
    "head": (80, 30, 118, 72),
    "thorax": (75, 95, 111, 133),
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
_raid_outcome_state = {
    "confirmed": None,
    "pending": None,
    "pending_samples": 0,
    "last_sample_time": 0.0,
}


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


def _raid_badge_detected(
    frame,
    hue_ranges,
    minimum_saturation,
    minimum_value,
    minimum_color_ratio,
    maximum_saturation=255,
    minimum_fill_ratio=RAID_MIN_BADGE_FILL_RATIO,
):
    empty_metrics = {
        "color_ratio": 0.0,
        "aspect": 0.0,
        "fill": 0.0,
        "detail": 0.0,
        "center_offset": 1.0,
        "width_ratio": 0.0,
        "height_ratio": 0.0,
    }
    if frame is None or frame.size == 0:
        return False, empty_metrics

    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    color_mask = np.zeros(hsv.shape[:2], dtype=np.uint8)
    for hue_min, hue_max in hue_ranges:
        color_mask |= cv2.inRange(
            hsv,
            np.array([hue_min, minimum_saturation, minimum_value]),
            np.array([hue_max, maximum_saturation, 255]),
        )
    color_ratio = cv2.countNonZero(color_mask) / color_mask.size
    component_count, _, stats, _ = cv2.connectedComponentsWithStats(
        color_mask,
        connectivity=8,
    )
    if component_count <= 1:
        empty_metrics["color_ratio"] = color_ratio
        return False, empty_metrics

    component_index = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    x, y, width, height, area = stats[component_index]
    if width == 0 or height == 0:
        empty_metrics["color_ratio"] = color_ratio
        return False, empty_metrics

    fill_ratio = area / (width * height)
    detail_ratio = 1.0 - fill_ratio
    aspect_ratio = width / height
    width_ratio = width / frame.shape[1]
    height_ratio = height / frame.shape[0]
    center_offset = max(
        abs((x + width / 2) - frame.shape[1] / 2) / frame.shape[1],
        abs((y + height / 2) - frame.shape[0] / 2) / frame.shape[0],
    )
    metrics = {
        "color_ratio": color_ratio,
        "aspect": aspect_ratio,
        "fill": fill_ratio,
        "detail": detail_ratio,
        "center_offset": center_offset,
        "width_ratio": width_ratio,
        "height_ratio": height_ratio,
    }
    detected = (
        color_ratio >= minimum_color_ratio
        and width_ratio >= RAID_MIN_BADGE_WIDTH_RATIO
        and height_ratio >= RAID_MIN_BADGE_HEIGHT_RATIO
        and RAID_BADGE_ASPECT_RANGE[0] <= aspect_ratio <= RAID_BADGE_ASPECT_RANGE[1]
        and fill_ratio >= minimum_fill_ratio
        and detail_ratio >= RAID_MIN_BADGE_DETAIL_RATIO
        and center_offset <= RAID_MAX_BADGE_CENTER_OFFSET
    )
    return detected, metrics


def _detect_raid_badge(frame, outcome):
    if outcome == "kia":
        detected, metrics = _raid_badge_detected(
            frame,
            ((0, 10), (170, 180)),
            RAID_RED_MIN_SATURATION,
            RAID_RED_MIN_VALUE,
            RAID_MIN_RED_RATIO,
        )
    elif outcome == "survived":
        detected, metrics = _raid_badge_detected(
            frame,
            (SURVIVED_GREEN_HUE_RANGE,),
            SURVIVED_GREEN_MIN_SATURATION,
            SURVIVED_GREEN_MIN_VALUE,
            SURVIVED_MIN_GREEN_RATIO,
        )
    elif outcome == "run_through":
        detected, metrics = _raid_badge_detected(
            frame,
            ((0, 180),),
            0,
            RUN_THROUGH_WHITE_MIN_VALUE,
            RUN_THROUGH_MIN_WHITE_RATIO,
            maximum_saturation=RUN_THROUGH_WHITE_MAX_SATURATION,
            minimum_fill_ratio=RUN_THROUGH_MIN_BADGE_FILL_RATIO,
        )
    else:
        raise ValueError(f"Unknown raid outcome: {outcome}")

    metrics[f"{outcome}_ratio"] = metrics.pop("color_ratio")
    return detected, metrics


def get_raid_outcome():
    """Return a raid outcome after three matching samples, or None while checking."""
    now = time.monotonic()
    if now - _raid_outcome_state["last_sample_time"] < RAID_OUTCOME_SAMPLE_INTERVAL:
        return _raid_outcome_state["confirmed"]
    _raid_outcome_state["last_sample_time"] = now

    screen_width, screen_height = get_screen_resolution()
    scaled_region = scale_region(
        RAID_OUTCOME_REGION,
        screen_width,
        screen_height,
    )
    frame = camera.grab(region=inset_region(scaled_region, DETECTION_INSET))
    if frame is None:
        return _raid_outcome_state["confirmed"]

    detected_outcomes = [
        outcome
        for outcome in ("kia", "survived", "run_through")
        if _detect_raid_badge(frame, outcome)[0]
    ]
    if len(detected_outcomes) > 1:
        candidate = "ambiguous"
    elif detected_outcomes:
        candidate = detected_outcomes[0]
    else:
        candidate = "none"

    if candidate == _raid_outcome_state["pending"]:
        _raid_outcome_state["pending_samples"] += 1
    else:
        _raid_outcome_state["pending"] = candidate
        _raid_outcome_state["pending_samples"] = 1

    if _raid_outcome_state["pending_samples"] >= RAID_OUTCOME_CONFIRM_SAMPLES:
        _raid_outcome_state["confirmed"] = candidate
    return _raid_outcome_state["confirmed"]


def reset_raid_outcome_detection():
    _raid_outcome_state.update({
        "confirmed": None,
        "pending": None,
        "pending_samples": 0,
        "last_sample_time": 0.0,
    })


def _rotated_region_points(region, angle_degrees):
    left, top, right, bottom = region
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    half_width = (right - left) / 2
    half_height = (bottom - top) / 2
    angle = np.deg2rad(angle_degrees)
    width_axis = (np.cos(angle), np.sin(angle))
    height_axis = (-np.sin(angle), np.cos(angle))
    return [
        (
            center_x - half_width * width_axis[0] - half_height * height_axis[0],
            center_y - half_width * width_axis[1] - half_height * height_axis[1],
        ),
        (
            center_x + half_width * width_axis[0] - half_height * height_axis[0],
            center_y + half_width * width_axis[1] - half_height * height_axis[1],
        ),
        (
            center_x + half_width * width_axis[0] + half_height * height_axis[0],
            center_y + half_width * width_axis[1] + half_height * height_axis[1],
        ),
        (
            center_x - half_width * width_axis[0] + half_height * height_axis[0],
            center_y - half_width * width_axis[1] + half_height * height_axis[1],
        ),
    ]


def _rectified_health_sample(region, angle_degrees, screen_width, screen_height):
    scaled_region = scale_region(region, screen_width, screen_height)
    inset_sample = inset_region(scaled_region, DETECTION_INSET)
    if angle_degrees == 0:
        return camera.grab(region=inset_sample)

    points = _rotated_region_points(inset_sample, angle_degrees)
    capture_region = (
        int(np.floor(min(point[0] for point in points))),
        int(np.floor(min(point[1] for point in points))),
        int(np.ceil(max(point[0] for point in points))),
        int(np.ceil(max(point[1] for point in points))),
    )
    frame = camera.grab(region=capture_region)
    if frame is None:
        return None

    source_points = np.array(
        [(x - capture_region[0], y - capture_region[1]) for x, y in points],
        dtype=np.float32,
    )
    output_width = inset_sample[2] - inset_sample[0]
    output_height = inset_sample[3] - inset_sample[1]
    destination_points = np.array(
        [
            (0, 0),
            (output_width - 1, 0),
            (output_width - 1, output_height - 1),
            (0, output_height - 1),
        ],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(source_points, destination_points)
    return cv2.warpPerspective(frame, transform, (output_width, output_height))


def is_full_body_green():
    """Sample all tuned health regions and return True only when each is green."""
    screen_width, screen_height = get_screen_resolution()
    for limb, (region, angle_degrees) in HEALTH_SAMPLE_REGIONS.items():
        frame = _rectified_health_sample(
            region,
            angle_degrees,
            screen_width,
            screen_height,
        )
        minimum_percentage = (
            HEALTH_TORSO_MIN_COLOUR_PERCENTAGE
            if limb == "thorax" or angle_degrees != 0
            else HEALTH_MIN_COLOUR_PERCENTAGE
        )
        detected_level = detect_health_level(
            frame,
            minimum_percentage=minimum_percentage,
            prefer_chromatic=(angle_degrees != 0),
        )
        if detected_level != 0.0:
            return False
    return True


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


def detect_health_level(frame, minimum_percentage, prefer_chromatic=False):
    percentages = get_colour_percentages(frame)
    if not percentages:
        return None

    if prefer_chromatic:
        chromatic_candidates = {
            0.0: percentages["green"],
            0.25: percentages["yellow"],
            0.5: percentages["red"],
        }
        level, percentage = max(
            chromatic_candidates.items(),
            key=lambda item: item[1],
        )
        if percentage >= minimum_percentage:
            return level

    candidates = {
        0.0: percentages["green"],
        0.25: percentages["yellow"],
        0.5: percentages["red"],
        1.0: percentages["black"],
    }
    level, percentage = max(candidates.items(), key=lambda item: item[1])
    return level if percentage >= minimum_percentage else None


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
        detection_region = inset_region(scaled_region, DETECTION_INSET)
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

