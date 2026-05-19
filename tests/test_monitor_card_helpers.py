"""Unit tests for the pure helpers in monitor card UI (no GTK required)."""

from dataclasses import replace

from hyprmod.pages.monitors.card import (
    CM_MODES,
    CM_VALUES,
    HDR_SLIDER_FIELDS,
    HDR_SLIDER_SPEC_BY_FIELD,
    SDR_VALUE_DEFAULT,
    SDR_VALUE_DIGITS,
    _format_hdr_display_value,
    _format_hdr_value,
    _parse_hdr_value,
    _resolve_hdr_specs,
)


class TestHdrSliderValues:
    def test_parse_uses_field_default(self):
        assert _parse_hdr_value(None, 80.0) == 80.0
        assert _parse_hdr_value("250", 80.0) == 250.0
        assert _parse_hdr_value("invalid", -1.0) == -1.0

    def test_format_returns_none_at_default_for_non_auto_fields(self):
        # auto_default=False (sdr_brightness/saturation): omit at default so the
        # saved config doesn't carry redundant lines for values Hyprland already
        # uses by default.
        assert _format_hdr_value(80.0, 80.0, 0) is None
        assert _format_hdr_value(120.0, 80.0, 0) == "120"
        assert _format_hdr_value(0.25, 0.2, 2) == "0.25"

    def test_format_tolerates_fp_jitter_at_default(self):
        # Slider can land slightly off the default due to FP arithmetic; the
        # 1e-3 epsilon catches it and still emits None.
        assert _format_hdr_value(1.0 + 1e-9, SDR_VALUE_DEFAULT, SDR_VALUE_DIGITS) is None
        assert _format_hdr_value(1.0 - 1e-9, SDR_VALUE_DEFAULT, SDR_VALUE_DIGITS) is None

    def test_format_zero_renders_as_zero(self):
        # Regression: a naive rstrip("0").rstrip(".") would produce "" for 0.0,
        # which then got written as a malformed config line.
        assert _format_hdr_value(0.0, SDR_VALUE_DEFAULT, SDR_VALUE_DIGITS) == "0"

    def test_format_strips_trailing_zeros(self):
        assert _format_hdr_value(1.5, SDR_VALUE_DEFAULT, SDR_VALUE_DIGITS) == "1.5"
        assert _format_hdr_value(0.8, SDR_VALUE_DEFAULT, SDR_VALUE_DIGITS) == "0.8"

    def test_format_always_emits_for_auto_default_fields(self):
        # auto_default=True (luminance): spec.default is the panel's EDID value;
        # the config must carry it explicitly because Hyprland's no-override
        # behavior for these fields is wrong (uses an internal default that
        # ignores the panel's mastering luminance).
        assert _format_hdr_value(993.0, 993.0, 0, auto_default=True) == "993"
        assert _format_hdr_value(800.0, 993.0, 0, auto_default=True) == "800"

    def test_format_rounds_to_slider_precision(self):
        # Sub-precision panel values like 0.000611 cd/m² collapse to 0 at the
        # slider's digits=2 representation. That's by design — the difference
        # is well below human perception and the panel can't display it
        # distinctly anyway, so the config stays panel-spec-agnostic.
        assert _format_hdr_value(0.000611, 0.000611, 2, auto_default=True) == "0"
        # Slider-representable values keep their precision.
        assert _format_hdr_value(0.5, 0.000611, 2, auto_default=True) == "0.5"

    def test_sdr_brightness_display_uses_two_decimals(self):
        spec = HDR_SLIDER_SPEC_BY_FIELD["sdr_brightness"]
        assert _format_hdr_display_value(1.0, spec) == "1.00"
        assert _format_hdr_display_value(1.25, spec) == "1.25"

    def test_display_value_uses_slider_precision(self):
        # Labels render at the slider's native digits — sub-precision panel
        # defaults render as "0.00" because that's what the slider represents,
        # and the imperceptible difference between 0 and 0.000611 cd/m² makes
        # the precision-loss harmless.
        min_spec = replace(HDR_SLIDER_SPEC_BY_FIELD["sdr_min_luminance"], default=0.000611)
        assert _format_hdr_display_value(0.000611, min_spec) == "0.00"
        assert _format_hdr_display_value(0.5, min_spec) == "0.50"

        max_spec = replace(HDR_SLIDER_SPEC_BY_FIELD["sdr_max_luminance"], default=993.486)
        assert _format_hdr_display_value(993.486, max_spec) == "993"
        assert _format_hdr_display_value(990.0, max_spec) == "990"

    def test_luminance_ranges_and_defaults(self):
        assert HDR_SLIDER_SPEC_BY_FIELD["min_luminance"].title == "HDR Min Luminance"
        assert HDR_SLIDER_SPEC_BY_FIELD["max_luminance"].title == "HDR Max Luminance"

        for field in ("sdr_max_luminance", "max_luminance"):
            spec = HDR_SLIDER_SPEC_BY_FIELD[field]
            assert spec.minimum == 0.0
            assert spec.maximum == 2000.0
            assert spec.default == 800.0

        for field in ("sdr_min_luminance", "min_luminance"):
            spec = HDR_SLIDER_SPEC_BY_FIELD[field]
            assert spec.minimum == 0.0
            assert spec.maximum == 1.0

        spec = HDR_SLIDER_SPEC_BY_FIELD["max_avg_luminance"]
        assert spec.minimum == 0.0
        assert spec.maximum == 2000.0
        assert spec.default == 500.0

    def test_auto_default_flag_set_for_fields_with_non_hyprland_defaults(self):
        # The auto_default flag drives the config-emission semantic: True means
        # spec.default is a real value to write to disk explicitly (panel EDID
        # for luminance, the HDR-mode 0.5 starting point for sdr_brightness);
        # False means spec.default is Hyprland's own correct no-override, so
        # omit from disk and let hyprland-state's explicit_hdr_defaults=True
        # handle the live-apply reset.
        for field in (
            "sdr_brightness",
            "sdr_min_luminance",
            "sdr_max_luminance",
            "min_luminance",
            "max_luminance",
            "max_avg_luminance",
        ):
            spec = HDR_SLIDER_SPEC_BY_FIELD[field]
            assert spec.auto_default is True, f"{field} should auto-default"

        # sdr_saturation is the lone holdout — Hyprland's 1.0 default is the
        # right value for HDR mode too, so we omit at default.
        assert HDR_SLIDER_SPEC_BY_FIELD["sdr_saturation"].auto_default is False

    def test_sdr_brightness_hdr_default(self):
        # sdr_brightness sits at 0.5 in HDR mode, not Hyprland's no-override 1.0
        # which over-brightens SDR content in the HDR pipeline.
        assert HDR_SLIDER_SPEC_BY_FIELD["sdr_brightness"].default == 0.5

    def test_luminance_fields_present(self):
        for field in (
            "sdr_min_luminance",
            "sdr_max_luminance",
            "min_luminance",
            "max_luminance",
            "max_avg_luminance",
        ):
            assert field in HDR_SLIDER_FIELDS


