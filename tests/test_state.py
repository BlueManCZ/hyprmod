"""Tests for :class:`hyprmod.core.state.AppState`.

AppState is the per-option state machine behind the whole editor: it
tracks live/saved/default values and the "managed by hyprmod" flag, and
those two axes together drive the unsaved-changes banner, the pending
list, and what gets written on save. The invariants are subtle enough to
deserve isolated coverage:

- registering an option seeds its saved baseline from the *live* IPC
  value, not the on-disk value, so a config the user hand-edited with
  ``hyprctl keyword`` doesn't trip the dirty banner on startup;
- editing a key to a new value marks it managed, editing it back to the
  saved value un-manages it (unless it was already saved-managed);
- save/discard mirror live↔saved in both the value and managed axes.

The only collaborator is ``HyprlandState`` (the IPC layer), which is a
clean seam. :class:`FakeHypr` stands in for it, records the calls
AppState makes, and lets each test choose which IPC applies succeed.
"""

from typing import cast

import pytest
from hyprland_config import value_to_conf
from hyprland_socket import HyprlandError
from hyprland_state import HyprlandState

from hyprmod.core.state import AppState
from hyprmod.core.undo import OptionChange


class FakeHypr:
    """Minimal stand-in for HyprlandState recording the calls AppState makes.

    ``live`` maps a key to ``(value, available)`` returned by
    :meth:`get_live`; unknown keys fall back to the caller's hint and
    ``available=True``. ``apply_ok`` toggles whether :meth:`apply`
    reports success. ``discard_result`` is what :meth:`discard` returns.
    """

    def __init__(self, live=None, apply_ok=True, discard_result=None):
        self.live = live or {}
        self.apply_ok = apply_ok
        self.discard_result = discard_result or {}
        self.applied: list[tuple[str, object]] = []
        self.keywords: list[tuple[str, object]] = []
        self.reloaded = 0
        self.discarded = 0
        self.raise_on_keyword = False

    def get_live(self, key, hint=None):
        return self.live.get(key, (hint, True))

    def apply(self, key, value, *, validate=True):
        self.applied.append((key, value))
        return self.apply_ok

    def keyword(self, key, value):
        if self.raise_on_keyword:
            raise HyprlandError("boom")
        self.keywords.append((key, value))
        return True

    def reload_compositor(self):
        self.reloaded += 1
        return True

    def discard(self):
        self.discarded += 1
        return dict(self.discard_result)


def make_state(hypr: FakeHypr | None = None) -> AppState:
    # FakeHypr covers every HyprlandState method AppState calls; cast at the
    # single construction seam so the fake type-checks as the real one.
    return AppState(cast(HyprlandState, hypr or FakeHypr()))


# --- register: seeds the saved baseline from the live value -----------------


class TestRegister:
    def test_registered_option_is_not_dirty(self):
        """Saved baseline seeds from live, so a fresh option is clean."""
        state = make_state(FakeHypr(live={"gaps_in": (5, True)}))
        state.register("gaps_in", default_value=0, was_managed_value=None)
        opt = state.options["gaps_in"]
        assert opt.live_value == opt.saved_value == 5
        assert not opt.is_dirty
        assert not state.has_dirty()

    def test_live_differing_from_disk_is_still_not_dirty(self):
        """A key hand-set via ``hyprctl keyword`` must not trip the banner.

        The on-disk (managed) value is 10 but the compositor reports 25;
        the baseline follows live, so the option registers clean while
        still counting as managed.
        """
        state = make_state(FakeHypr(live={"gaps_in": (25, True)}))
        state.register("gaps_in", default_value=0, was_managed_value=10)
        opt = state.options["gaps_in"]
        assert opt.saved_value == 25
        assert opt.managed and opt.saved_managed
        assert not opt.is_dirty

    def test_was_managed_none_registers_unmanaged(self):
        state = make_state(FakeHypr(live={"k": (3, True)}))
        state.register("k", default_value=0, was_managed_value=None)
        opt = state.options["k"]
        assert not opt.managed and not opt.saved_managed

    def test_unavailable_key_falls_back_to_disk_then_default(self):
        """When the compositor can't report the value, use disk else default."""
        hypr = FakeHypr(live={"a": (None, False), "b": (None, False)})
        state = make_state(hypr)
        state.register("a", default_value=1, was_managed_value=7)
        state.register("b", default_value=1, was_managed_value=None)
        assert state.options["a"].live_value == 7  # disk value wins
        assert state.options["b"].live_value == 1  # default fallback
        assert state.options["a"].available is False

    def test_float_normalized_to_registered_precision(self):
        state = make_state(FakeHypr(live={"f": (1.23456, True)}))
        state.register("f", default_value=0.0, was_managed_value=None, digits=2)
        assert state.options["f"].live_value == 1.23


# --- normalize --------------------------------------------------------------


