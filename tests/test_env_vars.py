"""Tests for env-var parsing, serialization, and config integration."""

from pathlib import Path

import pytest

from hyprmod.core import config
from hyprmod.core.env_vars import (
    RESERVED_NAMES,
    EnvVar,
    ExternalEnvVar,
    count_pending_changes,
    detect_reorder,
    drop_target_idx,
    is_reserved,
    iter_item_changes,
    load_external_env_vars,
    overridden_external_names,
    parse_env_line,
    parse_env_lines,
    serialize,
)
from hyprmod.core.ownership import SavedList


@pytest.fixture
def gui_conf_tmp(tmp_path):
    """Redirect gui_conf() to a temporary file for the duration of a test."""
    target = tmp_path / "hyprland-gui.conf"
    config.set_gui_conf(target)
    yield target
    config.set_gui_conf(None)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


class TestParseEnvLine:
    def test_basic(self):
        result = parse_env_line("env = QT_QPA_PLATFORM,wayland")
        assert result == EnvVar(name="QT_QPA_PLATFORM", value="wayland")

    def test_strips_whitespace(self):
        result = parse_env_line("  env =  GDK_BACKEND ,  wayland  ")
        assert result == EnvVar(name="GDK_BACKEND", value="wayland")

    def test_preserves_commas_in_value(self):
        # Hyprland splits on the *first* comma after ``=`` only — further
        # commas are part of the value (common for fallback chains, e.g.
        # ``GDK_BACKEND=wayland,x11``).
        result = parse_env_line("env = GDK_BACKEND,wayland,x11")
        assert result == EnvVar(name="GDK_BACKEND", value="wayland,x11")

    def test_preserves_equals_in_value(self):
        # Only the first ``=`` is the keyword separator; further ones are
        # part of the value (e.g. ``CLUTTER_BACKEND=foo=bar``).
        result = parse_env_line("env = CLUTTER_BACKEND,foo=bar=baz")
        assert result == EnvVar(name="CLUTTER_BACKEND", value="foo=bar=baz")

    def test_empty_value_rejected(self):
        # Hyprland 0.54+ rejects ``env = NAME,`` with an empty value;
        # emitting one would be a runtime error.
        assert parse_env_line("env = FOO,") is None
        # Trimmed-empty value too.
        assert parse_env_line("env = FOO,   ") is None

    def test_no_comma_rejected(self):
        # Hyprland needs the ``,`` separator. Without it, the value is
        # missing (or the name is part of the value depending on how you
        # squint) — neither is unambiguous, so we drop it.
        assert parse_env_line("env = FOO") is None

    def test_unknown_keyword_rejected(self):
        assert parse_env_line("envv = FOO,bar") is None
        assert parse_env_line("exec = FOO,bar") is None
        assert parse_env_line("monitor = , preferred, auto, 1") is None

    def test_missing_equals_rejected(self):
        assert parse_env_line("env FOO,bar") is None

    def test_empty_name_rejected(self):
        # ``env = ,value`` — comma present, name empty.
        assert parse_env_line("env = ,wayland") is None


class TestParseEnvLines:
    def test_preserves_order(self):
        lines = [
            "env = FIRST,one",
            "env = SECOND,two",
            "env = THIRD,three",
        ]
        result = parse_env_lines(lines)
        assert [(e.name, e.value) for e in result] == [
            ("FIRST", "one"),
            ("SECOND", "two"),
            ("THIRD", "three"),
        ]

    def test_drops_unparseable(self):
        lines = [
            "env = OK,one",
            "garbage",  # no '='
            "env = NOCOMMA",  # missing separator
            "env = ,empty-name",  # empty name
            "env = ALSO_OK,two",
        ]
        result = parse_env_lines(lines)
        assert [(e.name, e.value) for e in result] == [
            ("OK", "one"),
            ("ALSO_OK", "two"),
        ]

    def test_does_not_filter_reserved(self):
        # Filtering reserved names is the page's job, not the parser's —
        # the pending-changes diff needs every env line to render an
        # accurate save preview.
        lines = ["env = XCURSOR_THEME,Bibata-Modern-Ice", "env = FOO,bar"]
        result = parse_env_lines(lines)
        assert len(result) == 2
        assert result[0].name == "XCURSOR_THEME"

    def test_empty_input(self):
        assert parse_env_lines([]) == []


