"""Unit tests for the pure helpers in monitor card UI (no GTK required)."""

from hyprmod.pages.monitors.card import (
    CM_MODES,
    CM_VALUES,
    HDR_RESET_FIELDS,
    HDR_SLIDER_FIELDS,
    HDR_SLIDER_SPEC_BY_FIELD,
    SDR_VALUE_DEFAULT,
    _format_hdr_display_value,
    _format_hdr_value,
    _format_sdr,
    _parse_hdr_value,
    _parse_sdr,
)


class TestParseSdr:
    def test_none_returns_default(self):
        assert _parse_sdr(None) == SDR_VALUE_DEFAULT

    def test_numeric_string(self):
        assert _parse_sdr("1.25") == 1.25

    def test_zero(self):
        assert _parse_sdr("0") == 0.0

    def test_invalid_falls_back_to_default(self):
        assert _parse_sdr("not a number") == SDR_VALUE_DEFAULT


class TestFormatSdr:
    def test_default_returns_none(self):
        assert _format_sdr(SDR_VALUE_DEFAULT) is None

    def test_near_default_returns_none(self):
        # Float-precision jitter around 1.0 — still treated as "default".
        assert _format_sdr(1.0 + 1e-9) is None
        assert _format_sdr(1.0 - 1e-9) is None

    def test_just_below_default_emits_value(self):
        # 0.95 is a valid override the spinner can produce; it must not be swallowed.
        assert _format_sdr(0.95) == "0.95"

    def test_override_formatted(self):
        assert _format_sdr(1.2) == "1.2"

    def test_trailing_zero_stripped(self):
        assert _format_sdr(1.5) == "1.5"
        assert _format_sdr(0.8) == "0.8"

    def test_two_decimal_places(self):
        assert _format_sdr(0.98) == "0.98"

    def test_zero_renders_as_zero(self):
        # Regression: naive rstrip("0").rstrip(".") would produce "" for 0.0,
        # which then got written as a malformed config line.
        assert _format_sdr(0.0) == "0"


class TestHdrSliderValues:
    def test_parse_uses_field_default(self):
        assert _parse_hdr_value(None, 80.0) == 80.0
        assert _parse_hdr_value("250", 80.0) == 250.0
        assert _parse_hdr_value("invalid", -1.0) == -1.0

    def test_format_uses_field_default(self):
        assert _format_hdr_value(80.0, 80.0, 0) is None
        assert _format_hdr_value(120.0, 80.0, 0) == "120"
        assert _format_hdr_value(0.25, 0.2, 2) == "0.25"

    def test_sdr_brightness_display_uses_two_decimals(self):
        spec = HDR_SLIDER_SPEC_BY_FIELD["sdr_brightness"]
        assert _format_hdr_display_value(1.0, spec) == "1.00"
        assert _format_hdr_display_value(1.25, spec) == "1.25"

    def test_luminance_ranges_and_defaults(self):
        assert HDR_SLIDER_SPEC_BY_FIELD["min_luminance"].title == "HDR Min Luminance"
        assert HDR_SLIDER_SPEC_BY_FIELD["max_luminance"].title == "HDR Max Luminance"

        for field in ("sdr_max_luminance", "max_luminance"):
            spec = HDR_SLIDER_SPEC_BY_FIELD[field]
            assert spec.minimum == 0.0
            assert spec.maximum == 2000.0
            assert spec.default == 800.0
            assert spec.auto_default is False

        for field in ("sdr_min_luminance", "min_luminance"):
            spec = HDR_SLIDER_SPEC_BY_FIELD[field]
            assert spec.minimum == 0.0
            assert spec.maximum == 1.0
            assert spec.auto_default is False

    def test_max_avg_defaults_to_auto_without_negative_range(self):
        spec = HDR_SLIDER_SPEC_BY_FIELD["max_avg_luminance"]
        assert spec.minimum == 0.0
        assert spec.maximum == 2000.0
        assert spec.default == 500.0
        assert spec.auto_default is True
        assert _format_hdr_display_value(spec.default, spec) == "Auto"

    def test_luminance_fields_present(self):
        for field in (
            "sdr_min_luminance",
            "sdr_max_luminance",
            "min_luminance",
            "max_luminance",
            "max_avg_luminance",
        ):
            assert field in HDR_SLIDER_FIELDS

    def test_reset_fields_cover_all_hdr_sliders(self):
        assert HDR_RESET_FIELDS == HDR_SLIDER_FIELDS


class TestColorManagementPresets:
    def test_aligned_lengths(self):
        assert len(CM_MODES) == len(CM_VALUES)

    def test_first_value_is_none(self):
        # The first entry represents "no override" / Hyprland default.
        assert CM_VALUES[0] is None

    def test_hdr_presets_present(self):
        assert "hdr" in CM_VALUES
        assert "hdredid" in CM_VALUES

    def test_standard_presets_present(self):
        for preset in ("srgb", "adobe", "wide", "edid"):
            assert preset in CM_VALUES
