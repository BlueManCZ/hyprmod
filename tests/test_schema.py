"""Validate the options.json schema file."""

import pytest

from hyprmod.core.schema import _drop_unavailable, load_schema

VALID_TYPES = {"bool", "int", "float", "string", "choice", "color", "gradient", "vec2"}


@pytest.fixture(scope="module")
def schema():
    return load_schema()


@pytest.fixture(scope="module")
def all_options(schema):
    """Flatten all options from the schema."""
    options = []
    for group in schema["groups"]:
        for section in group.get("sections", []):
            for option in section.get("options", []):
                options.append(option)
    return options


@pytest.fixture(scope="module")
def all_keys(all_options):
    return {o["key"] for o in all_options}


class TestSchemaStructure:
    def test_valid_json(self, schema):
        assert "groups" in schema
        assert isinstance(schema["groups"], list)

    def test_groups_have_required_fields(self, schema):
        for group in schema["groups"]:
            assert "id" in group, f"Group missing 'id': {group}"
            assert "label" in group, f"Group missing 'label': {group}"
            assert "sections" in group, f"Group '{group['id']}' missing 'sections'"

    def test_sections_have_required_fields(self, schema):
        for group in schema["groups"]:
            for section in group["sections"]:
                assert "id" in section, f"Section missing 'id' in group '{group['id']}'"
                assert "label" in section, f"Section missing 'label' in group '{group['id']}'"
                assert "options" in section, f"Section '{section['id']}' missing 'options'"

    def test_no_empty_sections(self, schema):
        for group in schema["groups"]:
            for section in group["sections"]:
                assert len(section["options"]) > 0, f"Section '{section['id']}' has no options"


class TestOptionFields:
    def test_required_fields(self, all_options):
        for opt in all_options:
            assert "key" in opt, f"Option missing 'key': {opt}"
            assert "label" in opt, f"Option '{opt.get('key')}' missing 'label'"
            assert "type" in opt, f"Option '{opt.get('key')}' missing 'type'"
            assert "default" in opt, f"Option '{opt.get('key')}' missing 'default'"

    def test_valid_type(self, all_options):
        for opt in all_options:
            assert opt["type"] in VALID_TYPES, (
                f"Option '{opt['key']}' has invalid type '{opt['type']}'"
            )

    def test_unique_keys(self, all_options):
        keys = [o["key"] for o in all_options]
        dupes = [k for k in keys if keys.count(k) > 1]
        assert not dupes, f"Duplicate keys: {set(dupes)}"

    def test_key_format(self, all_options):
        for opt in all_options:
            key = opt["key"]
            assert ":" in key, f"Key '{key}' missing section separator ':'"
            assert not key.startswith(":"), f"Key '{key}' starts with ':'"
            assert not key.endswith(":"), f"Key '{key}' ends with ':'"


class TestDefaultTypes:
    def test_bool_default(self, all_options):
        for opt in all_options:
            if opt["type"] == "bool":
                assert isinstance(opt["default"], bool), (
                    f"Option '{opt['key']}' default should be bool, "
                    f"got {type(opt['default']).__name__}"
                )

    def test_int_default(self, all_options):
        for opt in all_options:
            if opt["type"] == "int":
                assert isinstance(opt["default"], int) and not isinstance(opt["default"], bool), (
                    f"Option '{opt['key']}' default should be int, "
                    f"got {type(opt['default']).__name__}"
                )

    def test_float_default(self, all_options):
        for opt in all_options:
            if opt["type"] == "float":
                assert isinstance(opt["default"], (int, float)), (
                    f"Option '{opt['key']}' default should be float, "
                    f"got {type(opt['default']).__name__}"
                )

    def test_str_default(self, all_options):
        for opt in all_options:
            if opt["type"] == "string":
                assert isinstance(opt["default"], str), (
                    f"Option '{opt['key']}' default should be str, "
                    f"got {type(opt['default']).__name__}"
                )

    def test_enum_default(self, all_options):
        for opt in all_options:
            if opt["type"] == "choice":
                assert isinstance(opt["default"], (str, int)), (
                    f"Option '{opt['key']}' enum default should be str or int"
                )