class TestSerialize:
    def test_round_trip(self):
        original = [
            EnvVar("QT_QPA_PLATFORM", "wayland"),
            EnvVar("GDK_BACKEND", "wayland,x11"),
        ]
        lines = serialize(original)
        assert lines == [
            "env = QT_QPA_PLATFORM,wayland",
            "env = GDK_BACKEND,wayland,x11",
        ]
        # Re-parse and confirm round-trip.
        assert parse_env_lines(lines) == original

    def test_preserves_order(self):
        original = [
            EnvVar("THIRD", "3"),
            EnvVar("FIRST", "1"),
            EnvVar("SECOND", "2"),
        ]
        # ``serialize()`` emits items in input order; the page is
        # responsible for any reordering before calling.
        assert serialize(original) == [
            "env = THIRD,3",
            "env = FIRST,1",
            "env = SECOND,2",
        ]


# ---------------------------------------------------------------------------
# Reserved names
# ---------------------------------------------------------------------------


class TestReservedNames:
    """The four cursor-managed names live in :data:`RESERVED_NAMES` and
    must stay in sync with :mod:`hyprmod.pages.cursor`."""

    def test_cursor_vars_are_reserved(self):
        for name in ("XCURSOR_THEME", "XCURSOR_SIZE", "HYPRCURSOR_THEME", "HYPRCURSOR_SIZE"):
            assert is_reserved(name), f"{name!r} should be reserved"

    def test_unrelated_var_not_reserved(self):
        assert not is_reserved("QT_QPA_PLATFORM")
        assert not is_reserved("PATH")

    def test_reserved_names_match_cursor_module(self):
        # The cursor page imports ``RESERVED_NAMES`` and uses it as
        # ``_MANAGED_VARS``. If either side ever drifts, both pages
        # would silently disagree about ownership of an env var. This
        # test pins the union so any future addition triggers a
        # deliberate decision.
        from hyprmod.pages.cursor import _MANAGED_VARS, _SIZE_VARS, _THEME_VARS

        assert _MANAGED_VARS == RESERVED_NAMES
        assert set(_THEME_VARS) | set(_SIZE_VARS) == RESERVED_NAMES

    def test_case_sensitive(self):
        # POSIX env-var names are case-sensitive; Hyprland forwards
        # them verbatim. A lowercase variant is therefore not reserved
        # and would be treated as a separate name.
        assert not is_reserved("xcursor_theme")
        assert not is_reserved("Xcursor_theme")


# ---------------------------------------------------------------------------
# SavedList integration (the page's underlying state model)
# ---------------------------------------------------------------------------


class TestSavedListBaselines:
    """EnvVar uses a non-frozen dataclass; verify it round-trips through
    SavedList's deepcopy / key-comparison machinery."""

    def _make(self) -> SavedList[EnvVar]:
        items = [
            EnvVar("QT_QPA_PLATFORM", "wayland"),
            EnvVar("GDK_BACKEND", "wayland,x11"),
        ]
        return SavedList(items, key=lambda e: e.to_line())

    def test_clean_after_load(self):
        sl = self._make()
        assert not sl.is_dirty()
        assert all(not sl.is_item_dirty(i) for i in range(len(sl)))

    def test_append_marks_only_new_dirty(self):
        sl = self._make()
        sl.append_new(EnvVar("MOZ_ENABLE_WAYLAND", "1"))
        assert sl.is_dirty()
        assert not sl.is_item_dirty(0)
        assert not sl.is_item_dirty(1)
        assert sl.is_item_dirty(2)

    def test_edit_in_place_marks_dirty(self):
        sl = self._make()
        sl[0] = EnvVar("QT_QPA_PLATFORM", "xcb")
        assert sl.is_dirty()
        assert sl.is_item_dirty(0)
        assert not sl.is_item_dirty(1)

    def test_discard_at_restores_baseline(self):
        sl = self._make()
        sl[0] = EnvVar("QT_QPA_PLATFORM", "xcb")
        sl.discard_at(0)
        assert sl[0] == EnvVar("QT_QPA_PLATFORM", "wayland")
        assert not sl.is_item_dirty(0)


