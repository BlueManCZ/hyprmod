"""Tests for autostart parsing, serialization, and config integration."""

import pytest

from hyprmod.core import config
from hyprmod.core.autostart import (
    EXEC_KEYWORDS,
    KEYWORD_LABELS,
    ExecData,
    count_pending_changes,
    detect_reorder,
    drop_target_idx,
    parse_exec_line,
    parse_exec_lines,
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


class TestParseExecLine:
    def test_basic_exec_once(self):
        result = parse_exec_line("exec-once = waybar")
        assert result == ExecData(keyword="exec-once", command="waybar")

    def test_basic_exec(self):
        result = parse_exec_line("exec = pkill -SIGUSR1 waybar")
        assert result == ExecData(keyword="exec", command="pkill -SIGUSR1 waybar")

    def test_strips_whitespace(self):
        result = parse_exec_line("  exec-once   =   swaybg -i wallpaper.jpg  ")
        assert result == ExecData(keyword="exec-once", command="swaybg -i wallpaper.jpg")

    def test_preserves_internal_whitespace(self):
        result = parse_exec_line('exec-once = sh -c "sleep 2 && waybar"')
        assert result is not None
        assert result.command == 'sh -c "sleep 2 && waybar"'

    def test_preserves_command_equals_signs(self):
        # Only the *first* '=' is the keyword separator; further ones are part
        # of the command (e.g. "VAR=val cmd" or shell parameter expansions).
        result = parse_exec_line("exec-once = env FOO=bar baz")
        assert result == ExecData(keyword="exec-once", command="env FOO=bar baz")

    def test_unknown_keyword_returns_none(self):
        assert parse_exec_line("bind = SUPER, T, exec, kitty") is None
        assert parse_exec_line("execr-once = something") is None
        assert parse_exec_line("monitor = , preferred, auto, 1") is None

    def test_missing_equals_returns_none(self):
        assert parse_exec_line("exec-once waybar") is None

    def test_empty_command_returns_none(self):
        assert parse_exec_line("exec-once = ") is None
        assert parse_exec_line("exec =") is None


class TestParseExecLines:
    def test_preserves_order(self):
        lines = [
            "exec-once = swaybg",
            "exec-once = waybar",
            "exec = pkill -SIGUSR1 waybar",
        ]
        result = parse_exec_lines(lines)
        assert [e.command for e in result] == ["swaybg", "waybar", "pkill -SIGUSR1 waybar"]

    def test_drops_unparseable(self):
        lines = [
            "exec-once = waybar",
            "garbage",  # no '='
            "monitor = , preferred, auto, 1",  # not an exec keyword
            "exec-once = ",  # empty command
            "exec = something",
        ]
        result = parse_exec_lines(lines)
        assert [e.command for e in result] == ["waybar", "something"]

    def test_empty_input(self):
        assert parse_exec_lines([]) == []


class TestSerialize:
    def test_round_trip(self):
        original = [
            ExecData("exec-once", "waybar"),
            ExecData("exec", "pkill -SIGUSR1 waybar"),
        ]
        lines = serialize(original)
        assert lines == [
            "exec-once = waybar",
            "exec = pkill -SIGUSR1 waybar",
        ]
        # Re-parse and confirm we land back where we started.
        assert parse_exec_lines(lines) == original

    def test_preserves_order(self):
        original = [
            ExecData("exec", "first"),
            ExecData("exec-once", "second"),
            ExecData("exec", "third"),
        ]
        # serialize() emits items in the input order; the page is responsible
        # for any reordering before calling.
        assert serialize(original) == [
            "exec = first",
            "exec-once = second",
            "exec = third",
        ]


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestKeywords:
    def test_exec_keywords_contains_both(self):
        assert config.KEYWORD_EXEC in EXEC_KEYWORDS
        assert config.KEYWORD_EXEC_ONCE in EXEC_KEYWORDS

    def test_keyword_labels_cover_all_keywords(self):
        for kw in EXEC_KEYWORDS:
            assert kw in KEYWORD_LABELS, f"missing label for {kw!r}"


# ---------------------------------------------------------------------------
# SavedList integration (the page's underlying state model)
# ---------------------------------------------------------------------------


class TestSavedListBaselines:
    """ExecData uses a non-frozen dataclass; verify it round-trips through
    SavedList's deepcopy / key-comparison machinery."""

    def _make(self) -> SavedList[ExecData]:
        items = [
            ExecData("exec-once", "waybar"),
            ExecData("exec-once", "swaybg -i wallpaper.jpg"),
        ]
        return SavedList(items, key=lambda e: e.to_line())

    def test_clean_after_load(self):
        sl = self._make()
        assert not sl.is_dirty()
        assert all(not sl.is_item_dirty(i) for i in range(len(sl)))

    def test_append_marks_only_new_dirty(self):
        sl = self._make()
        sl.append_new(ExecData("exec", "pkill -SIGUSR1 waybar"))
        assert sl.is_dirty()
        assert not sl.is_item_dirty(0)
        assert not sl.is_item_dirty(1)
        assert sl.is_item_dirty(2)

    def test_edit_in_place_marks_dirty(self):
        sl = self._make()
        sl[0] = ExecData("exec-once", "waybar --config /etc/waybar.cfg")
        assert sl.is_dirty()
        assert sl.is_item_dirty(0)
        assert not sl.is_item_dirty(1)

    def test_discard_at_restores_baseline(self):
        sl = self._make()
        sl[0] = ExecData("exec-once", "modified")
        sl.discard_at(0)
        assert sl[0] == ExecData("exec-once", "waybar")
        assert not sl.is_item_dirty(0)

    def test_pop_keeps_others_clean(self):
        sl = self._make()
        sl.pop_at(0)
        assert sl.is_dirty()  # length changed
        assert not sl.is_item_dirty(0)  # index 0 now points at the old [1]

    def test_mark_saved_clears_dirty(self):
        sl = self._make()
        sl.append_new(ExecData("exec", "later"))
        sl.mark_saved()
        assert not sl.is_dirty()


class TestSavedListMove:
    """Reordering must keep items + baselines aligned and dirty-track
    the *list*, not the individual moved items."""

    def _make(self) -> SavedList[ExecData]:
        items = [
            ExecData("exec-once", "first"),
            ExecData("exec-once", "second"),
            ExecData("exec-once", "third"),
        ]
        return SavedList(items, key=lambda e: e.to_line())

    def test_no_op_when_indices_equal(self):
        sl = self._make()
        sl.move(1, 1)
        assert [e.command for e in sl] == ["first", "second", "third"]
        assert not sl.is_dirty()

    def test_forward_move(self):
        sl = self._make()
        sl.move(0, 2)
        assert [e.command for e in sl] == ["second", "third", "first"]

    def test_backward_move(self):
        sl = self._make()
        sl.move(2, 0)
        assert [e.command for e in sl] == ["third", "first", "second"]

    def test_baselines_travel_with_items(self):
        # An item moved from idx 0 to idx 2 should still know its
        # baseline — its individual dirty flag stays False because
        # the *value* didn't change.
        sl = self._make()
        sl.move(0, 2)
        # Item at new idx 2 ("first") is still pristine vs. its baseline.
        assert not sl.is_item_dirty(2)
        # Items at other slots also stayed clean.
        assert not sl.is_item_dirty(0)
        assert not sl.is_item_dirty(1)

    def test_reorder_marks_list_dirty(self):
        # The list comparison is order-sensitive, so any non-identity
        # permutation makes ``is_dirty()`` flip True.
        sl = self._make()
        sl.move(0, 1)
        assert sl.is_dirty()

    def test_discard_all_restores_original_order(self):
        sl = self._make()
        sl.move(2, 0)
        assert [e.command for e in sl] == ["third", "first", "second"]
        sl.discard_all()
        assert [e.command for e in sl] == ["first", "second", "third"]
        assert not sl.is_dirty()

    def test_mark_saved_after_move_locks_new_order(self):
        sl = self._make()
        sl.move(0, 2)
        sl.mark_saved()
        assert not sl.is_dirty()
        # And subsequent discards now snap to the new order.
        sl.move(0, 1)
        sl.discard_all()
        assert [e.command for e in sl] == ["second", "third", "first"]

    def test_move_then_edit_preserves_individual_dirty(self):
        # Move an item, *then* edit it. The combination should mark
        # both the list and the individual item dirty.
        sl = self._make()
        sl.move(0, 2)
        sl[2] = ExecData("exec-once", "first-edited")
        assert sl.is_dirty()
        assert sl.is_item_dirty(2)

    def test_out_of_range_from_idx_raises(self):
        sl = self._make()
        with pytest.raises(IndexError):
            sl.move(5, 0)
        with pytest.raises(IndexError):
            sl.move(-1, 0)

    def test_out_of_range_to_idx_raises(self):
        sl = self._make()
        with pytest.raises(IndexError):
            sl.move(0, 5)
        with pytest.raises(IndexError):
            sl.move(0, -1)

    def test_undo_snapshot_round_trip_preserves_order(self):
        # The page uses ``snapshot()``/``restore()`` for Ctrl+Z. After
        # a move-then-undo cycle, both items and baselines must be in
        # their pre-move positions.
        sl = self._make()
        before_items, before_baselines = sl.snapshot()
        sl.move(0, 2)
        sl.restore(before_items, before_baselines)
        assert [e.command for e in sl] == ["first", "second", "third"]
        assert not sl.is_dirty()


# ---------------------------------------------------------------------------
# Reorder detection
# ---------------------------------------------------------------------------


class TestDetectReorder:
    """``detect_reorder`` should fire on pure reorders, ignore pure
    add/remove churn, and still detect mixed cases where the *common*
    entries between saved and current have different relative order."""

    def _items(self, *commands: str) -> list[ExecData]:
        return [ExecData("exec-once", c) for c in commands]

    def test_no_change(self):
        items = self._items("a", "b", "c")
        assert not detect_reorder(items, items)

    def test_pure_reorder_detected(self):
        saved = self._items("a", "b", "c")
        current = self._items("c", "a", "b")
        assert detect_reorder(saved, current)

    def test_swap_two_detected(self):
        saved = self._items("a", "b")
        current = self._items("b", "a")
        assert detect_reorder(saved, current)

    def test_pure_addition_not_a_reorder(self):
        saved = self._items("a", "b")
        current = self._items("a", "b", "c")
        # ``c`` is new; the common items (a, b) kept their order.
        assert not detect_reorder(saved, current)

    def test_pure_removal_not_a_reorder(self):
        saved = self._items("a", "b", "c")
        current = self._items("a", "c")
        # b was removed but the common items (a, c) kept their order.
        assert not detect_reorder(saved, current)

    def test_addition_with_reorder_detected(self):
        saved = self._items("a", "b", "c")
        current = self._items("c", "a", "b", "d")
        # ``d`` is new and ``a, b, c`` got rearranged — both add AND
        # reorder are happening; we should still flag the reorder.
        assert detect_reorder(saved, current)

    def test_removal_with_reorder_detected(self):
        saved = self._items("a", "b", "c", "d")
        current = self._items("c", "a", "b")
        assert detect_reorder(saved, current)

    def test_empty_lists_no_reorder(self):
        assert not detect_reorder([], [])
        assert not detect_reorder([], self._items("a"))
        assert not detect_reorder(self._items("a"), [])

    def test_single_common_item_cannot_be_reordered(self):
        # Only ``a`` survives; with one common item there's no relative
        # order to compare against.
        saved = self._items("a", "b")
        current = self._items("a", "c")
        assert not detect_reorder(saved, current)

    def test_keyword_change_treated_as_distinct_entry(self):
        # An entry whose keyword changed isn't "the same item moved" —
        # it's a delete + add as far as ``to_line()`` is concerned. The
        # remaining common entries (here, none) determine reorder.
        saved = [ExecData("exec-once", "x"), ExecData("exec", "y")]
        current = [ExecData("exec", "x"), ExecData("exec-once", "y")]
        assert not detect_reorder(saved, current)


# ---------------------------------------------------------------------------
# revert_reorder logic — tested at the SavedList level since that's where
# the page does its work; the page method is a thin orchestrator.
# ---------------------------------------------------------------------------


class TestRevertReorderShape:
    """Validate the rebuilt items+baselines list shape that
    ``AutostartPage.revert_reorder`` constructs by hand. We replicate
    that construction in-test (no GTK needed) so the algorithm is
    covered without spinning up a window."""

    def _build_revert_pairs(
        self, owned: SavedList[ExecData]
    ) -> tuple[list[ExecData], list[ExecData | None]]:
        """Mirror of ``AutostartPage.revert_reorder`` rebuild logic."""
        by_saved_line: dict[str, tuple[ExecData, ExecData | None]] = {}
        new_pairs: list[tuple[ExecData, ExecData | None]] = []
        for idx in range(len(owned)):
            item = owned[idx]
            baseline = owned.get_baseline(idx)
            if baseline is None:
                new_pairs.append((item, baseline))
            else:
                by_saved_line[baseline.to_line()] = (item, baseline)

        items: list[ExecData] = []
        baselines: list[ExecData | None] = []
        for saved in owned.saved:
            pair = by_saved_line.get(saved.to_line())
            if pair is None:
                continue
            items.append(pair[0])
            baselines.append(pair[1])
        for item, baseline in new_pairs:
            items.append(item)
            baselines.append(baseline)
        return items, baselines

    def test_pure_reorder_revert_restores_saved_order(self):
        # Saved: [a, b, c]. User reorders to [c, a, b]. Revert must
        # produce [a, b, c] with all baselines retained.
        owned = SavedList(
            [ExecData("exec-once", c) for c in ("a", "b", "c")],
            key=lambda e: e.to_line(),
        )
        owned.move(2, 0)
        assert [e.command for e in owned] == ["c", "a", "b"]

        items, baselines = self._build_revert_pairs(owned)
        owned.restore(items, baselines)
        assert [e.command for e in owned] == ["a", "b", "c"]
        assert not owned.is_dirty()

    def test_revert_preserves_value_edits(self):
        # Saved: [a, b]. User reorders to [b, a] AND edits ``a``. After
        # revert: order is [a-edited, b], with the edit preserved.
        owned = SavedList(
            [ExecData("exec-once", "a"), ExecData("exec-once", "b")],
            key=lambda e: e.to_line(),
        )
        owned.move(0, 1)  # [b, a]
        # Edit the moved item in place — index 1 currently holds "a".
        owned[1] = ExecData("exec-once", "a-edited")

        items, baselines = self._build_revert_pairs(owned)
        owned.restore(items, baselines)
        assert [e.command for e in owned] == ["a-edited", "b"]
        # ``a-edited`` is dirty (value changed), ``b`` is clean
        # (value matches its baseline).
        assert owned.is_item_dirty(0)
        assert not owned.is_item_dirty(1)

    def test_revert_keeps_new_items_at_end(self):
        # Saved: [a, b]. User reorders to [b, a] AND adds new ``c``.
        # After revert: [a, b, c] — saved-order for the common items,
        # ``c`` appended at the end.
        owned = SavedList(
            [ExecData("exec-once", "a"), ExecData("exec-once", "b")],
            key=lambda e: e.to_line(),
        )
        owned.move(0, 1)  # [b, a]
        owned.append_new(ExecData("exec-once", "c"))  # [b, a, c]

        items, baselines = self._build_revert_pairs(owned)
        owned.restore(items, baselines)
        assert [e.command for e in owned] == ["a", "b", "c"]
        # ``c`` has no baseline (still new), ``a`` and ``b`` do.
        assert owned.get_baseline(2) is None
        assert owned.get_baseline(0) is not None
        assert owned.get_baseline(1) is not None

    def test_revert_does_not_resurrect_removed_items(self):
        # Saved: [a, b, c]. User removes ``b`` and reorders to [c, a].
        # Revert restores order [a, c]; ``b`` stays removed.
        owned = SavedList(
            [ExecData("exec-once", c) for c in ("a", "b", "c")],
            key=lambda e: e.to_line(),
        )
        owned.pop_at(1)  # [a, c]
        owned.move(1, 0)  # [c, a]

        items, baselines = self._build_revert_pairs(owned)
        owned.restore(items, baselines)
        assert [e.command for e in owned] == ["a", "c"]


# ---------------------------------------------------------------------------
# count_pending_changes — sidebar badge ↔ pending-list parity
# ---------------------------------------------------------------------------


class TestCountPendingChanges:
    """The sidebar badge and the pending-list both derive from this
    helper, so any discrepancy between the two would mean a bug here."""

    def _baselines(self, owned: SavedList[ExecData]) -> list[ExecData | None]:
        return [owned.get_baseline(i) for i in range(len(owned))]

    def test_clean_list_zero(self):
        owned = SavedList([ExecData("exec-once", "a")], key=lambda e: e.to_line())
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 0

    def test_one_added_counts_one(self):
        # Explicit type annotation: an empty ``SavedList`` can't have its
        # type parameter inferred, so the lambda's ``e`` would otherwise
        # resolve to ``object`` and pyright would reject ``e.to_line()``.
        owned: SavedList[ExecData] = SavedList([], key=lambda e: e.to_line())
        owned.append_new(ExecData("exec-once", "a"))
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_one_modified_counts_one(self):
        owned = SavedList([ExecData("exec-once", "a")], key=lambda e: e.to_line())
        owned[0] = ExecData("exec-once", "a-edited")
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_one_removed_counts_one(self):
        owned = SavedList(
            [ExecData("exec-once", "a"), ExecData("exec-once", "b")],
            key=lambda e: e.to_line(),
        )
        owned.pop_at(0)
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_pure_reorder_counts_one(self):
        owned = SavedList(
            [ExecData("exec-once", c) for c in ("a", "b", "c")],
            key=lambda e: e.to_line(),
        )
        owned.move(0, 2)
        # No items added, modified, or removed — just a single
        # reorder roll-up.
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_add_plus_reorder_counts_two(self):
        # The exact scenario from the screenshot: one new entry plus
        # a reorder. Both should be counted independently — total 2.
        owned = SavedList(
            [
                ExecData("exec-once", "google-chrome"),
                ExecData("exec-once", "hyprmod"),
            ],
            key=lambda e: e.to_line(),
        )
        owned.move(0, 1)  # reorder
        owned.append_new(ExecData("exec-once", "audacity"))  # add
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 2

    def test_add_modify_remove_reorder_counts_four(self):
        # Stack every kind of change to make sure the count is the sum.
        # ``detect_reorder`` matches items by ``to_line()``, so an
        # edited item no longer "counts" as common with its saved
        # version. The scenario below keeps two unedited items
        # (a, c) and reorders them so the reorder remains visible.
        owned = SavedList(
            [ExecData("exec-once", c) for c in ("a", "b", "c", "d")],
            key=lambda e: e.to_line(),
        )
        owned.move(2, 0)  # [c, a, b, d] — reorder (+1; common a, c flipped)
        owned[2] = ExecData("exec-once", "b-edited")  # modify b (+1)
        owned.pop_at(3)  # remove d (+1)
        owned.append_new(ExecData("exec-once", "e"))  # add (+1)
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 4

    def test_edit_does_not_double_count_as_remove(self):
        # Regression for the bug that triggered this work: editing an
        # entry's command should produce exactly one "modified" entry,
        # not "modified" + "removed (old version)". Tracking surviving
        # baselines (rather than current item lines) is what fixes it.
        owned = SavedList([ExecData("exec-once", "a")], key=lambda e: e.to_line())
        owned[0] = ExecData("exec-once", "a-renamed")
        assert count_pending_changes(owned.saved, list(owned), self._baselines(owned)) == 1

    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="must be the same length"):
            count_pending_changes([], [ExecData("exec-once", "a")], [None, None])