class TestNumericConstraints:
    def test_int_has_min_max(self, all_options):
        for opt in all_options:
            if opt["type"] == "int":
                assert "min" in opt, f"Int option '{opt['key']}' missing 'min'"
                assert "max" in opt, f"Int option '{opt['key']}' missing 'max'"

    def test_float_has_min_max(self, all_options):
        for opt in all_options:
            if opt["type"] == "float":
                assert "min" in opt, f"Float option '{opt['key']}' missing 'min'"
                assert "max" in opt, f"Float option '{opt['key']}' missing 'max'"

    def test_min_less_than_max(self, all_options):
        for opt in all_options:
            if "min" in opt and "max" in opt:
                assert opt["min"] < opt["max"], (
                    f"Option '{opt['key']}' min ({opt['min']}) >= max ({opt['max']})"
                )

    def test_default_in_range(self, all_options):
        for opt in all_options:
            if "min" in opt and "max" in opt:
                assert opt["min"] <= opt["default"] <= opt["max"], (
                    f"Option '{opt['key']}' default ({opt['default']}) "
                    f"not in range [{opt['min']}, {opt['max']}]"
                )


class TestChoiceOptions:
    def test_enum_has_values(self, all_options):
        for opt in all_options:
            if opt["type"] == "choice":
                assert "values" in opt, f"Choice '{opt['key']}' missing 'values'"
                assert len(opt["values"]) >= 2, (
                    f"Choice '{opt['key']}' should have at least 2 values"
                )

    def test_enum_values_have_label(self, all_options):
        for opt in all_options:
            if opt["type"] == "choice":
                for v in opt["values"]:
                    assert "label" in v, f"Enum value in '{opt['key']}' missing 'label'"

    def test_enum_default_in_values(self, all_options):
        for opt in all_options:
            if opt["type"] == "choice":
                ids = [v.get("id", str(i)) for i, v in enumerate(opt["values"])]
                assert str(opt["default"]) in ids, (
                    f"Choice '{opt['key']}' default '{opt['default']}' not in values {ids}"
                )


class TestDependencies:
    def test_depends_on_references_existing_key(self, all_options, all_keys):
        for opt in all_options:
            dep = opt.get("depends_on")
            if dep:
                assert dep in all_keys, (
                    f"Option '{opt['key']}' depends_on '{dep}' which doesn't exist"
                )

    def test_depends_on_references_bool_option(self, all_options):
        opt_map = {o["key"]: o for o in all_options}
        for opt in all_options:
            dep = opt.get("depends_on")
            if dep and dep in opt_map:
                # Options with a dynamic source use depends_on to refresh
                # values, not for visibility — the parent need not be bool.
                if opt.get("source"):
                    continue
                assert opt_map[dep]["type"] == "bool", (
                    f"Option '{opt['key']}' depends_on '{dep}' "
                    f"which is type '{opt_map[dep]['type']}', expected 'bool'"
                )

    def test_no_circular_dependencies(self, all_options):
        opt_map = {o["key"]: o for o in all_options}
        for opt in all_options:
            visited = set()
            current = opt.get("depends_on")
            while current:
                assert current not in visited, (
                    f"Circular dependency detected: {opt['key']} -> {visited}"
                )
                visited.add(current)
                current = opt_map.get(current, {}).get("depends_on")


