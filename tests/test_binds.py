"""Tests for keybind override tracking and dispatcher presentation data."""

from gi.repository import Adw
from hyprland_config import BindData, Document, parse_string
from hyprland_socket import MOD_BITS, Bind

from hyprmod.binds import (
    BIND_TYPES,
    BINDM_DISPATCHERS,
    CATEGORY_BY_ID,
    DISPATCHER_CATEGORIES,
    DISPATCHER_INFO,
    GDK_BUTTON_TO_MOUSE_KEY,
    KEY_BIND_TYPES,
    MOUSE_BUTTON_PRESETS,
    OverrideTracker,
    bind_dispatcher_label,
    categorize_bind,
    categorize_dispatcher,
    dispatcher_label,
    enrich_lua_binds,
    format_action,
    format_bind_action,
    live_bind_to_data,
)
from hyprmod.binds.dialog import _build_resize_arg, _parse_resize_arg
from hyprmod.binds.gdk_modifiers import keysym_name

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mkbind(mods, key, dispatcher, arg="", bind_type="bind"):
    return BindData(
        mods=mods,
        key=key,
        dispatcher=dispatcher,
        arg=arg,
        bind_type=bind_type,
    )


# ---------------------------------------------------------------------------
# OverrideTracker — get_bind_lines
# ---------------------------------------------------------------------------


class TestGetBindLines:
    def test_no_hypr_binds(self):
        tracker = OverrideTracker([])
        owned = [_mkbind(["SUPER"], "T", "exec", "kitty")]
        lines = tracker.get_bind_lines(owned)
        assert not any("unbind" in line for line in lines)
        assert len(lines) == 1

    def test_same_combo_override(self):
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        tracker = OverrideTracker(hypr)
        owned = [_mkbind(["SUPER"], "Q", "killactive")]
        lines = tracker.get_bind_lines(owned)
        assert lines[0] == "unbind = SUPER, Q"
        assert "killactive" in lines[1]

    def test_changed_combo_override(self):
        hypr_bind = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_bind])
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker.add_override(0, hypr_bind)
        lines = tracker.get_bind_lines(owned)
        unbind_lines = [ln for ln in lines if "unbind" in ln]
        assert any("SUPER, Q" in ln for ln in unbind_lines)

    def test_no_duplicate_unbind(self):
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        tracker = OverrideTracker(hypr)
        owned = [
            _mkbind(["SUPER"], "Q", "killactive"),
            _mkbind(["SUPER"], "Q", "exec", "something"),
        ]
        lines = tracker.get_bind_lines(owned)
        assert sum(1 for line in lines if "unbind" in line) == 1

    def test_mixed_override_and_new(self):
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        tracker = OverrideTracker(hypr)
        owned = [
            _mkbind(["SUPER"], "Q", "killactive"),
            _mkbind(["SUPER"], "T", "exec", "kitty"),
        ]
        lines = tracker.get_bind_lines(owned)
        unbind_lines = [ln for ln in lines if "unbind" in ln]
        assert len(unbind_lines) == 1
        assert "SUPER, Q" in unbind_lines[0]

    def test_unbind_precedes_its_matching_bind(self):
        # Adjacency matters: parse_saved_overrides pairs each unbind with
        # the *next* bind it sees, so any unrelated bind in between would
        # silently misattribute the override. The non-overridden bind that
        # comes first in owned must not appear between unbind and the
        # actual override.
        hypr = [_mkbind(["CTRL", "ALT"], "R", "exec", "meru-original")]
        tracker = OverrideTracker(hypr)
        owned = [
            _mkbind(["MOD3"], "G", "exec", "ghostty"),
            _mkbind(["CTRL", "ALT"], "R", "exec", "meru"),
        ]
        lines = tracker.get_bind_lines(owned)
        # The unbind sits immediately before its replacement bind.
        unbind_idx = next(i for i, ln in enumerate(lines) if ln.startswith("unbind"))
        meru_idx = next(i for i, ln in enumerate(lines) if "meru" in ln and ln.startswith("bind"))
        assert unbind_idx + 1 == meru_idx
        # And the unrelated bind isn't sandwiched between them.
        ghostty_idx = next(i for i, ln in enumerate(lines) if "ghostty" in ln)
        assert ghostty_idx < unbind_idx