# ---------------------------------------------------------------------------
# drop_target_idx — drag-and-drop hover → SavedList.move target
# ---------------------------------------------------------------------------


class TestDropTargetIdx:
    """Drop math is the only piece of the drag-and-drop wiring that
    isn't a thin wrapper around GTK callbacks. Verifying it here so
    we don't have to drive GTK to be sure the resulting list orders
    are what users expect when they drop on each half of a row."""

    def _move(self, items: list[str], src: int, hover: int, *, before: bool) -> list[str]:
        """Apply the drop-target math, then perform the move on a copy."""
        target = drop_target_idx(src, hover, before)
        result = list(items)
        item = result.pop(src)
        result.insert(target, item)
        return result

    # Source comes BEFORE hover (dragging down)

    def test_above_target_when_src_before(self):
        # A above C → A lands one slot higher than C did.
        assert self._move(["A", "B", "C", "D"], src=0, hover=2, before=True) == ["B", "A", "C", "D"]

    def test_below_target_when_src_before(self):
        # A below C → A lands at C's original slot, C shifts up.
        assert self._move(["A", "B", "C", "D"], src=0, hover=2, before=False) == [
            "B",
            "C",
            "A",
            "D",
        ]

    # Source comes AFTER hover (dragging up)

    def test_above_target_when_src_after(self):
        # D above B → D lands at B's original slot.
        assert self._move(["A", "B", "C", "D"], src=3, hover=1, before=True) == ["A", "D", "B", "C"]

    def test_below_target_when_src_after(self):
        # D below B → D lands one slot below B's original slot.
        assert self._move(["A", "B", "C", "D"], src=3, hover=1, before=False) == [
            "A",
            "B",
            "D",
            "C",
        ]

    # Edges of the list

    def test_drag_first_to_above_first(self):
        # Above the first row, with first as src: identity (target == src)
        assert drop_target_idx(0, 0, before=True) == 0

    def test_drag_last_to_below_last(self):
        # Below the last row, with last as src: also self → no-op
        assert drop_target_idx(3, 3, before=False) == 4
        # Caller must reject this as src == target after computing,
        # since 4 is out of range for a 4-element list. The pure
        # helper just does the math.

    def test_drag_below_to_top(self):
        # Pull D all the way to above A (the very top of the list).
        assert self._move(["A", "B", "C", "D"], src=3, hover=0, before=True) == ["D", "A", "B", "C"]

    def test_drag_top_to_bottom(self):
        # Push A all the way past D (insert below the last row).
        assert self._move(["A", "B", "C", "D"], src=0, hover=3, before=False) == [
            "B",
            "C",
            "D",
            "A",
        ]

    # Adjacent-row edge cases — these compute to a self-position
    # target that the caller is expected to filter out.

    def test_below_self_above_neighbour_is_no_op(self):
        # ``B`` (idx 1) dropped on the top half of ``C`` (idx 2):
        # would mean "B between B and C," which is its current spot.
        assert drop_target_idx(1, 2, before=True) == 1

    def test_above_self_below_neighbour_is_no_op(self):
        # ``B`` dropped on the bottom half of ``A`` (idx 0): "B
        # between A and B," also its current spot.
        assert drop_target_idx(1, 0, before=False) == 1


