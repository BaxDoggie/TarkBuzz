import cv2
import numpy as np
import time
import importlib
import dxcam
import tkinter as tk
import ctypes

REFERENCE_WIDTH = 3440
REFERENCE_HEIGHT = 1440


HEAD_REGION = (80, 30, 118, 72)  # (left, top, right, bottom)
TORSO_REGION = (75, 81, 111, 119)  # 10 left, 40 up
DETECTION_INSET = 4

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

def get_colour_percentages(frame):
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
    yellow = (
        (red_channel > 150) &
        (green_channel > 150) &
        (blue_channel < 100)
    )
    orange = (
        (hue >= 10) & (hue <= 25) &
        (saturation >= 60) & (value >= 80)
    )
    red = (
        ((hue < 10) | (hue > 170)) &
        (saturation >= 60) & (value >= 80)
    )
    black = value < 25

    total_pixels = frame.shape[0] * frame.shape[1]

    return {
        "green": cv2.countNonZero(green.astype(np.uint8)) / total_pixels,
        "yellow": cv2.countNonZero(yellow.astype(np.uint8)) / total_pixels,
        "orange": cv2.countNonZero(orange.astype(np.uint8)) / total_pixels,
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


def detect_head_damage(frame):
    percentages = get_colour_percentages(frame)

    if not percentages:
        return None

    if percentages["yellow"] > 0.08:
        return 0.25
    if percentages["orange"] > 0.08:
        return 0.5
    if percentages["red"] > 0.08:
        return 0.75
    if percentages["black"] > 0.08:
        return 1.0
    if percentages["green"] > 0.05:
        return 0.0

    return None


DEBUG_SCREEN_REGION = (0, 0, 3440, 1440)

def debug_head_loop(): #Shows damage level in terminal and overlays the detection areas on screen
    print("Starting overlay debug. Press Ctrl+C to stop.")

    screen_width, screen_height = get_screen_size()
    scaled_head_region = scale_region(
        HEAD_REGION,
        screen_width,
        screen_height
    )

    scaled_torso_region = scale_region(
        TORSO_REGION,
        screen_width,
        screen_height
    )
    scaled_head_detection_region = inset_region(
        scaled_head_region,
        DETECTION_INSET
    )

    overlay = create_detection_overlay(
        [scaled_head_region, scaled_torso_region],
        screen_width,
        screen_height
    )

    try:
        while True:
            frame = camera.grab(region=scaled_head_detection_region)

            if frame is not None:
                percentages = get_colour_percentages(frame)
                level = detect_head_damage(frame)
                damage_colours = {
                    0.0: "green",
                    0.25: "yellow",
                    0.5: "orange",
                    0.75: "red",
                    1.0: "black",
                }
                colour = damage_colours.get(level, "unknown")
                print(
                    f"Head: {colour} | "
                    f"green={percentages['green']:.1%}, "
                    f"yellow={percentages['yellow']:.1%}, "
                    f"orange={percentages['orange']:.1%}, "
                    f"red={percentages['red']:.1%}, "
                    f"black={percentages['black']:.1%} | "
                    f"{get_colour_diagnostics(frame)}"
                )

            overlay.update()
            time.sleep(0.5)

    except KeyboardInterrupt:
        print("Stopped")

    finally:
        overlay.destroy()


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

    colors = ["lime", "cyan"]

    for region, color in zip(regions, colors):
        left, top, right, bottom = region

        canvas.create_rectangle(
            left,
            top,
            right,
            bottom,
            outline=color,
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
    debug_head_loop()