class TestSavedListRestoreDeleted:
    """``restore_deleted`` re-inserts a previously-deleted saved item
    at its saved position with its saved baseline, so a pure
    delete-then-restore round trip leaves the list non-dirty.

    Regression coverage for the env-vars page's "Restore this variable"
    action — before this method existed, the page used ``append_new``
    which marked the restored row as a new addition.
    """

    def _items(self, *names: str) -> list[EnvVar]:
        return [EnvVar(n, "x") for n in names]

    def _make(self, *names: str) -> SavedList[EnvVar]:
        return SavedList(self._items(*names), key=lambda e: e.to_line())

    def test_delete_then_restore_is_clean(self):
        # The original bug report: delete an entry, immediately click
        # restore, expect the page to flip back to non-dirty.
        sl = self._make("A", "B", "C")
        sl.pop_at(1)
        assert sl.is_dirty()  # mid-state — the delete is pending
        sl.restore_deleted(EnvVar("B", "x"))
        assert not sl.is_dirty()
        assert [e.name for e in sl] == ["A", "B", "C"]

    def test_restore_inserts_at_saved_position_among_survivors(self):
        # Saved [A, B, C, D]; user deletes B. Restore puts B back
        # between A and C even though it was appended-at-end before.
        sl = self._make("A", "B", "C", "D")
        sl.pop_at(1)
        assert [e.name for e in sl] == ["A", "C", "D"]
        idx = sl.restore_deleted(EnvVar("B", "x"))
        assert idx == 1
        assert [e.name for e in sl] == ["A", "B", "C", "D"]

    def test_restore_appends_when_no_later_survivors(self):
        # Saved [A, B, C]; user deletes C. There's no surviving saved
        # item with a higher index, so the only sensible insertion
        # point is the end.
        sl = self._make("A", "B", "C")
        sl.pop_at(2)
        idx = sl.restore_deleted(EnvVar("C", "x"))
        assert idx == 2
        assert [e.name for e in sl] == ["A", "B", "C"]
        assert not sl.is_dirty()

    def test_restored_row_carries_baseline(self):
        # A restored row's baseline is the saved value, so its
        # individual ``is_item_dirty`` is False — distinguishing
        # restore from "append a brand-new row that happens to look
        # the same as the deleted one."
        sl = self._make("A", "B", "C")
        sl.pop_at(1)
        idx = sl.restore_deleted(EnvVar("B", "x"))
        assert sl.get_baseline(idx) == EnvVar("B", "x")
        assert not sl.is_item_dirty(idx)

    def test_restore_preserves_other_dirty_edits(self):
        # User: edit A, delete B, restore B. The edit on A must
        # survive the restore — restore is a *targeted* undo for one
        # row, not a wholesale revert.
        sl = self._make("A", "B", "C")
        sl[0] = EnvVar("A", "edited")
        sl.pop_at(1)
        sl.restore_deleted(EnvVar("B", "x"))
        assert sl.is_item_dirty(0)  # A's edit survived
        assert not sl.is_item_dirty(1)  # B is back to baseline
        assert not sl.is_item_dirty(2)
        # The list is still dirty because A's value differs from saved.
        assert sl.is_dirty()

    def test_restore_at_saved_position_when_survivors_reordered(self):
        # User reorders surviving items, then restores a deleted one.
        # The restored row goes between the saved-neighbours that still
        # exist — even if they're now in a different absolute order.
        sl = self._make("A", "B", "C", "D")
        sl.pop_at(1)  # items: [A, C, D]
        sl.move(0, 2)  # items: [C, D, A]  — A pushed to the back
        # B's saved-idx is 1; first surviving saved item with idx > 1
        # is C, currently at position 0. So B inserts at index 0.
        idx = sl.restore_deleted(EnvVar("B", "x"))
        assert idx == 0
        assert [e.name for e in sl] == ["B", "C", "D", "A"]
        # B itself is at its saved value; the list is still dirty
        # due to the user's prior reorder.
        assert not sl.is_item_dirty(idx)
        assert sl.is_dirty()

    def test_raises_when_item_not_in_saved(self):
        sl = self._make("A", "B")
        with pytest.raises(ValueError, match="not in the saved baseline"):
            sl.restore_deleted(EnvVar("Z", "x"))

    def test_raises_when_item_already_in_list(self):
        sl = self._make("A", "B")
        with pytest.raises(ValueError, match="already in the list"):
            sl.restore_deleted(EnvVar("A", "x"))

    def test_restored_baseline_is_independent_copy(self):
        # Editing the restored row must not mutate the saved baseline,
        # even though both started life as the same saved entry.
        sl = SavedList(
            [EnvVar("FOO", "original")],
            key=lambda e: e.to_line(),
        )
        sl.pop_at(0)
        idx = sl.restore_deleted(EnvVar("FOO", "original"))
        sl[idx] = EnvVar("FOO", "mutated")
        # Saved snapshot still has the original value.
        assert sl.saved == [EnvVar("FOO", "original")]
        # And the restored row is now dirty against its baseline.
        assert sl.is_item_dirty(idx)


