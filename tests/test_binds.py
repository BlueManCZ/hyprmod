"""Tests for keybind override tracking and dispatcher presentation data."""

from hyprland_config import BindData

from hyprmod.binds import (
    BIND_TYPES,
    CATEGORY_BY_ID,
    DISPATCHER_CATEGORIES,
    DISPATCHER_INFO,
    OverrideTracker,
    categorize_dispatcher,
    dispatcher_label,
    format_action,
)

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


# ---------------------------------------------------------------------------
# OverrideTracker — config parsing
# ---------------------------------------------------------------------------


class TestOverrideParsing:
    """Test that unbind+bind pairs in config are correctly parsed as overrides."""

    @staticmethod
    def _parse(config_text, owned_binds, all_hypr_binds):
        """Helper: replicate config parsing logic via OverrideTracker."""
        import os
        import tempfile
        from pathlib import Path

        from hyprmod.core.config import set_gui_conf

        # Write config to a temp file and temporarily set gui_conf
        fd, path = tempfile.mkstemp(suffix=".conf")
        try:
            os.write(fd, config_text.encode())
            os.close(fd)
            set_gui_conf(Path(path))
            tracker = OverrideTracker(all_hypr_binds)
            tracker.parse_saved_overrides(owned_binds)
            return tracker
        finally:
            set_gui_conf(None)
            os.unlink(path)

    def test_same_combo_override(self):
        config_text = "unbind = SUPER, Q\nbind = SUPER, Q, exec, my-close-script\n"
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER"], "Q", "exec", "my-close-script")]
        tracker = self._parse(config_text, owned, hypr)
        assert tracker.has_original(0)

    def test_changed_combo_override(self):
        config_text = "unbind = SUPER, Q\nbind = SUPER SHIFT, Q, killactive,\n"
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker = self._parse(config_text, owned, hypr)
        assert tracker.has_original(0)

    def test_regular_bind_not_override(self):
        config_text = "bind = SUPER, T, exec, kitty\n"
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER"], "T", "exec", "kitty")]
        tracker = self._parse(config_text, owned, hypr)
        assert not tracker.has_original(0)

    def test_unbind_without_matching_hypr(self):
        """When neither live binds nor config document have the original, no override is tracked."""
        config_text = "unbind = SUPER, Z\nbind = SUPER, Z, exec, something\n"
        tracker = self._parse(config_text, [_mkbind(["SUPER"], "Z", "exec", "something")], [])
        assert not tracker.has_original(0)

    def test_multiple_binds_mixed(self):
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
        tracker = self._parse(config_text, owned, hypr)
        assert tracker.has_original(0)
        assert not tracker.has_original(1)
        assert tracker.has_original(2)

    def test_comment_between_unbind_and_bind(self):
        config_text = "unbind = SUPER, Q\n# comment\nbind = SUPER SHIFT, Q, killactive,\n"
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker = self._parse(config_text, owned, hypr)
        assert tracker.has_original(0)

    def test_option_between_unbind_and_bind_breaks_pairing(self):
        config_text = (
            "unbind = SUPER, Q\ngeneral:gaps_out = 5\nbind = SUPER SHIFT, Q, killactive,\n"
        )
        hypr = [_mkbind(["SUPER"], "Q", "killactive")]
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker = self._parse(config_text, owned, hypr)
        assert not tracker.has_original(0)


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

    def test_saved_override_filtered(self):
        """Original combo filtered via saved unbind originals after mark_saved."""
        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q])
        tracker.add_override(0, hypr_q)
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]

        # Simulate save: clear session, re-parse
        import os
        import tempfile
        from pathlib import Path

        from hyprmod.core.config import set_gui_conf

        config_text = "unbind = SUPER, Q\nbind = SUPER SHIFT, Q, killactive,\n"
        fd, path = tempfile.mkstemp(suffix=".conf")
        try:
            os.write(fd, config_text.encode())
            os.close(fd)
            set_gui_conf(Path(path))
            tracker.mark_saved(owned)
        finally:
            set_gui_conf(None)
            os.unlink(path)

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

    def test_override_save_then_delete(self):
        import os
        import tempfile
        from pathlib import Path

        hypr_q = _mkbind(["SUPER"], "Q", "killactive")
        tracker = OverrideTracker([hypr_q])
        owned = [_mkbind(["SUPER", "SHIFT"], "Q", "killactive")]
        tracker.add_override(0, hypr_q)

        # Save
        from hyprmod.core.config import set_gui_conf

        config_text = "unbind = SUPER, Q\nbind = SUPER SHIFT, Q, killactive,\n"
        fd, path = tempfile.mkstemp(suffix=".conf")
        try:
            os.write(fd, config_text.encode())
            os.close(fd)
            set_gui_conf(Path(path))
            tracker.mark_saved(owned)
        finally:
            set_gui_conf(None)
            os.unlink(path)

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
