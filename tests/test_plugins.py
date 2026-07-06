"""Tests for plugin options parsing, serialization, and config integration."""

from hyprmod.core.plugins import (
    PluginSetting,
    load_external_plugins,
    parse_plugin_options,
    serialize,
)


class TestPluginSetting:
    def test_to_line(self):
        setting = PluginSetting("dynamic-cursors", "mode", "stretch")
        assert setting.to_line() == "mode = stretch"


class TestParsePluginOptions:
    def test_basic_parsing(self):
        options = {
            "plugin:dynamic-cursors:mode": "stretch",
            "plugin:dynamic-cursors:threshold": "2",
            "misc:vfr": "true",
            "plugin:other:enabled": "false",
        }
        result = parse_plugin_options(options)
        assert len(result) == 3
        assert result[0] == PluginSetting("dynamic-cursors", "mode", "stretch")
        assert result[1] == PluginSetting("dynamic-cursors", "threshold", "2")
        assert result[2] == PluginSetting("other", "enabled", "false")

    def test_ignores_non_plugin_keys(self):
        options = {
            "general:border_size": "2",
            "decoration:rounding": "10",
        }
        assert parse_plugin_options(options) == []

    def test_ignores_malformed_plugin_keys(self):
        options = {
            "plugin:": "test",
            "plugin:name": "test",
        }
        assert parse_plugin_options(options) == []


class TestSerialize:
    def test_basic_serialization(self):
        settings = [
            PluginSetting("dynamic-cursors", "mode", "stretch"),
            PluginSetting("dynamic-cursors", "threshold", "2"),
            PluginSetting("other", "enabled", "false"),
        ]
        result = serialize(settings)
        expected = [
            "plugin {\n",
            "  dynamic-cursors {\n",
            "    mode = stretch\n",
            "    threshold = 2\n",
            "  }\n",
            "}\n",
            "plugin {\n",
            "  other {\n",
            "    enabled = false\n",
            "  }\n",
            "}\n",
        ]
        assert result == expected

    def test_empty_serialization(self):
        assert serialize([]) == []


class TestLoadExternalPlugins:
    def test_loads_external_plugin_settings(self, tmp_path):
        managed_path = tmp_path / "hyprmod.conf"
        managed_path.write_text("plugin { \n dynamic-cursors { mode = none } \n }")

        main_path = tmp_path / "hyprland.conf"
        main_path.write_text(
            f"""
source = {managed_path.absolute()}
plugin {{
    dynamic-cursors {{
        mode = stretch
        threshold = 2
    }}
    other {{
        enabled = true
    }}
}}
"""
        )

        external = load_external_plugins(main_path, managed_path)

        assert len(external) == 3

        assert external[0].setting == PluginSetting("dynamic-cursors", "mode", "stretch")
        assert external[0].source_path.resolve() == main_path.resolve()
        assert external[0].lineno == 5

        assert external[1].setting == PluginSetting("dynamic-cursors", "threshold", "2")
        assert external[1].lineno == 6

        assert external[2].setting == PluginSetting("other", "enabled", "true")
        assert external[2].lineno == 9

    def test_ignores_managed_path(self, tmp_path):
        managed_path = tmp_path / "hyprmod.conf"
        managed_path.write_text("plugin { \n dynamic-cursors { mode = none } \n }")
        main_path = tmp_path / "hyprland.conf"
        main_path.write_text(f"source = {managed_path.absolute()}")

        external = load_external_plugins(main_path, managed_path)
        assert len(external) == 0

    def test_returns_empty_when_root_path_not_exists(self, tmp_path):
        main_path = tmp_path / "hyprland.conf"
        managed_path = tmp_path / "hyprmod.conf"

        external = load_external_plugins(main_path, managed_path)
        assert len(external) == 0
