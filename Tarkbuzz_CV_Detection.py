import cv2
import numpy as np
import time
import importlib
import dxcam
import tkinter as tk
import ctypes

REFERENCE_WIDTH = 3440
REFERENCE_HEIGHT = 1440


HEAD_REGION = (80, 27, 118, 72)  # (left, top, right, bottom)
TORSO_REGION = (75, 75, 111, 113)
STOMACH_REGION = (70, 125, 116, 163)
RIGHT_LEG_REGION = (110, 165, 130, 285)
RIGHT_LEG_ANGLE_DEGREES = -12
LEFT_LEG_REGION = (59, 165, 79, 285)
LEFT_LEG_ANGLE_DEGREES = 3
RIGHT_ARM_REGION = (127, 75, 147, 168)
RIGHT_ARM_ANGLE_DEGREES = -21
LEFT_ARM_REGION = (39, 75, 59, 168)
LEFT_ARM_ANGLE_DEGREES = 15
DETECTION_INSET = 4
MIN_COLOUR_PERCENTAGE = 0.02
TORSO_MIN_COLOUR_PERCENTAGE = 0.003
TORSO_YELLOW_HUE_RANGE = (10, 45)
TORSO_YELLOW_MIN_SATURATION = 45
TORSO_YELLOW_MIN_VALUE = 55
UNKNOWN_HOLD_SAMPLES = 8
COLOUR_CHANGE_SAMPLES = 2

camera = dxcam.create(output_color="BGR")

def get_screen_size():
    width = ctypes.windll.user32.GetSystemMetrics(0)
    height = ctypes.windll.user32.GetSystemMetrics(1)
    return width, height

def scale_region(region, actual_width, actual_height):
    left, top, right, bottom = region
    scale_x = actual_width / REFERENCE_WIDTH
    scale_y = actual_height / REFERENCE_HEIGHT

    return (
            int(left * scale_x),
            int(top * scale_y),
            int(right * scale_x),
            int(bottom * scale_y)
        )


def inset_region(region, inset):
    left, top, right, bottom = region

    return (
        left + inset,
        top + inset,
        right - inset,
        bottom - inset
    )


def rotated_region_points(region, angle_degrees):
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


def points_bounds(points):
    xs, ys = zip(*points)
    return (
        int(np.floor(min(xs))),
        int(np.floor(min(ys))),
        int(np.ceil(max(xs))),
        int(np.ceil(max(ys))),
    )


def rectify_region(frame, capture_region, sample_points, output_size):
    left, top, _, _ = capture_region
    source_points = np.array(
        [(x - left, y - top) for x, y in sample_points],
        dtype=np.float32
    )
    output_width, output_height = output_size
    destination_points = np.array(
        [
            (0, 0),
            (output_width - 1, 0),
            (output_width - 1, output_height - 1),
            (0, output_height - 1),
        ],
        dtype=np.float32
    )
    transform = cv2.getPerspectiveTransform(source_points, destination_points)
    return cv2.warpPerspective(frame, transform, output_size)


def damage_colour(frame, lower_hsv, upper_hsv):

    if frame is None or frame.size == 0:
        return 0.0

    if frame.shape[-1] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    hsv_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv_frame, lower_hsv, upper_hsv)

    total = mask.shape[0] * mask.shape[1]

    if total == 0:
        return 0.0

    return cv2.countNonZero(mask) / total

def get_colour_percentages(frame, torso=False):
    if frame is None or frame.size == 0:
        return {}

    if frame.shape[-1] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    r, g, b = cv2.split(rgb)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    hue, saturation, value = cv2.split(hsv)
    red_channel = r.astype(np.int16)
    green_channel = g.astype(np.int16)
    blue_channel = b.astype(np.int16)

    green = (
        (green_channel > red_channel + 20) &
        (green_channel > blue_channel + 20) &
        (green_channel >= 50)
    )
    if torso:
        yellow_hue_min, yellow_hue_max = TORSO_YELLOW_HUE_RANGE
        yellow_min_saturation = TORSO_YELLOW_MIN_SATURATION
        yellow_min_value = TORSO_YELLOW_MIN_VALUE
    else:
        yellow_hue_min, yellow_hue_max = 10, 35
        yellow_min_saturation = 60
        yellow_min_value = 80

    yellow = (
        (hue >= yellow_hue_min) & (hue <= yellow_hue_max) &
        (saturation >= yellow_min_saturation) & (value >= yellow_min_value)
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


def get_colour_diagnostics(frame):
    if frame is None or frame.size == 0:
        return "no pixels"

    if frame.shape[-1] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.float32)
    average = rgb.reshape(-1, 3).mean(axis=0)
    brightest = rgb.reshape(-1, 3)[np.argmax(rgb.sum(axis=2))]

    return (
        f"avg RGB=({average[0]:.0f}, {average[1]:.0f}, {average[2]:.0f}), "
        f"brightest RGB=({brightest[0]:.0f}, {brightest[1]:.0f}, {brightest[2]:.0f})"
    )


def detect_damage_level(
    frame,
    minimum_percentage=MIN_COLOUR_PERCENTAGE,
    torso=False,
    prefer_chromatic=False
):
    percentages = get_colour_percentages(frame, torso=torso)

    if not percentages:
        return None

    if prefer_chromatic:
        chromatic_candidates = {
            0.0: percentages["green"],
            0.25: percentages["yellow"],
            0.5: percentages["red"],
        }
        chromatic_level, chromatic_percentage = max(
            chromatic_candidates.items(),
            key=lambda item: item[1]
        )
        if chromatic_percentage >= minimum_percentage:
            return chromatic_level

    candidates = {
        0.0: percentages["green"],
        0.25: percentages["yellow"],
        0.5: percentages["red"],
        1.0: percentages["black"],
    }
    level, percentage = max(candidates.items(), key=lambda item: item[1])

    if percentage >= minimum_percentage:
        return level

    return None