# ---------------------------------------------------------------------------
# Reorder detection
# ---------------------------------------------------------------------------


class TestDetectReorder:
    """``detect_reorder`` should fire on pure reorders, ignore pure
    add/remove churn, and still detect mixed cases where the *common*
    entries between saved and current have different relative order."""

    def _items(self, *names: str) -> list[EnvVar]:
        return [EnvVar(n, "x") for n in names]

    def test_no_change(self):
        items = self._items("A", "B", "C")
        assert not detect_reorder(items, items)

    def test_pure_reorder_detected(self):
        saved = self._items("A", "B", "C")
        current = self._items("C", "A", "B")
        assert detect_reorder(saved, current)

    def test_pure_addition_not_a_reorder(self):
        saved = self._items("A", "B")
        current = self._items("A", "B", "C")
        assert not detect_reorder(saved, current)

    def test_pure_removal_not_a_reorder(self):
        saved = self._items("A", "B", "C")
        current = self._items("A", "C")
        assert not detect_reorder(saved, current)

    def test_addition_with_reorder_detected(self):
        saved = self._items("A", "B", "C")
        current = self._items("C", "A", "B", "D")
        assert detect_reorder(saved, current)

    def test_empty_lists_no_reorder(self):
        assert not detect_reorder([], [])
        assert not detect_reorder([], self._items("A"))
        assert not detect_reorder(self._items("A"), [])

    def test_value_change_treated_as_distinct_entry(self):
        # An entry whose *value* changed isn't "the same item moved" —
        # it's a delete + add as far as ``to_line()`` is concerned.
        saved = [EnvVar("A", "old"), EnvVar("B", "y")]
        current = [EnvVar("B", "y"), EnvVar("A", "new")]
        # B and A are both "in different positions" but A's line
        # changed too — only B is common, and one common item can't
        # be reordered relative to anything.
        assert not detect_reorder(saved, current)


# ---------------------------------------------------------------------------
# count_pending_changes — sidebar badge ↔ pending-list parity
# ---------------------------------------------------------------------------


