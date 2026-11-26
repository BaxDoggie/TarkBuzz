import time
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

CV_LIMB_REGIONS = {
    "head": ((80, 27, 118, 72), 0),
    "thorax": ((75, 75, 111, 113), 0),
    "stomach": ((70, 125, 116, 163), 0),
    "right_arm": ((127, 75, 147, 168), -21),
    "left_arm": ((46, 75, 62, 168), 15),
    "right_leg": ((110, 165, 130, 285), -12),
    "left_leg": ((59, 165, 79, 285), 3),
}
MIN_COLOUR_PERCENTAGE = 0.003
UNKNOWN_HOLD_SAMPLES = 8
COLOUR_CHANGE_SAMPLES = 2
LIMB_SAMPLE_INTERVAL = 0.5
INVENTORY_TAB_REGIONS = {
    "OVERALL": (455, 5, 685, 58),
    "GEAR": (685, 5, 950, 58),
    "HEALTH": (950, 5, 1165, 58),
    "SKILLS": (1165, 5, 1400, 58),
    "MAP": (1400, 5, 1645, 58),
    "TASKS": (1645, 5, 1900, 58),
    "ACHIEVEMENTS": (1900, 5, 2105, 58),
    "PRESTIGE": (2105, 5, 2310, 58),
}
INVENTORY_MIN_TAB_WHITE_RATIO = 0.30
INVENTORY_MIN_TAB_CONTRAST = 0.20
INVENTORY_MIN_TAB_BAR_DARK_RATIO = 0.15
INVENTORY_CONFIRM_SAMPLES = 3
INVENTORY_SAMPLE_INTERVAL = 0.5
_DAMAGE_COLOURS_BY_LEVEL = {
    0.0: "green",
    0.25: "yellow",
    0.5: "red",
    1.0: "black",
}
DETECTED_COLOUR_RGB = {
    "green": (0, 255, 0),
    "yellow": (255, 255, 0),
    "red": (255, 0, 0),
    "black": (0, 0, 0),
}
_limb_detection_states = {}
_last_limb_sample_time = 0.0
_inventory_detection_state = {
    "confirmed": None,
    "pending": None,
    "pending_samples": 0,
    "last_sample_time": 0.0,
}



# Vibration level for each detected colour. Read at call time, so the GUI
# can change these through set_colour_levels() while monitoring is running.
DEFAULT_COLOUR_LEVELS = {
    "green_limb": 0.0,
    "yellow_limb": 0.25,
    "red_limb": 0.75,
    "black_limb": 1.0,
}
COLOUR_LEVEL_KEYS = tuple(DEFAULT_COLOUR_LEVELS)
green_limb = DEFAULT_COLOUR_LEVELS["green_limb"]
yellow_limb = DEFAULT_COLOUR_LEVELS["yellow_limb"]
red_limb = DEFAULT_COLOUR_LEVELS["red_limb"]
black_limb = DEFAULT_COLOUR_LEVELS["black_limb"]


def set_colour_levels(levels):
    """Update vibration levels from a {'green_limb': 0.0, ...} dict."""
    for key, value in levels.items():
        if key not in COLOUR_LEVEL_KEYS:
            raise ValueError(f"Unknown colour level: {key}")
        globals()[key] = min(1.0, max(0.0, float(value)))

_camera = None
_raid_outcome_state = {
    "confirmed": None,
    "pending": None,
    "pending_samples": 0,
    "last_sample_time": 0.0,
}


def enable_dpi_awareness():
    """Make Windows report real pixel sizes, matching what dxcam captures.

    Without this, on a scaled display (125%, 150%, ...) GetSystemMetrics
    returns a smaller, scaled resolution and every region is misplaced.
    Must run before any window is created.
    """
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


enable_dpi_awareness()


def get_camera():
    """Create the dxcam capture on first use instead of at import time."""
    global _camera
    if _camera is None:
        _camera = dxcam.create(output_color="BGR")
    return _camera


_last_frames = {}


def _grab(region):
    """Capture a screen region, reusing the last capture if nothing changed.

    dxcam returns None when nothing on screen changed since the last
    capture. A still screen (such as the raid results screen with the mouse
    not moving) would then never give the repeated samples the detectors
    need. dxcam's own new_frame_only=False fallback only remembers the
    single most recent region, and TarkBuzz alternates between many
    regions, so keep one cached frame per region here instead.
    """
    region = tuple(int(v) for v in region)
    frame = get_camera().grab(region=region)
    if frame is not None:
        _last_frames[region] = frame
        return frame
    cached = _last_frames.get(region)
    return None if cached is None else cached.copy()


def get_screen_resolution():
    return (
        ctypes.windll.user32.GetSystemMetrics(0),
        ctypes.windll.user32.GetSystemMetrics(1),
    )