# ---------------------------------------------------------------------------
# Config write integration
# ---------------------------------------------------------------------------


class TestWriteIntegration:
    def test_exec_lines_emitted_in_autostart_section(self, gui_conf_tmp):
        config.write_all(
            {"general:gaps_in": "5"},
            exec_lines=[
                "exec-once = waybar",
                "exec-once = swaybg -i wallpaper.jpg",
                "exec = pkill -SIGUSR1 waybar",
            ],
        )
        content = gui_conf_tmp.read_text()
        assert "# Autostart" in content
        assert "exec-once = waybar" in content
        assert "exec-once = swaybg -i wallpaper.jpg" in content
        assert "exec = pkill -SIGUSR1 waybar" in content
        # Autostart header should appear after Keybinds (if any) and after
        # plain options — which means after general:gaps_in.
        assert content.index("general:gaps_in") < content.index("# Autostart")

    def test_no_section_when_empty(self, gui_conf_tmp):
        config.write_all({"general:gaps_in": "5"})
        content = gui_conf_tmp.read_text()
        assert "# Autostart" not in content

    def test_round_trip_through_read_all_sections(self, gui_conf_tmp):
        config.write_all(
            {},
            exec_lines=[
                "exec-once = waybar",
                "exec = something",
            ],
        )
        _, sections = config.read_all_sections()
        assert sections.get(config.KEYWORD_EXEC_ONCE) == ["exec-once = waybar"]
        assert sections.get(config.KEYWORD_EXEC) == ["exec = something"]

    def test_collect_section_picks_up_exec(self, gui_conf_tmp):
        config.write_all(
            {},
            exec_lines=[
                "exec-once = waybar",
                "exec-once = swaybg",
                "exec = pkill",
            ],
        )
        _, sections = config.read_all_sections()
        # Same call shape the AutostartPage uses on init.
        result = config.collect_section(sections, *EXEC_KEYWORDS)
        # The order between *EXEC_KEYWORDS groups follows the EXEC_KEYWORDS
        # tuple (exec-once first, then exec) — within a group, source order
        # is preserved.
        assert result == [
            "exec-once = waybar",
            "exec-once = swaybg",
            "exec = pkill",
        ]