class TestCountPendingChanges:
    """The sidebar badge and the pending-list both derive from this
    helper, so any discrepancy between the two would mean a bug here."""

    def _baselines(self, owned: SavedList[EnvVar]) -> list[EnvVar | None]:
        return [owned.get_baseline(i) for i in range(len(owned))]

    def test_clean_list_zero(self):
        owned = SavedList([EnvVar("A", "x")], key=lambda e: e.to_line())
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 0

    def test_one_added_counts_one(self):
        owned: SavedList[EnvVar] = SavedList([], key=lambda e: e.to_line())
        owned.append_new(EnvVar("A", "x"))
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_one_modified_counts_one(self):
        owned = SavedList([EnvVar("A", "x")], key=lambda e: e.to_line())
        owned[0] = EnvVar("A", "y")
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_one_removed_counts_one(self):
        owned = SavedList(
            [EnvVar("A", "x"), EnvVar("B", "y")],
            key=lambda e: e.to_line(),
        )
        owned.pop_at(0)
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_pure_reorder_counts_one(self):
        owned = SavedList(
            [EnvVar(n, "x") for n in ("A", "B", "C")],
            key=lambda e: e.to_line(),
        )
        owned.move(0, 2)
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_add_plus_reorder_counts_two(self):
        owned = SavedList(
            [EnvVar(n, "x") for n in ("A", "B")],
            key=lambda e: e.to_line(),
        )
        owned.move(0, 1)
        owned.append_new(EnvVar("C", "x"))
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 2

    def test_edit_does_not_double_count(self):
        # Regression for the autostart edit-counted-twice bug — same
        # iterator, same potential for trouble. Editing an entry's
        # value should produce exactly one ``modified`` entry.
        owned = SavedList([EnvVar("A", "old")], key=lambda e: e.to_line())
        owned[0] = EnvVar("A", "new")
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="must be the same length"):
            count_pending_changes([], [EnvVar("A", "x")], [None, None])


# ---------------------------------------------------------------------------
# iter_item_changes — yielded shape used by the pending-list collector
# ---------------------------------------------------------------------------


class TestIterItemChanges:
    def test_added_index_and_baseline(self):
        owned: SavedList[EnvVar] = SavedList([], key=lambda e: e.to_line())
        owned.append_new(EnvVar("FOO", "bar"))
        baselines = [owned.get_baseline(i) for i in range(len(owned))]
        events = list(iter_item_changes(owned.saved, list(owned), baselines))
        assert len(events) == 1
        kind, idx, item, baseline = events[0]
        assert kind == "added"
        assert idx == 0
        assert item == EnvVar("FOO", "bar")
        assert baseline is None

    def test_modified_carries_baseline(self):
        owned = SavedList([EnvVar("A", "old")], key=lambda e: e.to_line())
        owned[0] = EnvVar("A", "new")
        baselines = [owned.get_baseline(i) for i in range(len(owned))]
        events = list(iter_item_changes(owned.saved, list(owned), baselines))
        assert len(events) == 1
        kind, idx, item, baseline = events[0]
        assert kind == "modified"
        assert idx == 0
        assert item == EnvVar("A", "new")
        assert baseline == EnvVar("A", "old")

    def test_removed_uses_minus_one_index(self):
        owned = SavedList([EnvVar("A", "x"), EnvVar("B", "y")], key=lambda e: e.to_line())
        owned.pop_at(0)
        baselines = [owned.get_baseline(i) for i in range(len(owned))]
        events = list(iter_item_changes(owned.saved, list(owned), baselines))
        assert len(events) == 1
        kind, idx, item, baseline = events[0]
        assert kind == "removed"
        assert idx == -1
        assert item == EnvVar("A", "x")
        assert baseline is None


# ---------------------------------------------------------------------------
# drop_target_idx — drag-and-drop hover → SavedList.move target
# ---------------------------------------------------------------------------


