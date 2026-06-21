"""Tests for keyboard-layout (kb_layout / kb_variant) parse and serialize.

The widget owns two positionally-aligned config keys. These tests cover
the pure round-trip between the comma-separated strings Hyprland stores
and the ordered (layout, variant) pairs the widget edits.
"""

import pytest

from hyprmod.core.schema import load_schema
from hyprmod.ui.options.keyboard import parse_sources, serialize_sources
from hyprmod.ui.sources import MissingDependencyError, get_source_values


class TestParseSources:
    def test_single_layout(self):
        assert parse_sources("us", "") == [("us", "")]

    def test_multiple_layouts(self):
        assert parse_sources("us,ru", "") == [("us", ""), ("ru", "")]

    def test_variant_aligns_by_position(self):
        assert parse_sources("us,ru", "dvorak,") == [("us", "dvorak"), ("ru", "")]

    def test_second_layout_variant(self):
        assert parse_sources("us,ru", ",phonetic") == [("us", ""), ("ru", "phonetic")]

    def test_strips_whitespace_after_commas(self):
        assert parse_sources("us, ru, de", "") == [("us", ""), ("ru", ""), ("de", "")]

    def test_empty_layout_string_yields_nothing(self):
        assert parse_sources("", "") == []

    def test_empty_layout_slots_are_dropped(self):
        assert parse_sources("us,,de", "") == [("us", ""), ("de", "")]

    def test_surplus_variants_are_ignored(self):
        assert parse_sources("us", "dvorak,colemak") == [("us", "dvorak")]


class TestSerializeSources:
    def test_single_layout(self):
        assert serialize_sources([("us", "")]) == ("us", "")

    def test_multiple_base_layouts_leave_variant_unset(self):
        assert serialize_sources([("us", ""), ("ru", "")]) == ("us,ru", "")

    def test_variant_kept_with_positional_padding(self):
        assert serialize_sources([("us", "dvorak"), ("ru", "")]) == ("us,ru", "dvorak,")

    def test_trailing_variant_preserved(self):
        assert serialize_sources([("us", ""), ("ru", "phonetic")]) == ("us,ru", ",phonetic")

    def test_empty_list(self):
        assert serialize_sources([]) == ("", "")


@pytest.mark.parametrize(
    ("layout", "variant"),
    [
        ("us", ""),
        ("us,ru", ""),
        ("us,ru", "dvorak,"),
        ("us,ru,de", ",phonetic,"),
    ],
)
def test_round_trip(layout, variant):
    assert serialize_sources(parse_sources(layout, variant)) == (layout, variant)


class TestSchemaWiring:
    """The layouts row owns kb_variant; the schema must reflect that pairing."""

    @pytest.fixture(scope="class")
    def options(self):
        flat = {}
        for group in load_schema()["groups"]:
            for section in group.get("sections", []):
                for option in section.get("options", []):
                    flat[option["key"]] = option
        return flat

    def test_kb_layout_is_keyboard_layouts_type(self, options):
        assert options["input:kb_layout"]["type"] == "keyboard_layouts"

    def test_kb_layout_points_at_variant_companion(self, options):
        assert options["input:kb_layout"]["companion_key"] == "input:kb_variant"

    def test_kb_variant_is_managed_by_layout(self, options):
        assert options["input:kb_variant"]["managed_by"] == "input:kb_layout"


def test_input_sources_carry_layout_and_variant():
    """Each input source splits into the layout + variant the widget pairs."""
    try:
        items = get_source_values("xkb_input_sources")
    except MissingDependencyError:
        pytest.skip("gnome-desktop-4 not installed")
    assert items, "expected at least one XKB input source"
    assert all({"id", "layout", "variant", "name", "label"} <= item.keys() for item in items)
    us = next(item for item in items if item["id"] == "us")
    assert (us["layout"], us["variant"]) == ("us", "")