# ---------------------------------------------------------------------------
# OverrideTracker — config parsing
# ---------------------------------------------------------------------------


class TestOverrideParsing:
    """Test that unbind+bind pairs in config are correctly parsed as overrides."""

    @staticmethod
    def _parse(tmp_path, config_text, owned_binds, all_hypr_binds):
        """Helper: replicate config parsing logic via OverrideTracker."""
        path = tmp_path / "hyprland-gui.conf"
        path.write_text(config_text)
        tracker = OverrideTracker(all_hypr_binds, managed_path=path)
        tracker.parse_saved_overrides(owned_binds)
        return tracker

    def test_same_combo_override(self, tmp_path):
        config_text = "unbind = SUPER, Q\nbind = SUPER, Q, exec, my-close-script\n"
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER"], "Q", "exec", "my-close-script")]
        tracker = self._parse(tmp_path, config_text, owned, hypr)
        assert tracker.has_original(0)

    def test_changed_combo_override(self, tmp_path):
        config_text = "unbind = SUPER, Q\nbind = SUPER SHIFT, Q, killactive,\n"
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker = self._parse(tmp_path, config_text, owned, hypr)
        assert tracker.has_original(0)

    def test_regular_bind_not_override(self, tmp_path):
        config_text = "bind = SUPER, T, exec, kitty\n"
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER"], "T", "exec", "kitty")]
        tracker = self._parse(tmp_path, config_text, owned, hypr)
        assert not tracker.has_original(0)

    def test_unbind_without_matching_hypr(self, tmp_path):
        """When neither live binds nor config document have the original, no override is tracked."""
        config_text = "unbind = SUPER, Z\nbind = SUPER, Z, exec, something\n"
        tracker = self._parse(
            tmp_path, config_text, [_mkbind(["SUPER"], "Z", "exec", "something")], []
        )
        assert not tracker.has_original(0)

    def test_multiple_binds_mixed(self, tmp_path):
        config_text = (
            "unbind = SUPER, Q\nbind = SUPER SHIFT, Q, killactive,\n"
            "bind = SUPER, T, exec, kitty\n"
            "unbind = SUPER, V\nbind = SUPER, V, togglefloating,\n"
        )
        hypr = [
            _mkbind(["SUPER"], "Q", "killactive"),
            _mkbind(["SUPER"], "V", "togglefloating"),
        ]
        owned = [
            _mkbind(["SUPER", "SHIFT"], "Q", "killactive"),
            _mkbind(["SUPER"], "T", "exec", "kitty"),
            _mkbind(["SUPER"], "V", "togglefloating"),
        ]
        tracker = self._parse(tmp_path, config_text, owned, hypr)
        assert tracker.has_original(0)
        assert not tracker.has_original(1)
        assert tracker.has_original(2)

    def test_comment_between_unbind_and_bind(self, tmp_path):
        config_text = "unbind = SUPER, Q\n# comment\nbind = SUPER SHIFT, Q, killactive,\n"
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker = self._parse(tmp_path, config_text, owned, hypr)
        assert tracker.has_original(0)

    def test_option_between_unbind_and_bind_still_pairs(self, tmp_path):
        # Unrelated assignments between an unbind and the next bind don't
        # break the override pairing — the user clearly intended the
        # unbind+bind as a changed-combo override regardless of what's
        # interleaved between. The parser prefers same-combo matches first
        # and falls back to next-following-bind, which is robust against
        # config noise between the two lines.
        config_text = (
            "unbind = SUPER, Q\ngeneral:gaps_out = 5\nbind = SUPER SHIFT, Q, killactive,\n"
        )
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker = self._parse(tmp_path, config_text, owned, hypr)
        assert tracker.has_original(0)

    def test_unbind_pairs_with_same_combo_bind_not_intervening_bind(self, tmp_path):
        # Reproduces the older layout's pairing trap: when an unbind sits
        # ahead of an unrelated bind followed by the actual replacement,
        # the parser must pair the unbind with the *same-combo* bind, not
        # with the first bind it encounters.
        config_text = (
            "unbind = CTRL ALT, R\n"
            "bind = ALT MOD3, G, exec, ghostty\n"
            "bind = CTRL ALT, R, exec, meru\n"
        )
        hypr = [_mkbind(["CTRL", "ALT"], "R", "exec", "meru-original")]
        owned = [
            _mkbind(["ALT", "MOD3"], "G", "exec", "ghostty"),
            _mkbind(["CTRL", "ALT"], "R", "exec", "meru"),
        ]
        tracker = self._parse(tmp_path, config_text, owned, hypr)
        # The G bind isn't an override of anything.
        assert not tracker.has_original(0)
        # The R bind correctly takes the unbind as its override marker.
        assert tracker.has_original(1)


