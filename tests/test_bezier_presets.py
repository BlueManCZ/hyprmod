"""Tests for bezier math and presets."""

from hyprland_state import HYPRLAND_NATIVE_CURVES

from hyprmod.data.bezier_presets import BUILTIN_PRESETS, cubic_bezier, ease


class TestCubicBezier:
    def test_endpoints(self):
        assert cubic_bezier(0.0, 0.5, 0.5) == 0.0
        assert cubic_bezier(1.0, 0.5, 0.5) == 1.0

    def test_midpoint_linear(self):
        # For a linear curve (p1=0.33, p2=0.67), midpoint should be ~0.5
        val = cubic_bezier(0.5, 1 / 3, 2 / 3)
        assert abs(val - 0.5) < 0.01


class TestEase:
    def test_endpoints(self):
        assert ease(0.0, 0.25, 0.1, 0.25, 1.0) == 0.0
        assert abs(ease(1.0, 0.25, 0.1, 0.25, 1.0) - 1.0) < 1e-6

    def test_easeInOut_symmetric(self):
        val = ease(0.5, 0.42, 0.0, 0.58, 1.0)
        assert abs(val - 0.5) < 0.01


class TestPresets:
    def test_all_presets_have_four_points(self):
        for name, points in BUILTIN_PRESETS.items():
            assert len(points) == 4, f"{name} has {len(points)} points"

    def test_native_curves_not_in_presets(self):
        for name in HYPRLAND_NATIVE_CURVES:
            assert name not in BUILTIN_PRESETS

    def test_native_curves_is_frozenset(self):
        assert isinstance(HYPRLAND_NATIVE_CURVES, frozenset)