class TestDropTargetIdx:
    """Same math as autostart's; smoke-test the boundary cases here."""

    def _move(self, items: list[str], src: int, hover: int, *, before: bool) -> list[str]:
        target = drop_target_idx(src, hover, before)
        result = list(items)
        item = result.pop(src)
        result.insert(target, item)
        return result

    def test_drag_top_to_bottom(self):
        assert self._move(["A", "B", "C"], src=0, hover=2, before=False) == ["B", "C", "A"]

    def test_drag_bottom_to_top(self):
        assert self._move(["A", "B", "C"], src=2, hover=0, before=True) == ["C", "A", "B"]

    def test_above_self_below_neighbour_is_self_position(self):
        # ``B`` (idx 1) dropped on the bottom half of ``A`` (idx 0):
        # "B between A and B," its current spot.
        assert drop_target_idx(1, 0, before=False) == 1


# ---------------------------------------------------------------------------
# Config write integration
# ---------------------------------------------------------------------------


class TestWriteIntegration:
    def test_env_lines_emitted_in_environment_section(self, gui_conf_tmp):
        config.write_all(
            {"general:gaps_in": "5"},
            env_lines=[
                "env = QT_QPA_PLATFORM,wayland",
                "env = GDK_BACKEND,wayland,x11",
            ],
        )
        content = gui_conf_tmp.read_text()
        assert "# Environment" in content
        assert "env = QT_QPA_PLATFORM,wayland" in content
        assert "env = GDK_BACKEND,wayland,x11" in content
        # Environment is emitted before plain options (env affects spawned
        # processes, including any child of the gui-conf reload).
        assert content.index("# Environment") < content.index("general:gaps_in")

    def test_no_section_when_empty(self, gui_conf_tmp):
        config.write_all({"general:gaps_in": "5"})
        content = gui_conf_tmp.read_text()
        assert "# Environment" not in content

    def test_round_trip_through_read_all_sections(self, gui_conf_tmp):
        config.write_all(
            {},
            env_lines=[
                "env = FOO,bar",
                "env = BAZ,qux",
            ],
        )
        _, sections = config.read_all_sections()
        assert sections.get(config.KEYWORD_ENV) == [
            "env = FOO,bar",
            "env = BAZ,qux",
        ]


# ---------------------------------------------------------------------------
# External loader
# ---------------------------------------------------------------------------