# ---------------------------------------------------------------------------
# OverrideTracker — filter_hypr_binds
# ---------------------------------------------------------------------------


class TestRefilterHyprBinds:
    def test_owned_bind_filtered(self):
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        tracker = OverrideTracker(hypr)
        assert len(tracker.filter_hypr_binds([_mkbind(["SUPER"], "Q", "killactive")])) == 0

    def test_unrelated_hypr_bind_kept(self):
        hypr = [
            _mkbind(["SUPER"], "Q", "killactive"),
            _mkbind(["SUPER"], "M", "exit"),
        ]
        tracker = OverrideTracker(hypr)
        filtered = tracker.filter_hypr_binds([_mkbind(["SUPER"], "Q", "killactive")])
        assert len(filtered) == 1
        assert filtered[0].key == "M"

    def test_changed_combo_override_filtered_session(self):
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q])
        tracker.add_override(0, hypr_q)
        filtered = tracker.filter_hypr_binds([_mkbind(["SUPER", "SHIFT"], "Q", "killactive")])
        assert len(filtered) == 0

    def test_deleted_override_restores_visibility(self):
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q])
        assert len(tracker.filter_hypr_binds([])) == 1

    def test_saved_override_filtered(self, tmp_path):
        """Original combo filtered via saved unbind originals after mark_saved."""
        # Simulate save: write the managed config first, then drive the
        # tracker against that path.
        path = tmp_path / "hyprland-gui.conf"
        path.write_text("unbind = SUPER, Q\nbind = SUPER SHIFT, Q, killactive,\n")
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q], managed_path=path)
        tracker.add_override(0, hypr_q)
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker.mark_saved(owned)

        filtered = tracker.filter_hypr_binds(owned)
        assert len(filtered) == 0


# ---------------------------------------------------------------------------
# OverrideTracker — has_original
# ---------------------------------------------------------------------------


class TestHasHyprOriginal:
    def test_session_override(self):
        hypr_bind = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_bind])
        tracker.add_override(0, hypr_bind)
        assert tracker.has_original(0)

    def test_not_override(self):
        tracker = OverrideTracker([])
        assert not tracker.has_original(0)

    def test_different_index(self):
        hypr_bind = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_bind])
        tracker.add_override(0, hypr_bind)
        assert not tracker.has_original(1)


# ---------------------------------------------------------------------------
# OverrideTracker — remove_at (reindexing)
# ---------------------------------------------------------------------------


class TestReindexAfterDelete:
    def test_delete_first(self):
        hb_q = _mkbind(["SUPER"], "Q", "killactive")
        hb_v = _mkbind(["SUPER"], "V", "togglefloating")
        tracker = OverrideTracker([hb_q, hb_v])
        tracker.add_override(0, hb_q)
        tracker.add_override(2, hb_v)

        original = tracker.remove_at(0)
        assert original is hb_q
        assert not tracker.has_original(0)  # was index 1, not an override
        assert tracker.has_original(1)  # was index 2, shifted down

    def test_delete_middle(self):
        hb = _mkbind(["SUPER"], "V", "togglefloating")
        tracker = OverrideTracker([hb])
        tracker.add_override(2, hb)

        original = tracker.remove_at(1)
        assert original is None
        assert tracker.has_original(1)  # was index 2

    def test_delete_last_no_shift(self):
        hb = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hb])
        tracker.add_override(0, hb)

        original = tracker.remove_at(5)
        assert original is None
        assert tracker.has_original(0)  # unchanged


# ---------------------------------------------------------------------------
# End-to-end override flow
# ---------------------------------------------------------------------------


