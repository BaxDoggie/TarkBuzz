import cv2
import numpy as np
import time
import importlib
dxcam = importlib.import_module("dxcam")


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

    green = damage_colour(frame, np.array([35, 50, 50]), np.array([90, 255, 255]))
    yellow = damage_colour(frame, np.array([15, 100, 100]), np.array([30, 255, 255]))
    orange = damage_colour(frame, np.array([5, 80, 80]), np.array([20, 255, 255]))
    red1 = damage_colour(frame, np.array([0, 80, 80]), np.array([10, 255, 255]))
    red2 = damage_colour(frame, np.array([170, 80, 80]), np.array([180, 255, 255]))
    red = max(red1, red2)

    if red > 0.08:
        return 1.0
    if orange > 0.08:
        return 0.75
    if yellow > 0.08:
        return 0.5
    if green > 0.05:
        return 0.0

    return 0.0










