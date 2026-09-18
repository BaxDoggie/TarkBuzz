import cv2
import numpy as np
import time
import importlib
import dxcam
import tkinter as tk
import ctypes

REFERENCE_WIDTH = 3440
REFERENCE_HEIGHT = 1440


HEAD_REGION = (80, 30, 120, 72)  # (left, top, right, bottom)
TORSO_REGION = (80, 130, 120, 172)  # (left, top, right, bottom)

camera = dxcam.create()

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

def detect_head_damage(frame):
    if frame is None or frame.size == 0:
        return 0.0

    if frame.shape[-1] == 4:
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    r, g, b = cv2.split(rgb)

    green = (g > 145) & (r < 45) & (b < 45)
    yellow = (r > 150) & (g > 150) & (b < 100)
    orange = (r > 150) & (g > 100) & (b < 100)
    red = (r > 150) & (g < 100) & (b < 100)
    black = (r < 50) & (g < 50) & (b < 50)

    total_pixels = frame.shape[0] * frame.shape[1]

    if cv2.countNonZero(green.astype(np.uint8)) / total_pixels > 0.05:
        return 0.0
    if cv2.countNonZero(yellow.astype(np.uint8)) / total_pixels > 0.08:
        return 0.25
    if cv2.countNonZero(orange.astype(np.uint8)) / total_pixels > 0.08:
        return 0.5
    if cv2.countNonZero(red.astype(np.uint8)) / total_pixels > 0.08:
        return 0.75
    if cv2.countNonZero(black.astype(np.uint8)) / total_pixels > 0.08:
        return 1.0

    return 0.0


DEBUG_SCREEN_REGION = (0, 0, 3440, 1440)

def debug_head_loop():
    print("Starting overlay debug. Press Ctrl+C to stop.")

    screen_width, screen_height = get_screen_size()
    scaled_head_region = scale_region(
        HEAD_REGION,
        screen_width,
        screen_height
    )

    overlay = create_detection_overlay(scaled_head_region)

    try:
        while True:
            frame = camera.grab(region=scaled_head_region)

            if frame is not None:
                level = detect_head_damage(frame)
                print(f"Head damage level: {level:.2f}")

            overlay.update()
            time.sleep(0.25)

    except KeyboardInterrupt:
        print("Stopped")

    finally:
        overlay.destroy()


def create_detection_overlay(region):
    left, top, right, bottom = region

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.attributes("-transparentcolor", "magenta")
    root.geometry("3440x1440+0+0")

    canvas = tk.Canvas(
        root,
        width=3440,
        height=1440,
        bg="magenta",
        highlightthickness=0
    )
    canvas.pack()

    canvas.create_rectangle(
        left,
        top,
        right,
        bottom,
        outline="lime",
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