class TestOverrideFlow:
    def test_override_same_combo_then_delete(self):
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q])
        owned = []

        # Override
        owned.append(_mkbind(["SUPER"], "Q", "exec", "my-close"))
        tracker.add_override(0, hypr_q)
        assert len(tracker.filter_hypr_binds(owned)) == 0
        assert tracker.has_original(0)

        # Delete
        owned.pop(0)
        original = tracker.remove_at(0)
        assert original is hypr_q
        assert len(tracker.filter_hypr_binds(owned)) == 1

    def test_override_changed_combo_then_delete(self):
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q])
        owned = []

        # Override with changed combo
        owned.append(_mkbind(["SUPER", "SHIFT"], "Q", "killactive"))
        tracker.add_override(0, hypr_q)
        assert len(tracker.filter_hypr_binds(owned)) == 0

        # Delete
        owned.pop(0)
        original = tracker.remove_at(0)
        assert original is hypr_q
        filtered = tracker.filter_hypr_binds(owned)
        assert len(filtered) == 1
        assert filtered[0].combo == (("SUPER",), "Q")

    def test_override_save_then_delete(self, tmp_path):
        # Save first so the managed-config path exists at construction time;
        # then drive the tracker against it.
        path = tmp_path / "hyprland-gui.conf"
        path.write_text("unbind = SUPER, Q\nbind = SUPER SHIFT, Q, killactive,\n")
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q], managed_path=path)
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker.add_override(0, hypr_q)
        tracker.mark_saved(owned)

        assert tracker.has_original(0)
        assert len(tracker.filter_hypr_binds(owned)) == 0

        # Delete
        owned.pop(0)
        original = tracker.remove_at(0)
        assert original is not None
        assert len(tracker.filter_hypr_binds(owned)) == 1

    def test_new_bind_not_override(self):
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q])
        owned = [_mkbind(["SUPER"], "T", "exec", "kitty")]

        assert not tracker.has_original(0)
        filtered = tracker.filter_hypr_binds(owned)
        assert len(filtered) == 1
        assert filtered[0].key == "Q"

    def test_multiple_overrides_delete_first(self):
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        hypr_v = _mkbind(["SUPER"], "V", "togglefloating")
        tracker = OverrideTracker([hypr_q, hypr_v])
        owned = []

        owned.append(_mkbind(["SUPER", "SHIFT"], "Q", "killactive"))
        tracker.add_override(0, hypr_q)
        owned.append(_mkbind(["SUPER"], "V", "exec", "my-float"))
        tracker.add_override(1, hypr_v)

        # Delete first
        owned.pop(0)
        original = tracker.remove_at(0)
        assert original is not None
        assert original.combo == (("SUPER",), "Q")
        assert tracker.has_original(0)  # shifted from index 1

        filtered = tracker.filter_hypr_binds(owned)
        assert len(filtered) == 1
        assert filtered[0].key == "Q"

    def test_discard_restores_all(self):
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q])

        tracker.add_override(0, hypr_q)
        originals = tracker.clear_session_overrides()

        assert len(originals) == 1
        assert originals[0].key == "Q"
        assert len(tracker.filter_hypr_binds([])) == 1


# ---------------------------------------------------------------------------
# Dispatcher presentation data tests
# ---------------------------------------------------------------------------


class TestBindTypes:
    def test_all_types_present(self):
        expected = {"bind", "binde", "bindm", "bindl", "bindr", "bindn"}
        assert set(BIND_TYPES.keys()) == expected


class TestDispatchers:
    def test_categorize_known(self):
        assert categorize_dispatcher("exec") == "apps"
        assert categorize_dispatcher("killactive") == "window_mgmt"

    def test_categorize_unknown_defaults_to_advanced(self):
        assert categorize_dispatcher("nonexistent") == "advanced"

    def test_dispatcher_label_known(self):
        assert dispatcher_label("exec") == "Run command"

    def test_dispatcher_label_unknown_returns_name(self):
        assert dispatcher_label("foobar") == "foobar"

    def test_categories_have_ids(self):
        for cat in DISPATCHER_CATEGORIES:
            assert "id" in cat
            assert cat["id"] in CATEGORY_BY_ID

    def test_dispatcher_info_has_category(self):
        for name, info in DISPATCHER_INFO.items():
            assert "category_id" in info

    def test_format_action_with_arg(self):
        assert format_action("exec", "firefox") == "Run command: firefox"

    def test_format_action_no_arg(self):
        assert format_action("killactive", "") == "Close window"

    def test_format_action_unknown_dispatcher(self):
        assert format_action("foobar", "") == "foobar"