class TestSchemaMerge:
    """Verify that hyprland-schema fields are merged into the overlay."""

    def test_schema_fills_missing_type(self, all_options):
        """Options without explicit type in overlay should get it from schema."""
        opt = next(o for o in all_options if o["key"] == "general:border_size")
        assert opt["type"] == "int"

    def test_schema_fills_missing_default(self, all_options):
        opt = next(o for o in all_options if o["key"] == "general:border_size")
        assert opt["default"] == 1

    def test_schema_fills_missing_min_max(self, all_options):
        opt = next(o for o in all_options if o["key"] == "general:border_size")
        assert opt["min"] == 0
        assert opt["max"] == 20

    def test_schema_fills_description(self, all_options):
        opt = next(o for o in all_options if o["key"] == "general:border_size")
        assert "description" in opt
        assert len(opt["description"]) > 0

    def test_overlay_type_override_preserved(self, all_options):
        """Type promotions in overlay should not be overwritten by schema."""
        opt = next(o for o in all_options if o["key"] == "general:gaps_in")
        assert opt["type"] == "int"  # schema says string

    def test_overlay_range_override_preserved(self, all_options):
        opt = next(o for o in all_options if o["key"] == "decoration:rounding")
        assert opt["max"] == 50  # schema says 20

    def test_overlay_choice_override_preserved(self, all_options):
        opt = next(o for o in all_options if o["key"] == "misc:vrr")
        assert opt["type"] == "choice"  # schema says int
        assert opt["default"] == 0

    def test_vec2_type_preserved(self, all_options):
        """vec2 type from schema should be preserved."""
        opt = next(o for o in all_options if o["key"] == "decoration:shadow:offset")
        assert opt["type"] == "vec2"


class TestDropUnavailable:
    """Verify that options/sections/groups unmatched by the running schema are
    dropped, so older Hyprland versions don't render empty sidebar pages (the
    Scrolling-on-0.49 bug: layout didn't exist yet, so every option lacked a
    ``type`` and the page showed only section titles)."""

    def test_option_without_type_is_dropped(self):
        overlay = {
            "groups": [
                {
                    "id": "g",
                    "label": "G",
                    "sections": [
                        {
                            "id": "s",
                            "label": "S",
                            "options": [
                                {"key": "a", "label": "A", "type": "bool", "default": False},
                                {"key": "b", "label": "B"},  # no type — unavailable
                            ],
                        }
                    ],
                }
            ]
        }
        _drop_unavailable(overlay)
        keys = [o["key"] for o in overlay["groups"][0]["sections"][0]["options"]]
        assert keys == ["a"]

    def test_empty_section_is_dropped(self):
        overlay = {
            "groups": [
                {
                    "id": "g",
                    "label": "G",
                    "sections": [
                        {
                            "id": "keep",
                            "label": "Keep",
                            "options": [
                                {"key": "a", "label": "A", "type": "bool", "default": False},
                            ],
                        },
                        {
                            "id": "empty",
                            "label": "Empty",
                            "options": [{"key": "b", "label": "B"}],
                        },
                    ],
                }
            ]
        }
        _drop_unavailable(overlay)
        section_ids = [s["id"] for s in overlay["groups"][0]["sections"]]
        assert section_ids == ["keep"]

    def test_empty_group_is_dropped(self):
        """Group with only unavailable options is removed entirely — the
        sidebar iterates ``schema.groups`` so the entry disappears."""
        overlay = {
            "groups": [
                {
                    "id": "keep",
                    "label": "Keep",
                    "sections": [
                        {
                            "id": "s",
                            "label": "S",
                            "options": [
                                {"key": "a", "label": "A", "type": "bool", "default": False},
                            ],
                        }
                    ],
                },
                {
                    "id": "scrolling",
                    "label": "Scrolling",
                    "sections": [
                        {
                            "id": "scrolling",
                            "label": "Columns",
                            "options": [{"key": "scrolling:column_width", "label": "Width"}],
                        },
                        {
                            "id": "scrolling:focus",
                            "label": "Focus",
                            "options": [{"key": "scrolling:follow_focus", "label": "Follow"}],
                        },
                    ],
                },
            ]
        }
        _drop_unavailable(overlay)
        group_ids = [g["id"] for g in overlay["groups"]]
        assert group_ids == ["keep"]