def scale_region(region, actual_width, actual_height):
    """Scale a region tuned at 3440x1440 to the current screen.

    Matches the detection test bench: width and height are scaled
    separately.
    """
    left, top, right, bottom = region
    scale_x = actual_width / Reference_screen_width
    scale_y = actual_height / Reference_screen_height
    return (
        int(left * scale_x),
        int(top * scale_y),
        int(right * scale_x),
        int(bottom * scale_y),
    )


def detection_inset(actual_width, actual_height):
    """DETECTION_INSET scaled to the current screen.

    The inset was tuned at 3440x1440 along with the regions, so it shrinks
    with them; otherwise the thin arm and leg regions collapse to nothing
    on smaller screens. Exactly DETECTION_INSET at the reference size.
    """
    scale = min(
        actual_width / Reference_screen_width,
        actual_height / Reference_screen_height,
    )
    return max(1, round(DETECTION_INSET * scale))


def inset_region(region, inset):
    """Shrink a region by inset on every side, always leaving at least 1 px."""
    left, top, right, bottom = region
    inset_x = max(0, min(inset, (right - left - 1) // 2))
    inset_y = max(0, min(inset, (bottom - top - 1) // 2))
    return left + inset_x, top + inset_y, right - inset_x, bottom - inset_y


def _region_bounds(regions):
    return (
        min(region[0] for region in regions),
        min(region[1] for region in regions),
        max(region[2] for region in regions),
        max(region[3] for region in regions),
    )


def _sample_region(frame, capture_region, sample_region, inset):
    left, top, right, bottom = inset_region(sample_region, inset)
    capture_left, capture_top, _, _ = capture_region
    return frame[
        top - capture_top:bottom - capture_top,
        left - capture_left:right - capture_left,
    ]


def _bright_neutral_ratio(frame, capture_region, sample_region, inset):
    sample = _sample_region(frame, capture_region, sample_region, inset)
    if sample.size == 0:
        return 0.0

    darkest_channel = sample.min(axis=2)
    brightest_channel = sample.max(axis=2)
    bright_neutral = (
        (darkest_channel > 170)
        & ((brightest_channel - darkest_channel) < 70)
    )
    return cv2.countNonZero(bright_neutral.astype(np.uint8)) / bright_neutral.size


def _dark_pixel_ratio(frame, capture_region, sample_region, inset):
    sample = _sample_region(frame, capture_region, sample_region, inset)
    if sample.size == 0:
        return 0.0

    grayscale = cv2.cvtColor(sample, cv2.COLOR_BGR2GRAY)
    dark_pixels = grayscale < 100
    return cv2.countNonZero(dark_pixels.astype(np.uint8)) / dark_pixels.size


def get_inventory_status():
    """Return Checking, In inventory, or Not in inventory."""
    state = _inventory_detection_state
    now = time.monotonic()
    if now - state["last_sample_time"] >= INVENTORY_SAMPLE_INTERVAL:
        state["last_sample_time"] = now
        screen_width, screen_height = get_screen_resolution()
        scaled_tabs = {
            name: scale_region(region, screen_width, screen_height)
            for name, region in INVENTORY_TAB_REGIONS.items()
        }
        inset = detection_inset(screen_width, screen_height)
        capture_region = _region_bounds(list(scaled_tabs.values()))
        frame = _grab(capture_region)
        if frame is not None:
            tab_scores = {
                name: _bright_neutral_ratio(
                    frame,
                    capture_region,
                    region,
                    inset=inset,
                )
                for name, region in scaled_tabs.items()
            }
            ranked_tabs = sorted(
                tab_scores.items(),
                key=lambda item: item[1],
                reverse=True,
            )
            selected_tab_ratio = ranked_tabs[0][1]
            tab_contrast = selected_tab_ratio - ranked_tabs[1][1]
            tab_bar_dark_ratio = _dark_pixel_ratio(
                frame,
                capture_region,
                capture_region,
                inset=inset,
            )
            detected_inventory = (
                selected_tab_ratio >= INVENTORY_MIN_TAB_WHITE_RATIO
                and tab_contrast >= INVENTORY_MIN_TAB_CONTRAST
                and tab_bar_dark_ratio >= INVENTORY_MIN_TAB_BAR_DARK_RATIO
            )

            if detected_inventory == state["pending"]:
                state["pending_samples"] += 1
            else:
                state["pending"] = detected_inventory
                state["pending_samples"] = 1

            if state["pending_samples"] >= INVENTORY_CONFIRM_SAMPLES:
                state["confirmed"] = detected_inventory

    if state["confirmed"] is None:
        return "Checking"
    return "In inventory" if state["confirmed"] else "Not in inventory"


def reset_inventory_detection():
    _inventory_detection_state.update({
        "confirmed": None,
        "pending": None,
        "pending_samples": 0,
        "last_sample_time": 0.0,
    })


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
    frame = _grab(
        inset_region(scaled_region, detection_inset(screen_width, screen_height))
    )
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


def _capture_limb_sample(limb):
    screen_width, screen_height = get_screen_resolution()
    region, angle_degrees = CV_LIMB_REGIONS[limb]
    scaled_region = scale_region(region, screen_width, screen_height)
    sample_region = inset_region(
        scaled_region,
        detection_inset(screen_width, screen_height),
    )
    sample_points = _rotated_region_points(sample_region, angle_degrees)
    capture_region = (
        int(np.floor(min(point[0] for point in sample_points))),
        int(np.floor(min(point[1] for point in sample_points))),
        int(np.ceil(max(point[0] for point in sample_points))),
        int(np.ceil(max(point[1] for point in sample_points))),
    )
    frame = _grab(capture_region)
    if frame is None:
        return None

    source_points = np.array(
        [
            (x - capture_region[0], y - capture_region[1])
            for x, y in sample_points
        ],
        dtype=np.float32,
    )
    output_width = sample_region[2] - sample_region[0]
    output_height = sample_region[3] - sample_region[1]
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
    return cv2.warpPerspective(
        frame,
        transform,
        (output_width, output_height),
    )


def all_limbs_green(colors):
    """Return True only when every limb region in colors is detected as green."""
    return all(
        colors.get(limb) == DETECTED_COLOUR_RGB["green"]
        for limb in CV_LIMB_REGIONS
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


def detect_health_level(frame, minimum_percentage):
    percentages = get_colour_percentages(frame)
    if not percentages:
        return None

    candidates = {
        0.0: percentages["green"],
        0.25: percentages["yellow"],
        0.5: percentages["red"],
        1.0: percentages["black"],
    }
    level, percentage = max(candidates.items(), key=lambda item: item[1])
    return level if percentage >= minimum_percentage else None


def detect_limb_colour(frame, limb):
    state = _limb_detection_states.setdefault(
        limb,
        {"last": None, "unknown_samples": 0, "pending": None, "pending_samples": 0},
    )
    if frame is None:
        current_colour = (
            state["last"]
            if state["last"] is not None
            and state["unknown_samples"] <= UNKNOWN_HOLD_SAMPLES
            else None
        )
        return DETECTED_COLOUR_RGB.get(current_colour)

    detected_level = detect_health_level(
        frame,
        minimum_percentage=MIN_COLOUR_PERCENTAGE,
    )
    detected_colour = _DAMAGE_COLOURS_BY_LEVEL.get(detected_level)

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
    global _last_limb_sample_time

    limbs = tuple(CV_LIMB_REGIONS)
    now = time.monotonic()
    if limbs and now - _last_limb_sample_time >= LIMB_SAMPLE_INTERVAL:
        _last_limb_sample_time = now
        # Spread region grabs across the 0.5-second sample interval.
        capture_spacing = LIMB_SAMPLE_INTERVAL / len(limbs)
        for index, limb in enumerate(limbs):
            frame = _capture_limb_sample(limb)
            if frame is not None:
                detect_limb_colour(frame, limb)
            if index < len(limbs) - 1:
                time.sleep(capture_spacing)

    colors = {}
    for limb in limbs:
        state = _limb_detection_states.get(limb)
        if state is None or state["unknown_samples"] > UNKNOWN_HOLD_SAMPLES:
            colors[limb] = None
        else:
            colors[limb] = DETECTED_COLOUR_RGB.get(state["last"])
    return colors


def reset_limb_detection():
    global _last_limb_sample_time
    _limb_detection_states.clear()
    _last_limb_sample_time = 0.0


def get_limb_detection_statuses():
    statuses = {}
    for limb in CV_LIMB_REGIONS:
        state = _limb_detection_states.get(limb)
        if state is None:
            statuses[limb] = ("Waiting", "Unknown")
            continue

        pending_colour = state["pending"]
        if pending_colour is not None and pending_colour != state["last"]:
            statuses[limb] = (
                f"Checking {pending_colour.title()}",
                "Checking",
            )
        elif (
            state["last"] is not None
            and state["unknown_samples"] <= UNKNOWN_HOLD_SAMPLES
        ):
            statuses[limb] = (state["last"].title(), state["last"].title())
        else:
            statuses[limb] = ("Unknown", "Unknown")

    return statuses


def get_damage_level_for_color(color):
    if color is None:
        return 0.0
    levels = {
        DETECTED_COLOUR_RGB["green"]: green_limb,
        DETECTED_COLOUR_RGB["yellow"]: yellow_limb,
        DETECTED_COLOUR_RGB["red"]: red_limb,
        DETECTED_COLOUR_RGB["black"]: black_limb,
    }
    return levels.get(tuple(color), 0.0)


# Limb names in display order, for the UI.
LIMB_KEYS = tuple(CV_LIMB_REGIONS)


def get_vibration_for_limb(limb_name, color):
    """Return the vibration level for a CV-classified limb colour.

    limb_name: one of LIMB_KEYS
    color: canonical RGB tuple, or None when unknown
    """
    if limb_name.lower() not in CV_LIMB_REGIONS:
        raise ValueError(f"Unknown limb: {limb_name}")
    return get_damage_level_for_color(color)


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