# ---------------------------------------------------------------------------
# Live bind -> BindData conversion
# ---------------------------------------------------------------------------


SUPER_MASK = MOD_BITS["SUPER"]


class TestLiveBindToData:
    def test_plain_bind_passes_through(self):
        bind = Bind(modmask=SUPER_MASK, key="Q", dispatcher="killactive", arg="")
        bd = live_bind_to_data(bind)
        assert bd.bind_type == "bind"
        assert bd.mods == ["SUPER"]
        assert bd.key == "Q"
        assert bd.dispatcher == "killactive"
        assert bd.arg == ""

    def test_bindm_unwraps_dispatcher_and_arg(self):
        """Hyprland reports bindm as ``dispatcher='mouse', arg='movewindow'``."""
        bind = Bind(
            modmask=SUPER_MASK,
            key="mouse:272",
            dispatcher="mouse",
            arg="movewindow",
            mouse=True,
        )
        bd = live_bind_to_data(bind)
        assert bd.bind_type == "bindm"
        assert bd.dispatcher == "movewindow"
        assert bd.arg == ""

    def test_bindm_round_trip_emits_no_trailing_comma(self):
        """Roundtripping a live bindm must not produce ``bindm = ..., movewindow,``."""
        bind = Bind(
            modmask=SUPER_MASK,
            key="mouse:273",
            dispatcher="mouse",
            arg="resizewindow",
            mouse=True,
        )
        bd = live_bind_to_data(bind)
        assert bd.to_line() == "bindm = SUPER, mouse:273, resizewindow"

    def test_binde_flag(self):
        bind = Bind(modmask=SUPER_MASK, key="L", dispatcher="resizeactive", arg="10 0", repeat=True)
        assert live_bind_to_data(bind).bind_type == "binde"

    def test_bindl_flag(self):
        bind = Bind(modmask=0, key="XF86AudioPlay", dispatcher="exec", arg="playerctl", locked=True)
        assert live_bind_to_data(bind).bind_type == "bindl"

    def test_bindr_flag(self):
        bind = Bind(modmask=SUPER_MASK, key="A", dispatcher="exec", arg="x", release=True)
        assert live_bind_to_data(bind).bind_type == "bindr"

    def test_bindn_flag(self):
        bind = Bind(modmask=0, key="A", dispatcher="exec", arg="x", non_consuming=True)
        assert live_bind_to_data(bind).bind_type == "bindn"

    def test_no_modmask_yields_empty_mods(self):
        bind = Bind(modmask=0, key="Print", dispatcher="exec", arg="screenshot")
        assert live_bind_to_data(bind).mods == []


class TestEnrichLuaBinds:
    """In Lua mode Hyprland reports ``dispatcher='__lua'`` for every bind —
    the runtime stores them as closures. Real dispatcher info has to come
    from the parsed source via the Lua reader's Document output.
    """

    def test_passes_through_when_no_lua_dispatcher(self):
        live = [_mkbind(["SUPER"], "Q", "killactive")]
        out = enrich_lua_binds(live, Document())
        assert out is live  # short-circuit: no work to do

    def test_replaces_lua_dispatcher_with_parsed_one(self):
        doc = parse_string("bind = SUPER, Q, killactive,\nbind = SUPER, return, exec, kitty\n")
        live = [
            _mkbind(["SUPER"], "Q", "__lua", arg="5"),
            _mkbind(["SUPER"], "return", "__lua", arg="6"),
        ]
        out = enrich_lua_binds(live, doc)
        assert [(b.dispatcher, b.arg) for b in out] == [
            ("killactive", ""),
            ("exec", "kitty"),
        ]

    def test_preserves_bind_type_from_live(self):
        # Document spells it ``bindm = …`` (mouse drag); live bind has the
        # right ``bind_type`` already. Enrichment must keep the live type
        # (authoritative for which variant is registered) but adopt the
        # document's dispatcher.
        doc = parse_string("bindm = SUPER, mouse:272, movewindow\n")
        live = [_mkbind(["SUPER"], "mouse:272", "__lua", arg="3", bind_type="bindm")]
        out = enrich_lua_binds(live, doc)
        assert out[0].bind_type == "bindm"
        assert out[0].dispatcher == "movewindow"

    def test_unmatched_combo_falls_through(self):
        # No matching bind in the document — keep the live bind so the
        # user at least sees that the bind exists.
        live = [_mkbind(["SUPER"], "X", "__lua", arg="99")]
        out = enrich_lua_binds(live, Document())
        assert out == live