class TestExternalLoader:
    """``load_external_env_vars`` walks the user's ``hyprland.conf`` and
    any sourced files for env entries the user defined themselves —
    surfaced read-only on the page with an "override" button. Mirrors
    :class:`tests.test_layer_rules.TestExternalLoader`.
    """

    def test_loads_env_from_root_file(self, tmp_path):
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text("env = QT_QPA_PLATFORM,wayland\n")
        managed.write_text("")
        external = load_external_env_vars(root, managed)
        assert len(external) == 1
        assert external[0].var == EnvVar(name="QT_QPA_PLATFORM", value="wayland")
        assert external[0].source_path == root
        assert external[0].lineno >= 1

    def test_loads_env_from_sourced_file(self, tmp_path):
        # External entries can live in a file sourced from the root —
        # exactly the situation users hit when they split their config
        # across ``env.conf``, ``binds.conf``, etc.
        root = tmp_path / "hyprland.conf"
        sourced = tmp_path / "env.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text(f"source = {sourced}\n")
        sourced.write_text("env = GDK_BACKEND,wayland,x11\n")
        managed.write_text("")
        external = load_external_env_vars(root, managed)
        assert len(external) == 1
        assert external[0].var == EnvVar(name="GDK_BACKEND", value="wayland,x11")
        # The loader records the *actual* source file, not the root —
        # that's what the page renders as the row's group title.
        assert external[0].source_path == sourced

    def test_excludes_managed_file(self, tmp_path):
        # Anything in the managed file is *not* surfaced as external —
        # the page renders managed entries from SavedList and external
        # from this loader; double-counting would confuse users.
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text(f"source = {managed}\n")
        managed.write_text("env = OWNED,one\n")
        external = load_external_env_vars(root, managed)
        assert external == []

    def test_excludes_reserved_names(self, tmp_path):
        # Cursor-managed names belong to the Cursor page; surfacing
        # them here too would split the UX of one logical setting
        # across two pages.
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text(
            "env = XCURSOR_THEME,Bibata-Modern-Ice\n"
            "env = HYPRCURSOR_SIZE,32\n"
            "env = QT_QPA_PLATFORM,wayland\n"
        )
        managed.write_text("")
        external = load_external_env_vars(root, managed)
        # Only the non-reserved entry survives.
        assert len(external) == 1
        assert external[0].var.name == "QT_QPA_PLATFORM"

    def test_missing_root_returns_empty(self, tmp_path):
        managed = tmp_path / "hyprland-gui.conf"
        managed.write_text("")
        external = load_external_env_vars(tmp_path / "nonexistent.conf", managed)
        assert external == []

    def test_skips_unparseable_lines(self, tmp_path):
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text(
            "env = NOCOMMA\n"  # missing separator → parser drops
            "env = ,empty-name\n"  # empty name → parser drops
            "env = OK,one\n"
        )
        managed.write_text("")
        external = load_external_env_vars(root, managed)
        assert len(external) == 1
        assert external[0].var.name == "OK"

    def test_preserves_line_numbers(self, tmp_path):
        root = tmp_path / "hyprland.conf"
        managed = tmp_path / "hyprland-gui.conf"
        root.write_text("# leading comment\n\nenv = FIRST,one\nenv = SECOND,two\n")
        managed.write_text("")
        external = load_external_env_vars(root, managed)
        assert len(external) == 2
        # Hyprland's parser is 1-indexed; with two header lines + the
        # blank, FIRST is on line 3 and SECOND is on line 4.
        first = next(e for e in external if e.var.name == "FIRST")
        second = next(e for e in external if e.var.name == "SECOND")
        assert second.lineno == first.lineno + 1


# ---------------------------------------------------------------------------
# overridden_external_names — the conflict detector
# ---------------------------------------------------------------------------


class TestOverriddenExternalNames:
    """The page calls this to decide which external rows render with
    an "Overridden" badge vs. an override button. Last-write-wins
    semantics means an owned line and an external line sharing a name
    yield the owned value, so the external is effectively shadowed.
    """

    def _ext(self, name: str, value: str = "x") -> ExternalEnvVar:
        return ExternalEnvVar(
            var=EnvVar(name=name, value=value),
            source_path=Path("/tmp/fake.conf"),
            lineno=1,
        )

    def test_no_overlap_returns_empty(self):
        external = [self._ext("FOO"), self._ext("BAR")]
        owned = [EnvVar("BAZ", "x")]
        assert overridden_external_names(external, owned) == set()

    def test_single_overlap(self):
        external = [self._ext("FOO"), self._ext("BAR")]
        owned = [EnvVar("FOO", "different")]
        assert overridden_external_names(external, owned) == {"FOO"}

    def test_multiple_overlaps(self):
        external = [self._ext("FOO"), self._ext("BAR"), self._ext("BAZ")]
        owned = [EnvVar("FOO", "x"), EnvVar("BAZ", "y")]
        assert overridden_external_names(external, owned) == {"FOO", "BAZ"}

    def test_value_difference_irrelevant(self):
        # Override status is a name-level concept — value difference
        # doesn't matter, since Hyprland uses the owned value either way.
        external = [self._ext("FOO", "external-val")]
        owned = [EnvVar("FOO", "external-val")]  # same value
        assert overridden_external_names(external, owned) == {"FOO"}

    def test_empty_inputs(self):
        assert overridden_external_names([], []) == set()
        assert overridden_external_names([self._ext("FOO")], []) == set()
        assert overridden_external_names([], [EnvVar("FOO", "x")]) == set()
