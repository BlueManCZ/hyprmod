"""Tests for projection presets: the pure layout rules, the store, and the page wiring."""

import json
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

from hyprland_monitors import MonitorState

from hyprmod.pages.monitors.page import MonitorsPage
from hyprmod.pages.monitors.presets import (
    NO_PRESET,
    PresetStore,
    default_layout,
    matches_preset,
)


def _monitor(name: str, **overrides) -> MonitorState:
    fields = {
        "name": name,
        "make": "Acme",
        "model": "Pixel",
        "width": 1920,
        "height": 1080,
        "refresh_rate": 60.0,
        "x": 0,
        "y": 0,
        "scale": 1.0,
    }
    return MonitorState(**(fields | overrides))


def _page(monitors: list[MonitorState]) -> MonitorsPage:
    ipc = MagicMock()
    ipc.get_all.return_value = monitors
    ipc.apply.return_value = True
    window = SimpleNamespace(
        hypr=SimpleNamespace(monitors=ipc, on_change=MagicMock(), document=None),
        show_toast=MagicMock(),
        show_bug_toast=MagicMock(),
        saved_sections={},
    )
    return MonitorsPage(window)


class TestMatchesPreset:
    def test_only_requires_exactly_that_monitor_enabled(self):
        monitors = [_monitor("DP-1"), _monitor("DP-2", disabled=True)]
        assert matches_preset("only:DP-1", monitors)
        assert not matches_preset("only:DP-2", monitors)

    def test_only_for_a_disconnected_monitor_never_matches(self):
        assert not matches_preset("only:HDMI-A-1", [_monitor("DP-1")])

    def test_extend_rejects_disabled_and_mirrored(self):
        assert matches_preset("extend", [_monitor("DP-1"), _monitor("DP-2", x=1920)])
        assert not matches_preset("extend", [_monitor("DP-1"), _monitor("DP-2", disabled=True)])
        assert not matches_preset("extend", [_monitor("DP-1"), _monitor("DP-2", mirror_of="DP-1")])

    def test_no_preset_matches_anything(self):
        assert matches_preset(NO_PRESET, [_monitor("DP-1", disabled=True)])


class TestDefaultLayout:
    def test_extend_places_monitors_in_natural_name_order(self):
        monitors = [_monitor("DP-10"), _monitor("DP-2", scale=2.0)]
        layout = default_layout("extend", monitors)
        # DP-2 comes first and is 960 logical pixels wide at scale 2.
        assert (layout["DP-2"]["x"], layout["DP-10"]["x"]) == (0, 960)

    def test_extend_gives_a_never_enabled_monitor_its_preferred_mode(self):
        off = _monitor(
            "DP-1", width=0, height=0, disabled=True, available_modes=("2560x1440@144.00Hz",)
        )
        layout = default_layout("extend", [off, _monitor("DP-2")])
        assert (layout["DP-1"]["width"], layout["DP-1"]["height"]) == (2560, 1440)
        assert layout["DP-2"]["x"] == 2560

    def test_only_disables_every_other_monitor(self):
        layout = default_layout("only:DP-2", [_monitor("DP-1"), _monitor("DP-2")])
        assert layout == {"DP-1": {"disabled": True}, "DP-2": {"disabled": False}}


class TestPresetStore:
    def test_round_trip(self):
        store = PresetStore.load()
        store.active = "extend"
        store.remember("extend", [_monitor("DP-1", x=100)], ("x", "y"))
        store.save()

        loaded = PresetStore.load()
        assert loaded.active == "extend"
        assert loaded.layouts == {"extend": {"DP-1": {"x": 100, "y": 0}}}

    def test_corrupt_file_falls_back_to_no_preset(self, preset_file_tmp):
        preset_file_tmp.write_text("{not json")
        store = PresetStore.load()
        assert (store.active, store.layouts) == (NO_PRESET, {})

    def test_no_preset_never_gets_a_slot(self):
        store = PresetStore.load()
        store.remember(NO_PRESET, [_monitor("DP-1")], ("x",))
        assert store.layouts == {}

    def test_auto_position_is_stored_as_explicit_coordinates(self):
        store = PresetStore.load()
        store.remember("extend", [_monitor("DP-1", position="auto")], ("x", "position"))
        assert store.layouts["extend"]["DP-1"]["position"] is None


class TestPageWiring:
    def test_stale_active_preset_is_dropped_on_startup(self, preset_file_tmp):
        preset_file_tmp.write_text(json.dumps({"active": "extend", "layouts": {}}))
        page = _page([_monitor("DP-1"), _monitor("DP-2", disabled=True)])
        assert page._active_preset == NO_PRESET

    def test_matching_active_preset_survives_startup(self, preset_file_tmp):
        preset_file_tmp.write_text(json.dumps({"active": "extend", "layouts": {}}))
        page = _page([_monitor("DP-1"), _monitor("DP-2", x=1920)])
        assert page._active_preset == "extend"

    def test_moving_a_monitor_without_a_preset_leaves_the_others_unowned(self):
        page = _page([_monitor("DP-1"), _monitor("DP-2", x=1920)])
        page._apply_change_for_card(page._monitors[0], {"y": 200})
        assert page._ownership.owned == {"DP-1"}

    def test_moving_a_monitor_under_a_preset_pins_the_others(self):
        page = _page([_monitor("DP-1"), _monitor("DP-2", x=1920)])
        page._on_preset_selected("extend")
        page._apply_change_for_card(page._monitors[0], {"y": 200})
        assert page._ownership.owned == {"DP-1", "DP-2"}

    def test_extend_keeps_an_already_extended_arrangement(self):
        page = _page([_monitor("DP-1", x=1920, y=300), _monitor("DP-2")])
        page._on_preset_selected("extend")
        cast(MagicMock, page._window.hypr.monitors.apply).assert_not_called()
        assert (page._monitors[0].x, page._monitors[0].y) == (1920, 300)

    def test_broken_outgoing_layout_is_not_remembered(self):
        page = _page([_monitor("DP-1"), _monitor("DP-2", x=1920)])
        page._on_preset_selected("extend")
        # Bypass the page so the layout stops being "extend" without the preset noticing.
        page._monitors[1].disabled = True
        page._on_preset_selected(NO_PRESET)
        assert "extend" not in page._presets.layouts