# ---------------------------------------------------------------------------
# Mouse-drag categorisation, dispatchers, and presets
# ---------------------------------------------------------------------------


class TestCategorizeBind:
    def test_bindm_always_categorises_as_mouse_button(self):
        # ``movewindow`` lives in window_focus for keyboard binds, but for
        # bindm it must bucket under mouse_button regardless of dispatcher.
        assert categorize_bind("bindm", "movewindow") == "mouse_button"
        assert categorize_bind("bindm", "resizewindow") == "mouse_button"
        # Even if the dispatcher is unknown, bindm forces mouse_button.
        assert categorize_bind("bindm", "weird_custom") == "mouse_button"

    def test_keyboard_bind_uses_dispatcher_category(self):
        assert categorize_bind("bind", "movewindow") == "window_focus"
        assert categorize_bind("bind", "killactive") == "window_mgmt"
        assert categorize_bind("binde", "resizeactive") == "window_focus"

    def test_unknown_dispatcher_falls_back_to_advanced(self):
        assert categorize_bind("bind", "no_such_dispatcher") == "advanced"


class TestBindDispatcherLabel:
    def test_bindm_uses_bindm_label(self):
        # In bindm mode movewindow is presented as the drag action,
        # not as the directional ``Move window`` keyboard variant.
        assert bind_dispatcher_label("bindm", "movewindow") == "Move window"
        assert bind_dispatcher_label("bindm", "resizewindow") == "Resize window"

    def test_keyboard_bind_uses_default_label(self):
        assert bind_dispatcher_label("bind", "killactive") == "Close window"
        assert bind_dispatcher_label("bind", "exec") == "Run command"

    def test_bindm_unknown_dispatcher_falls_through(self):
        assert bind_dispatcher_label("bindm", "weird_thing") == "weird_thing"


class TestFormatBindAction:
    def test_bindm_no_arg(self):
        assert format_bind_action("bindm", "movewindow", "") == "Move window"

    def test_bindm_with_arg(self):
        # Hyprland's stock bindm dispatchers don't take args but the format
        # helper still composes "label: arg" for completeness.
        assert format_bind_action("bindm", "movewindow", "x") == "Move window: x"

    def test_keyboard_bind(self):
        assert format_bind_action("bind", "exec", "firefox") == "Run command: firefox"


class TestMouseDragCategoryRegistered:
    def test_category_present_in_DISPATCHER_CATEGORIES(self):
        ids = [c["id"] for c in DISPATCHER_CATEGORIES]
        assert "mouse_button" in ids

    def test_category_in_lookup(self):
        assert "mouse_button" in CATEGORY_BY_ID
        assert CATEGORY_BY_ID["mouse_button"]["label"] == "Mouse Button"

    def test_category_dispatchers_kept_empty(self):
        # ``movewindow`` and ``resizewindow`` would clobber their entries in
        # the keyboard categories of the flat ``DISPATCHER_INFO`` lookup, so
        # the mouse_button category is intentionally empty in DISPATCHER_INFO.
        assert "movewindow" in DISPATCHER_INFO
        assert DISPATCHER_INFO["movewindow"]["category_id"] != "mouse_button"


