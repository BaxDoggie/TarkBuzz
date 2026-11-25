import unittest
from unittest import mock

import numpy as np

import _support  # noqa: F401  (must come before TarkBuzz)
import TarkBuzz as tb

REF = (tb.Reference_screen_width, tb.Reference_screen_height)
ALL_ANCHORS = (tb.ANCHOR_TOP_LEFT, tb.ANCHOR_CENTER)


def all_regions():
    regions = [region for region, _ in tb.CV_LIMB_REGIONS.values()]
    regions += list(tb.INVENTORY_TAB_REGIONS.values())
    regions.append(tb.RAID_OUTCOME_REGION)
    return regions


class ScalingTests(unittest.TestCase):
    def test_reference_resolution_is_unchanged_for_every_anchor(self):
        for region in all_regions():
            for anchor in ALL_ANCHORS:
                self.assertEqual(tb.scale_region(region, *REF, anchor), region)

    def test_health_figure_keeps_its_size_on_16_9_at_same_height(self):
        for region, _ in tb.CV_LIMB_REGIONS.values():
            self.assertEqual(
                tb.scale_region(region, 2560, 1440, tb.LIMB_ANCHOR), region
            )

    def test_centre_regions_shift_with_screen_centre(self):
        left, top, right, bottom = tb.RAID_OUTCOME_REGION
        shift = (3440 - 2560) // 2
        self.assertEqual(
            tb.scale_region(tb.RAID_OUTCOME_REGION, 2560, 1440, tb.ANCHOR_CENTER),
            (left - shift, top, right - shift, bottom),
        )

    def test_1080p_scales_uniformly_so_rotated_limbs_keep_their_shape(self):
        region = tb.CV_LIMB_REGIONS["left_arm"][0]
        scaled = tb.scale_region(region, 1920, 1080, tb.LIMB_ANCHOR)
        self.assertEqual(scaled, tuple(round(v * 0.75) for v in region))

    def test_centre_regions_stay_on_screen_for_16_10(self):
        for name, region in tb.INVENTORY_TAB_REGIONS.items():
            left, top, right, bottom = tb.scale_region(
                region, 1920, 1200, tb.INVENTORY_ANCHOR
            )
            self.assertGreaterEqual(left, 0, name)
            self.assertLessEqual(right, 1920, name)
            self.assertGreaterEqual(top, 0, name)

    def test_unknown_anchor_raises(self):
        with self.assertRaises(ValueError):
            tb.scale_region((0, 0, 1, 1), *REF, "nowhere")


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
