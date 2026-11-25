import unittest
from unittest import mock

import numpy as np

import _support  # noqa: F401  (must come before TarkBuzz)
import TarkBuzz as tb

REF = (tb.Reference_screen_width, tb.Reference_screen_height)


def all_regions():
    regions = [region for region, _ in tb.CV_LIMB_REGIONS.values()]
    regions += list(tb.INVENTORY_TAB_REGIONS.values())
    regions.append(tb.RAID_OUTCOME_REGION)
    return regions


class ScalingTests(unittest.TestCase):
    """Scaling must match the detection test bench: width and height are
    scaled separately from the 3440x1440 reference, truncating to ints."""

    def test_reference_resolution_is_unchanged(self):
        for region in all_regions():
            self.assertEqual(tb.scale_region(region, *REF), region)

    def test_width_and_height_scale_separately(self):
        self.assertEqual(
            tb.scale_region((3440, 1440, 1720, 720), 1920, 1080),
            (1920, 1080, 960, 540),
        )

    def test_matches_test_bench_formula_at_other_resolutions(self):
        for width, height in [(2560, 1440), (1920, 1080), (3840, 2160), (1920, 1200)]:
            for left, top, right, bottom in all_regions():
                expected = (
                    int(left * width / 3440),
                    int(top * height / 1440),
                    int(right * width / 3440),
                    int(bottom * height / 1440),
                )
                self.assertEqual(
                    tb.scale_region((left, top, right, bottom), width, height),
                    expected,
                )


class ImportTests(unittest.TestCase):
    def test_import_does_not_open_screen_capture(self):
        self.assertEqual(_support.camera_creations, [])


class LevelTests(unittest.TestCase):
    def tearDown(self):
        tb.set_colour_levels(
            {"green_limb": 0.0, "yellow_limb": 0.25, "red_limb": 0.75, "black_limb": 1.0}
        )

    def test_levels_follow_set_colour_levels(self):
        red = tb.DETECTED_COLOUR_RGB["red"]
        tb.set_colour_levels({"red_limb": 0.4})
        self.assertEqual(tb.get_vibration_for_limb("head", red), 0.4)

    def test_levels_are_clamped(self):
        tb.set_colour_levels({"black_limb": 3})
        self.assertEqual(tb.black_limb, 1.0)

    def test_unknown_level_key_raises(self):
        with self.assertRaises(ValueError):
            tb.set_colour_levels({"orange_limb": 0.5})

    def test_unknown_colour_is_no_vibration(self):
        self.assertEqual(tb.get_vibration_for_limb("head", None), 0.0)


class DynamicPatternTests(unittest.TestCase):
    def test_no_damage_gives_empty_pattern(self):
        self.assertEqual(tb.calculate_dynamic_buzz_pattern({}), [])

    def test_pattern_pulses_at_worst_level(self):
        pattern = tb.calculate_dynamic_buzz_pattern({"head": 0.75, "thorax": 0.25})
        self.assertEqual({level for level, _ in pattern}, {0.75, 0.0})


class FakeCamera:
    """Returns queued frames, then None (as dxcam does for a still screen)."""

    def __init__(self, frames=()):
        self.frames = list(frames)
        self.regions = []

    def grab(self, region):
        self.regions.append(region)
        return self.frames.pop(0) if self.frames else None


class GrabTests(unittest.TestCase):
    def setUp(self):
        tb._last_frames.clear()
        self.addCleanup(tb._last_frames.clear)

    def test_still_screen_reuses_last_frame_for_that_region(self):
        frame = np.full((4, 4, 3), 7, dtype=np.uint8)
        camera = FakeCamera([frame])
        with mock.patch.object(tb, "get_camera", lambda: camera):
            first = tb._grab((0, 0, 4, 4))
            second = tb._grab((0, 0, 4, 4))
        self.assertIs(first, frame)
        np.testing.assert_array_equal(second, frame)
        self.assertIsNot(second, frame)  # a copy, safe to modify

    def test_cache_is_kept_per_region_across_interleaved_grabs(self):
        a = np.full((2, 2, 3), 1, dtype=np.uint8)
        b = np.full((2, 2, 3), 2, dtype=np.uint8)
        camera = FakeCamera([a, b])
        with mock.patch.object(tb, "get_camera", lambda: camera):
            tb._grab((0, 0, 2, 2))
            tb._grab((10, 10, 12, 12))
            again_a = tb._grab((0, 0, 2, 2))
        np.testing.assert_array_equal(again_a, a)

    def test_region_never_captured_gives_none(self):
        with mock.patch.object(tb, "get_camera", lambda: FakeCamera()):
            self.assertIsNone(tb._grab((0, 0, 2, 2)))

    def test_all_detectors_capture_through_the_cache(self):
        grabbed = []
        with mock.patch.object(tb, "_grab", lambda r: grabbed.append(r)):
            tb.reset_raid_outcome_detection()
            tb.reset_inventory_detection()
            tb.get_raid_outcome()
            tb.get_inventory_status()
            tb._capture_limb_sample("head")
        tb.reset_raid_outcome_detection()
        tb.reset_inventory_detection()
        self.assertEqual(len(grabbed), 3)


if __name__ == "__main__":
    unittest.main()