class TestMouseButtonPresets:
    def test_left_right_middle_present(self):
        values = {v for v, _ in MOUSE_BUTTON_PRESETS}
        assert "mouse:272" in values  # Left
        assert "mouse:273" in values  # Right
        assert "mouse:274" in values  # Middle

    def test_gdk_button_map_covers_standard_buttons(self):
        # GtkGestureClick reports button 1 = left, 2 = middle, 3 = right.
        # The map must translate these to the matching ``mouse:NNN`` codes.
        assert GDK_BUTTON_TO_MOUSE_KEY[1] == "mouse:272"  # Left
        assert GDK_BUTTON_TO_MOUSE_KEY[2] == "mouse:274"  # Middle
        assert GDK_BUTTON_TO_MOUSE_KEY[3] == "mouse:273"  # Right


class TestBindmDispatchers:
    def test_movewindow_and_resizewindow_listed(self):
        assert "movewindow" in BINDM_DISPATCHERS
        assert "resizewindow" in BINDM_DISPATCHERS

    def test_key_bind_types_excludes_bindm(self):
        # The dialog's key-mode "Bind type" combo must not offer ``bindm``;
        # users reach it through the dedicated trigger toggle instead.
        assert "bindm" not in KEY_BIND_TYPES
        assert "bind" in KEY_BIND_TYPES
        assert "binde" in KEY_BIND_TYPES
        # Sanity: the full BIND_TYPES still includes bindm for serialisation.
        assert "bindm" in BIND_TYPES


# ---------------------------------------------------------------------------
# BindEditDialog — trigger mode wiring
# ---------------------------------------------------------------------------


class TestBindEditDialog:
    """Smoke tests for the dialog's trigger-mode plumbing.

    Instantiating ``BindEditDialog`` requires libadwaita, so the tests
    init it once and reuse a minimal fake window object.
    """

    @staticmethod
    def _dialog(bind=None, initial_category=""):
        # Local imports keep the GTK init out of the module-import critical
        # path (a few unrelated tests in this file are pure data).
        import gi

        gi.require_version("Adw", "1")
        gi.require_version("Gtk", "4.0")
        from gi.repository import Adw

        from hyprmod.binds.dialog import BindEditDialog

        Adw.init()

        class _Hypr:
            def keyword(self, *a):
                pass

            def dispatch(self, *a):
                pass

        class _Window:
            hypr = _Hypr()

            def show_toast(self, *a, **k):
                pass

            def show_bug_toast(self, *a, **k):
                pass

            def get_root(self):
                return None

        return BindEditDialog(bind=bind, window=_Window(), initial_category=initial_category)

    def test_new_bind_defaults_to_key_mode(self):
        d = self._dialog()
        assert d._is_mouse_mode is False
        assert d.get_bind().bind_type == "bind"

    def test_initial_category_mouse_button_starts_in_mouse_mode(self):
        d = self._dialog(initial_category="mouse_button")
        assert d._is_mouse_mode is True
        b = d.get_bind()
        assert b.bind_type == "bindm"
        # No button is prefilled — the user must pick one. This mirrors the
        # empty key entry shown in keyboard mode for new binds; otherwise
        # the capture label would read back whatever happens to sit at index
        # 0 (e.g. "mouse:272") as if the user already configured it.
        assert b.key == ""

    def test_new_mouse_bind_capture_label_is_none(self):
        d = self._dialog(initial_category="mouse_button")
        # Capture label is driven by ``format_shortcut`` of the current combo;
        # with nothing picked, it should read "(none)" — same as key mode.
        assert d._capture_label.get_label() == "(none)"

    def test_existing_bindm_opens_in_mouse_mode(self):
        existing = BindData(
            bind_type="bindm", mods=["SUPER"], key="mouse:273", dispatcher="resizewindow"
        )
        d = self._dialog(bind=existing)
        assert d._is_mouse_mode is True
        b = d.get_bind()
        assert b.bind_type == "bindm"
        assert b.key == "mouse:273"
        assert b.dispatcher == "resizewindow"

    def test_existing_regular_bind_opens_in_key_mode(self):
        existing = BindData(bind_type="bind", mods=["SUPER"], key="Q", dispatcher="killactive")
        d = self._dialog(bind=existing)
        assert d._is_mouse_mode is False
        b = d.get_bind()
        assert b.bind_type == "bind"
        assert b.key == "Q"

    def test_toggle_to_mouse_swaps_action_list_and_hides_arg(self):
        d = self._dialog()
        d._trigger_mouse_btn.set_active(True)
        assert d._is_mouse_mode is True
        assert "movewindow" in d._current_dispatcher_keys
        assert "resizewindow" in d._current_dispatcher_keys
        # bindm dispatchers take no argument.
        assert d._arg_container.get_visible() is False
        # Bind-type Advanced group is meaningless for bindm.
        assert d._adv_group.get_visible() is False

    def test_toggle_back_to_key_restores_bind_type(self):
        d = self._dialog()
        d._trigger_mouse_btn.set_active(True)
        d._trigger_key_btn.set_active(True)
        assert d._is_mouse_mode is False
        assert d.get_bind().bind_type == "bind"

    def test_custom_mouse_button_round_trips(self):
        existing = BindData(
            bind_type="bindm", mods=["SUPER"], key="mouse:280", dispatcher="movewindow"
        )
        d = self._dialog(bind=existing)
        assert d.get_bind().key == "mouse:280"