class TestNormalize:
    def test_rounds_only_registered_floats(self):
        state = make_state()
        state._precisions["f"] = 3
        assert state.normalize("f", 0.123456) == 0.123

    def test_unregistered_key_passes_through(self):
        state = make_state()
        assert state.normalize("f", 0.123456) == 0.123456

    def test_non_float_passes_through(self):
        state = make_state()
        state._precisions["f"] = 2
        assert state.normalize("f", "text") == "text"
        assert state.normalize("f", 7) == 7


# --- set_live: the managed-flag lifecycle -----------------------------------


class TestSetLive:
    def _registered(self, live=5, was_managed=None):
        hypr = FakeHypr(live={"k": (live, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=was_managed)
        return state, hypr

    def test_edit_marks_managed_and_dirty(self):
        state, hypr = self._registered()
        change = state.set_live("k", 9)
        opt = state.options["k"]
        assert opt.live_value == 9
        assert opt.managed and opt.is_dirty
        assert hypr.applied == [("k", 9)]
        assert change == OptionChange(
            key="k", old_value=5, new_value=9, old_managed=False, new_managed=True
        )

    def test_editing_back_to_saved_unmanages_a_previously_clean_key(self):
        """Set away then back: an unmanaged key ends up unmanaged and clean."""
        state, _ = self._registered()
        state.set_live("k", 9)
        state.set_live("k", 5)
        opt = state.options["k"]
        assert not opt.managed
        assert not opt.is_dirty

    def test_editing_back_to_saved_keeps_managed_if_saved_managed(self):
        """A key that was saved-managed stays managed even at the saved value."""
        state, _ = self._registered(was_managed=5)
        state.set_live("k", 9)
        state.set_live("k", 5)
        opt = state.options["k"]
        assert opt.managed  # saved_managed keeps it managed
        assert not opt.is_dirty  # but value matches saved

    def test_failed_ipc_apply_leaves_state_untouched(self):
        hypr = FakeHypr(live={"k": (5, True)}, apply_ok=False)
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        assert state.set_live("k", 9) is None
        opt = state.options["k"]
        assert opt.live_value == 5
        assert not opt.managed

    def test_unknown_key_returns_none(self):
        state = make_state()
        assert state.set_live("missing", 1) is None

    def test_float_value_normalized_before_apply(self):
        hypr = FakeHypr(live={"f": (0.0, True)})
        state = make_state(hypr)
        state.register("f", default_value=0.0, was_managed_value=None, digits=2)
        state.set_live("f", 0.98765)
        assert state.options["f"].live_value == 0.99
        assert hypr.applied == [("f", 0.99)]


# --- apply_option_value: the path undo/redo replays through -----------------


class TestApplyOptionValue:
    def test_string_value_is_canonicalised_through_value_to_conf(self):
        hypr = FakeHypr(live={"col": ("", True)})
        state = make_state(hypr)
        state.register("col", default_value="", was_managed_value=None)
        assert state.apply_option_value("col", "rgb(0,0,0)", managed=True)
        opt = state.options["col"]
        assert opt.live_value == value_to_conf("rgb(0,0,0)")
        assert opt.managed

    def test_bool_value_passes_through_untouched(self):
        hypr = FakeHypr(live={"b": (False, True)})
        state = make_state(hypr)
        state.register("b", default_value=False, was_managed_value=None)
        assert state.apply_option_value("b", True, managed=True)
        assert state.options["b"].live_value is True
        assert hypr.applied == [("b", True)]

    def test_failed_apply_returns_false_without_mutating(self):
        hypr = FakeHypr(live={"k": (1, True)}, apply_ok=False)
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        assert not state.apply_option_value("k", 2, managed=True)
        assert state.options["k"].live_value == 1

    def test_unknown_key_returns_false(self):
        assert not make_state().apply_option_value("missing", 1, managed=True)


# --- unmanage / reset_to_value ----------------------------------------------


class TestUnmanageAndReset:
    def test_unmanage_clears_both_managed_flags(self):
        hypr = FakeHypr(live={"k": (5, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=5)
        state.unmanage("k")
        opt = state.options["k"]
        assert not opt.managed and not opt.saved_managed
        assert not opt.is_dirty  # both axes moved together

    def test_reset_to_value_applies_fallback_and_unmanages(self):
        hypr = FakeHypr(live={"k": (9, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=9)
        assert state.reset_to_value("k", fallback=0)
        opt = state.options["k"]
        assert opt.live_value == 0
        assert not opt.managed
        assert hypr.applied == [("k", 0)]

    def test_reset_survives_ipc_error(self):
        hypr = FakeHypr(live={"k": (9, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=9)

        def boom(key, value, *, validate=True):
            raise HyprlandError("nope")

        hypr.apply = boom
        assert state.reset_to_value("k", fallback=0)
        assert not state.options["k"].managed  # flag cleared despite failed IPC

    def test_reset_unknown_key_returns_false(self):
        assert not make_state().reset_to_value("missing", fallback=0)


# --- discard_one ------------------------------------------------------------


class TestDiscardOne:
    def test_reverts_dirty_option_to_saved(self):
        hypr = FakeHypr(live={"k": (5, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        state.set_live("k", 9)
        assert state.discard_one("k")
        opt = state.options["k"]
        assert opt.live_value == 5
        assert opt.managed == opt.saved_managed
        assert not opt.is_dirty
        assert hypr.keywords[-1] == ("k", value_to_conf(5))

    def test_clean_option_is_not_reverted(self):
        hypr = FakeHypr(live={"k": (5, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        assert not state.discard_one("k")

    def test_ipc_failure_still_reverts_in_memory(self):
        hypr = FakeHypr(live={"k": (5, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        state.set_live("k", 9)
        hypr.raise_on_keyword = True
        assert state.discard_one("k")
        assert state.options["k"].live_value == 5

    def test_unknown_key_returns_false(self):
        assert not make_state().discard_one("missing")


# --- save / bulk-discard / query helpers ------------------------------------


class TestSaveAndDiscardDirty:
    def test_mark_saved_moves_baseline_to_live(self):
        hypr = FakeHypr(live={"k": (5, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        state.set_live("k", 9)
        assert state.has_dirty()
        state.mark_saved()
        opt = state.options["k"]
        assert opt.saved_value == 9 and opt.saved_managed is True
        assert not state.has_dirty()

    def test_discard_dirty_mirrors_reverted_values(self):
        hypr = FakeHypr(live={"k": (5, True)}, discard_result={"k": 5})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        state.set_live("k", 9)
        reverted = state.discard_dirty()
        assert reverted == {"k": 5}
        opt = state.options["k"]
        assert opt.live_value == opt.saved_value == 5
        assert not opt.is_dirty

    def test_discard_dirty_restores_managed_only_change(self):
        """A key whose only change is the managed flag is reverted too.

        ``unmanage`` moves both flags together, so instead drive a pure
        managed divergence by hand: value equals saved, managed differs.
        ``discard_dirty`` must pull the managed flag back to saved even
        though the key is absent from the IPC revert dict.
        """
        hypr = FakeHypr(live={"k": (5, True)}, discard_result={})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=5)
        opt = state.options["k"]
        opt.managed = False  # value unchanged, only the flag diverges
        assert opt.is_dirty
        state.discard_dirty()
        assert opt.managed == opt.saved_managed is True
        assert not opt.is_dirty


class TestQueryHelpers:
    def _two_options(self):
        hypr = FakeHypr(live={"a": (1, True), "b": (2, True)})
        state = make_state(hypr)
        state.register("a", default_value=0, was_managed_value=None)
        state.register("b", default_value=0, was_managed_value=None)
        return state

    def test_get_dirty_values_lists_only_changed(self):
        state = self._two_options()
        state.set_live("a", 9)
        assert state.get_dirty_values() == {"a": 9}

    def test_get_all_live_values_only_managed_as_strings(self):
        state = self._two_options()
        state.set_live("a", 9)  # marks 'a' managed; 'b' stays unmanaged
        assert state.get_all_live_values() == {"a": value_to_conf(9)}

    def test_has_dirty_reflects_any_change(self):
        state = self._two_options()
        assert not state.has_dirty()
        state.set_live("b", 5)
        assert state.has_dirty()


# --- refresh_all_live -------------------------------------------------------


class TestRefreshAllLive:
    def test_resets_baselines_and_notifies_only_changed(self):
        hypr = FakeHypr(live={"a": (1, True), "b": (2, True)})
        state = make_state(hypr)
        state.register("a", default_value=0, was_managed_value=None)
        state.register("b", default_value=0, was_managed_value=None)

        seen: list[str] = []
        state.on_change(seen.append)

        # 'a' changes live under us (e.g. profile switch), 'b' stays put.
        hypr.live["a"] = (99, True)
        state.refresh_all_live()

        opt = state.options["a"]
        assert opt.live_value == opt.saved_value == opt.initial_value == 99
        assert not opt.is_dirty
        assert seen == ["a"]  # only the changed key fires

    def test_unavailable_key_is_skipped(self):
        hypr = FakeHypr(live={"a": (1, True)})
        state = make_state(hypr)
        state.register("a", default_value=0, was_managed_value=None)
        hypr.live["a"] = (None, False)
        state.refresh_all_live()
        assert state.options["a"].live_value == 1  # untouched


# --- reload_preserving_dirty ------------------------------------------------


class TestReloadPreservingDirty:
    def test_reapplies_dirty_values_after_reload(self):
        hypr = FakeHypr(live={"k": (5, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        state.set_live("k", 9)
        state.reload_preserving_dirty()
        assert hypr.reloaded == 1
        assert ("k", 9) in hypr.keywords


# --- change notifications ---------------------------------------------------


class TestNotifications:
    def test_callbacks_fire_on_change(self):
        hypr = FakeHypr(live={"k": (5, True)})
        state = make_state(hypr)
        state.register("k", default_value=0, was_managed_value=None)
        calls: list[str] = []
        state.on_change(calls.append)
        state.set_live("k", 9)
        state.unmanage("k")
        assert calls == ["k", "k"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
