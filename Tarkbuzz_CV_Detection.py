import cv2
import numpy as np
import time
import importlib
import dxcam


HEAD_REGION = (40, 0, 260, 140) # (x, y, width, height) TEMPORARY

camera = dxcam.create()

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


def debug_head_loop():
    print("Starting head debug loop")
    try:
        while True:
            frame = camera.grab(region=HEAD_REGION)
            if frame is not None:
                level = detect_head_damage(frame)
                print(f"Head damage level: {level:.2f}")
            time.sleep(0.25)
    except KeyboardInterrupt:
        print("Stopped")



if __name__ == "__main__":
    debug_head_loop()