# ---------------------------------------------------------------------------


class TestKeysymName:
    """Captured keysyms must be spelled the way Hyprland's xkb lookup expects.

    ``Gdk.keyval_name`` drops the ``XF86`` prefix from every vendor keysym,
    and Hyprland rejects the whole bind with "Unknown keysym" when it gets
    the GDK spelling.
    """

    def test_media_keys_keep_their_xf86_prefix(self):
        assert keysym_name(0x1008FF13) == "XF86AudioRaiseVolume"
        assert keysym_name(0x1008FF02) == "XF86MonBrightnessUp"

    def test_vt_switch_keeps_its_xf86_prefix(self):
        # The keysym from issue #69: Ctrl+Alt+F12 resolves to the VT switch.
        assert keysym_name(0x1008FE0C) == "XF86Switch_VT_12"

    def test_legacy_gdk_names_map_to_their_xkb_spelling(self):
        # GDK names these two after their pre-xkb spelling (``WindowClear``,
        # ``SelectButton``), so restoring the prefix alone would still leave
        # xkb without a match.
        assert keysym_name(0x1008FF55) == "XF86Clear"
        assert keysym_name(0x1008FFA0) == "XF86Select"

    def test_unnamed_vendor_keysyms_stay_hex(self):
        # GDK falls back to a "0x..." literal, which xkb parses as-is.
        assert keysym_name(0x1008FFFE) == "0x1008fffe"

    def test_ordinary_keysyms_are_untouched(self):
        assert keysym_name(0xFFC9) == "F12"
        assert keysym_name(0xFF0D) == "Return"
        assert keysym_name(0x0061) == "a"


# ---------------------------------------------------------------------------


class TestResizeArgWidget:
    """``resizeactive`` takes ``[exact] W H``, not a direction.

    It sits among direction-picker dispatchers in the same category, so the
    old free-text field invited the ``l`` from issue #70.
    """

    @staticmethod
    def _build(current_value: str):
        Adw.init()
        return _build_resize_arg(current_value)

    def test_parses_relative_and_exact(self):
        assert _parse_resize_arg("10 0") == (False, 10, 0)
        assert _parse_resize_arg("-10 0") == (False, -10, 0)
        assert _parse_resize_arg("exact 800 600") == (True, 800, 600)

    def test_rejects_values_it_cannot_render(self):
        assert _parse_resize_arg("l") is None
        assert _parse_resize_arg("10% 0") is None
        assert _parse_resize_arg("10") is None
        assert _parse_resize_arg("") is None

    def test_existing_values_round_trip(self):
        for value in ("10 0", "-30 0", "exact 800 600"):
            _widget, getter = self._build(value)
            assert getter() == value

    def test_new_bind_starts_at_zero(self):
        _widget, getter = self._build("")
        assert getter() == "0 0"

    def test_percentage_stays_editable_as_text(self):
        # Valid for Hyprland but with no spin-row form, so it must survive.
        widget, getter = self._build("10% 0")
        assert isinstance(widget, Adw.EntryRow)
        assert getter() == "10% 0"

    def test_direction_letter_does_not_survive(self):
        # The issue #70 value. Not a resize argument at all, so the editor
        # opens on its defaults rather than carrying it forward.
        _widget, getter = self._build("l")
        assert getter() == "0 0"
