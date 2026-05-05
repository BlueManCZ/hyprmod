"""Tests for AppState normalization."""

from hyprmod.core.state import _normalize_gradient_string


class TestNormalizeGradient:
    """IPC reports gradients as bare hex; the normalizer adds the ``0x`` prefix
    Hyprland's config-file parser requires."""

    def test_bare_hex_gets_0x_prefix(self):
        assert (
            _normalize_gradient_string("eeb4e718 ee00ff99 45deg") == "0xeeb4e718 0xee00ff99 45deg"
        )

    def test_already_prefixed_unchanged(self):
        assert (
            _normalize_gradient_string("0xeeb4e718 0xee00ff99 45deg")
            == "0xeeb4e718 0xee00ff99 45deg"
        )

    def test_rgba_wrapped_unchanged(self):
        assert _normalize_gradient_string("rgba(b4e718ee) 45deg") == "rgba(b4e718ee) 45deg"

    def test_three_color_gradient(self):
        assert (
            _normalize_gradient_string("eeb4e718 ee00ff99 ffffffff 45deg")
            == "0xeeb4e718 0xee00ff99 0xffffffff 45deg"
        )

    def test_uppercase_hex(self):
        assert _normalize_gradient_string("EEB4E718 45deg") == "0xEEB4E718 45deg"

    def test_zero_degree(self):
        assert _normalize_gradient_string("ffffffff 0deg") == "0xffffffff 0deg"

    def test_non_gradient_string_unchanged(self):
        # Plain text without a trailing "deg" token must pass through untouched.
        assert _normalize_gradient_string("Some Text") == "Some Text"

    def test_vec2_unchanged(self):
        assert _normalize_gradient_string("10 10") == "10 10"

    def test_gaps_unchanged(self):
        assert _normalize_gradient_string("5 5 5 5") == "5 5 5 5"

    def test_empty_string_unchanged(self):
        assert _normalize_gradient_string("") == ""

    def test_mixed_prefixed_and_bare(self):
        # Edge case — partial normalization for mixed input.
        assert (
            _normalize_gradient_string("0xeeb4e718 ee00ff99 45deg") == "0xeeb4e718 0xee00ff99 45deg"
        )