def update_detection_state(state, detected_level):
    if detected_level is None:
        state["unknown_samples"] += 1
        if state["unknown_samples"] <= UNKNOWN_HOLD_SAMPLES:
            return state["last_level"]
        return None

    state["unknown_samples"] = 0
    if detected_level == state["last_level"]:
        state["pending_level"] = None
        state["pending_samples"] = 0
    elif detected_level == state["pending_level"]:
        state["pending_samples"] += 1
    else:
        state["pending_level"] = detected_level
        state["pending_samples"] = 1

    if state["pending_samples"] >= COLOUR_CHANGE_SAMPLES:
        state["last_level"] = detected_level
        state["pending_level"] = None
        state["pending_samples"] = 0

    return state["last_level"]


DEBUG_SCREEN_REGION = (0, 0, 3440, 1440)

def debug_arm_loop(name, region, angle_degrees):
    print("Starting overlay debug. Press Ctrl+C to stop.")

    screen_width, screen_height = get_screen_size()
    scaled_arm_region = scale_region(
        region,
        screen_width,
        screen_height
    )
    arm_border_points = rotated_region_points(
        scaled_arm_region,
        angle_degrees
    )
    arm_detection_region = inset_region(
        scaled_arm_region,
        DETECTION_INSET
    )
    arm_sample_points = rotated_region_points(
        arm_detection_region,
        angle_degrees
    )
    arm_capture_region = points_bounds(arm_sample_points)
    arm_sample_size = (
        arm_detection_region[2] - arm_detection_region[0],
        arm_detection_region[3] - arm_detection_region[1],
    )

    overlay = create_detection_overlay(
        [arm_border_points],
        screen_width,
        screen_height
    )
    state = {
        "last_level": None,
        "unknown_samples": 0,
        "pending_level": None,
        "pending_samples": 0,
    }
    damage_colours = {
        0.0: "green",
        0.25: "yellow",
        0.5: "red",
        1.0: "black",
    }

    try:
        while True:
            frame = camera.grab(region=arm_capture_region)
            if frame is not None:
                sample_frame = rectify_region(
                    frame,
                    arm_capture_region,
                    arm_sample_points,
                    arm_sample_size
                )
                percentages = get_colour_percentages(sample_frame)
                detected_level = detect_damage_level(
                    sample_frame,
                    minimum_percentage=TORSO_MIN_COLOUR_PERCENTAGE,
                    prefer_chromatic=True
                )
                level = update_detection_state(state, detected_level)
                colour = damage_colours.get(level, "unknown")
                selected_colour = damage_colours.get(
                    detected_level,
                    "unknown"
                )
                candidate_level, candidate_percentage = max(
                    percentages.items(),
                    key=lambda item: item[1]
                )
                print(
                    f"{name}: {colour} | "
                    f"selected={selected_colour} | "
                    f"largest raw category={candidate_level} "
                    f"({candidate_percentage:.2%}; "
                    f"minimum={TORSO_MIN_COLOUR_PERCENTAGE:.2%}) | "
                    f"green={percentages['green']:.2%}, "
                    f"yellow={percentages['yellow']:.2%}, "
                    f"red={percentages['red']:.2%}, "
                    f"black={percentages['black']:.2%} | "
                    f"sample={sample_frame.shape[1]}x{sample_frame.shape[0]} "
                    f"at {angle_degrees} deg | "
                    f"{get_colour_diagnostics(sample_frame)}"
                )

            overlay.update()
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("Stopped")

    finally:
        overlay.destroy()


def debug_right_arm_loop():
    debug_arm_loop(
        "Right arm",
        RIGHT_ARM_REGION,
        RIGHT_ARM_ANGLE_DEGREES
    )


def debug_left_leg_loop():
    debug_arm_loop(
        "Left leg",
        LEFT_LEG_REGION,
        LEFT_LEG_ANGLE_DEGREES
    )


def debug_left_arm_loop():
    debug_arm_loop(
        "Left arm",
        LEFT_ARM_REGION,
        LEFT_ARM_ANGLE_DEGREES
    )


def create_detection_overlay(regions, screen_width, screen_height):
    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", "magenta")
    root.geometry(f"{screen_width}x{screen_height}+0+0")

    canvas = tk.Canvas(
        root,
        width=screen_width,
        height=screen_height,
        bg="magenta",
        highlightthickness=0
    )
    canvas.pack()

    colors = ["cyan"]

    for points, color in zip(regions, colors):
        canvas.create_polygon(
            *[coordinate for point in points for coordinate in point],
            outline=color,
            fill="",
            width=4
        )

    # Make the overlay click-through and prevent it taking focus
    hwnd = ctypes.windll.user32.GetParent(root.winfo_id())
    styles = ctypes.windll.user32.GetWindowLongW(hwnd, -20)

    ctypes.windll.user32.SetWindowLongW(
        hwnd,
        -20,
        styles | 0x20 | 0x80 | 0x8000000
    )

    root.update()
    return root


if __name__ == "__main__":
    debug_left_arm_loop()