class TestResolveHdrSpecs:
    def test_caps_override_auto_position_with_edid_value(self):
        # A real-panel example: 993 cd/m² peak, 277 cd/m² average, 0.0006 cd/m² min.
        # Both SDR and HDR variants of min/max share the same EDID source.
        caps = {
            "max_luminance": 993.0,
            "max_avg_luminance": 277.0,
            "min_luminance": 0.0006,
        }
        specs = {s.field: s for s in _resolve_hdr_specs(caps)}
        assert specs["sdr_max_luminance"].default == 993.0
        assert specs["max_luminance"].default == 993.0
        assert specs["max_avg_luminance"].default == 277.0
        assert specs["sdr_min_luminance"].default == 0.0006
        assert specs["min_luminance"].default == 0.0006

    def test_falls_back_to_template_when_caps_missing(self):
        # No EDID coverage at all (typical for non-HDR monitors).
        specs = {s.field: s for s in _resolve_hdr_specs({})}
        assert specs["sdr_max_luminance"].default == 800.0
        assert specs["max_avg_luminance"].default == 500.0

    def test_falls_back_when_caps_value_is_none(self):
        # Caps dict includes the key but EDID didn't carry a value — still falls back.
        caps = {"max_luminance": None, "max_avg_luminance": None, "min_luminance": None}
        specs = {s.field: s for s in _resolve_hdr_specs(caps)}
        assert specs["max_luminance"].default == 800.0

    def test_sdr_brightness_and_saturation_unaffected(self):
        # Scalar pipeline knobs — defaults are creative/Hyprland constants,
        # never overridden by EDID.
        caps = {"max_luminance": 993.0, "max_avg_luminance": 277.0, "min_luminance": 0.0006}
        specs = {s.field: s for s in _resolve_hdr_specs(caps)}
        assert specs["sdr_brightness"].default == 0.5
        assert specs["sdr_saturation"].default == SDR_VALUE_DEFAULT


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
