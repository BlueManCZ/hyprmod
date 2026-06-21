"""Abstract base for special pages that own their dirty/save/discard lifecycle.

The "section pages" (animations, binds, cursor, monitors, autostart,
env-vars, window-rules, layer-rules) all manage their own state
independently of ``AppState``: they load their slice of the config on
init, expose ``is_dirty``/``mark_saved``/``discard`` to the window, and
most of them push undo entries when state changes.

This base class consolidates the constructor boilerplate (``window``,
``on_dirty_changed``, ``push_undo``), provides ``_notify_dirty`` and the
``_undo_track`` context manager, and leaves only the page-specific snapshot
plumbing for subclasses to fill in.

:class:`SavedListSectionPage` is a more specialised base for the pages
backed by a :class:`SavedList[T]` of line-serialisable items (autostart,
env-vars, window-rules, layer-rules). It absorbs the byte-identical
keyboard reorder, deleted-restore, pending-change roll-up, and the
``is_dirty``/``mark_saved``/``discard``/``reload_from_saved`` lifecycle
methods so each page keeps only the row/group rendering and item-level
actions.

Reorder (keyboard for every page, plus drag-and-drop where the page wires
it via ``self._reorder.attach``) lives in :class:`RowReorderController`;
the page supplies :meth:`_perform_reorder` and :meth:`_is_valid_move`.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from html import escape as html_escape
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from gi.repository import Adw, GLib, Gtk

from hyprmod.core.change_tracking import (
    LineSerialisable,
    count_pending_changes,
    detect_reorder,
    iter_item_changes,
)
from hyprmod.core.config import display_path
from hyprmod.core.ownership import SavedList
from hyprmod.core.pending import ChangeKind, PendingChange
from hyprmod.core.undo import SavedListSnapshot
from hyprmod.ui import clear_children
from hyprmod.ui.reorder import RowReorderController

if TYPE_CHECKING:
    from hyprmod.core.undo import UndoEntry
    from hyprmod.window import HyprModWindow


class SectionPage(ABC):
    """Base class for pages independent of ``AppState``."""

    def __init__(
        self,
        window: "HyprModWindow",
        on_dirty_changed: Callable[[], None] | None = None,
        push_undo: Callable[["UndoEntry"], None] | None = None,
    ):
        self._window = window
        self._on_dirty_changed = on_dirty_changed
        self._push_undo = push_undo

    # ── lifecycle (subclasses MUST implement) ──

    @abstractmethod
    def is_dirty(self) -> bool: ...

    @abstractmethod
    def mark_saved(self) -> None: ...

    @abstractmethod
    def discard(self) -> None: ...

    # ── pending-changes (default: no entries) ──
    #
    # Pages that surface their dirty state in the Pending Changes view
    # override this to yield one :class:`PendingChange` per item.

    def iter_pending_changes(self) -> Iterable[PendingChange]:
        return ()

    # ── shared scaffolding ──

    def _notify_dirty(self) -> None:
        """Notify the parent window that this page's dirty state may have changed."""
        if self._on_dirty_changed is not None:
            self._on_dirty_changed()

    @contextmanager
    def _undo_track(self):
        """Capture before/after snapshots and push an undo entry on change.

        Subclasses opt in by overriding ``_capture_undo``, ``_undo_key``, and
        ``_build_undo_entry``. Calling this without those overrides raises
        ``NotImplementedError``.

        The "new" snapshot is captured only after the key check passes, so
        subclasses with expensive snapshots (deep copies, etc.) don't pay
        when nothing changed.
        """
        old = self._capture_undo()
        old_key = self._undo_key()
        yield
        if self._push_undo is None:
            return
        if old_key is not None and self._undo_key() == old_key:
            return
        new = self._capture_undo()
        self._push_undo(self._build_undo_entry(old, new))

    # ── undo hooks (override to enable _undo_track) ──

    def _capture_undo(self) -> Any:
        """Snapshot state for undo. Override to enable ``_undo_track``."""
        raise NotImplementedError("override _capture_undo() to use _undo_track()")

    def _undo_key(self) -> object | None:
        """Cheap comparable key for change detection.

        Returning ``None`` disables the fast-path comparison; the
        ``_build_undo_entry`` override is then responsible for any change
        detection it needs (otherwise an entry will be pushed on every yield).
        """
        return None

    def _build_undo_entry(self, old: Any, new: Any) -> "UndoEntry":
        """Build the undo entry from old + new snapshots.

        Override to enable ``_undo_track``. The base class handles the push.
        """
        raise NotImplementedError("override _build_undo_entry() to use _undo_track()")


class SavedListSectionPage[T: LineSerialisable](SectionPage):
    """SectionPage backed by a :class:`SavedList[T]` of line-serialisable items.

    Specialisation of :class:`SectionPage` for the autostart, env-vars,
    window-rules, and layer-rules pages — each maintains a single
    ``_owned: SavedList[T]`` plus a parallel ``_rows_by_idx`` widget map
    and rebuilds the list with ``_rebuild_list``. The operations that all
    four pages duplicated verbatim live here:

    - **Reorder** via :class:`RowReorderController` (``self._reorder``):
      keyboard for every page, drag-and-drop where the page calls
      ``self._reorder.attach``. The page provides :meth:`_perform_reorder`
      (apply + undo) and :meth:`_is_valid_move` (subclasses with cross-group
      constraints override the latter).
    - **Deleted-baseline detection** (``_deleted_baselines``) for the
      "Removed (pending save)" group.
    - **Reorder roll-up** (``is_reordered``, ``revert_reorder``) for
      pending-changes display.
    - **Pending-change count** (``pending_change_count``) for the
      sidebar badge.
    - **SectionPage lifecycle** (``is_dirty``, ``mark_saved``,
      ``discard``, ``reload_from_saved``) — pages that need extra
      runtime sync (e.g. window-rules) override and call ``super``.

    Subclasses must initialise ``self._owned`` in ``__init__`` (after
    ``super().__init__``), implement ``_load``, ``_make_row``, ``_on_add``,
    and ``_build_empty_state``. The default :meth:`_rebuild_list` handles
    the standard skeleton (order-hint, managed group(s), deleted, external,
    empty); pages that need pre-rebuild snapshot work override
    :meth:`_pre_rebuild`, and pages with non-single-group layouts (autostart
    splits ``exec`` vs ``exec-once``) override :meth:`_build_managed_groups`.
    """

    # Subclasses set this in __init__ before any base method runs.
    _owned: SavedList[T]
    # The vertical content box the shared :meth:`_rebuild_list` populates.
    # Subclasses assign this from ``make_page_layout`` in their ``build()``.
    _content_box: Gtk.Box

    # The window attribute that holds this page instance — used by the
    # undo-redo dispatch to look the page up when replaying a snapshot.
    # Subclasses must set this so the shared ``_build_undo_entry`` below
    # can stamp the snapshot with the right page.
    _page_attr: ClassVar[str]

    # Pending-changes display metadata — subclasses set these so the
    # shared ``iter_pending_changes`` below can stamp each emitted
    # :class:`PendingChange` without each page reimplementing the loop.
    _pending_category: ClassVar[str]
    _pending_navigate_to: ClassVar[str]
    _pending_icon: ClassVar[str]

    # Default managed-group rendering knobs. Pages with a single managed
    # group (window-rules, layer-rules, env-vars) set these; multi-group
    # pages (autostart) override :meth:`_build_managed_groups` directly
    # and these go unused.
    _group_title: ClassVar[str] = ""
    _group_add_tooltip: ClassVar[str] = "Add another entry"

    # Read-only icon used by :meth:`_build_external_row` for the prefix on
    # external rows. Pages with an external section set this; pages without
    # (autostart) leave it blank.
    _external_prefix_icon: ClassVar[str] = ""

    def __init__(
        self,
        window: "HyprModWindow",
        on_dirty_changed: Callable[[], None] | None = None,
        push_undo: Callable[["UndoEntry"], None] | None = None,
    ):
        super().__init__(window, on_dirty_changed, push_undo)
        # Per-instance defaults so subclasses don't have to remember to
        # init them (and so the mutable list isn't shared across pages
        # via a class attribute).
        self._rows_by_idx: list[Adw.ActionRow | None] = []
        self._external: list[Any] = []
        # Shared reorder plumbing. Keyboard-only pages call
        # ``attach_keyboard``; pages that also drag call ``attach``.
        self._reorder = RowReorderController(
            move=self._perform_reorder,
            iter_rows=lambda: [row for row in self._rows_by_idx if row is not None],
            can_move=self._is_valid_move,
        )

    # ── Subclass hooks ──

    def _load(self, saved_sections: dict[str, list[str]] | None) -> None:
        """Re-read ``self._owned`` from *saved_sections* (or the live config)."""
        raise NotImplementedError

    def _make_row(self, idx: int, item: T) -> Adw.ActionRow:
        """Build the managed-content row for *item* at index *idx*."""
        raise NotImplementedError

    def _on_add(self) -> None:
        """Open the add dialog. Bound to managed-group "+" buttons."""
        raise NotImplementedError

    def _build_empty_state(self) -> Gtk.Widget:
        """Build the empty-state widget shown when nothing is in the list."""
        raise NotImplementedError

    # Optional hooks (default no-op).

    def _pre_rebuild(self) -> None:
        """Snapshot computations needed before rendering. Override for env-vars."""

    def _build_order_hint(self) -> Gtk.Widget | None:
        """Reorder-hint widget, shown when ``len(self._owned) >= 2``.

        Default ``None`` skips the hint. Pages that benefit from it
        (window/layer rules, env-vars, autostart) override to return a
        :func:`make_inline_hint` widget.
        """
        return None

    # ── Standard list rendering ──

    def _rebuild_list(self, focus_idx: int = -1) -> None:
        """Repaint the list. ``focus_idx`` re-focuses the row at that index.

        Standard layout: order-hint, managed group(s), deleted-baseline
        group, external section, empty state — in that order. Pages with
        non-default layout (e.g. autostart's per-keyword grouping)
        override :meth:`_build_managed_groups`.
        """
        self._pre_rebuild()
        clear_children(self._content_box)
        self._rows_by_idx = [None] * len(self._owned)

        if len(self._owned) >= 2:
            hint = self._build_order_hint()
            if hint is not None:
                self._content_box.append(hint)

        for widget in self._build_managed_groups():
            self._content_box.append(widget)

        deleted = self._deleted_baselines()
        if deleted:
            self._content_box.append(self._build_deleted_group(deleted))

        if self._external:
            for widget in self._build_external_section():
                self._content_box.append(widget)

        if len(self._owned) == 0 and not deleted and not self._external:
            self._content_box.append(self._build_empty_state())

        if 0 <= focus_idx < len(self._rows_by_idx):
            target = self._rows_by_idx[focus_idx]
            if target is not None:
                # Defer to idle so the row is mapped before grab_focus runs
                # (see :meth:`_grab_focus_once` for the SOURCE_REMOVE rationale).
                GLib.idle_add(self._grab_focus_once, target)

    def _build_managed_groups(self) -> list[Gtk.Widget]:
        """Build the managed-content group(s).

        Default: a single :class:`Adw.PreferencesGroup` built via
        :meth:`_build_managed_group` (or none if the list is empty).
        Override for layouts that split the items into multiple groups
        (autostart's ``exec`` vs ``exec-once``).
        """
        if len(self._owned) == 0:
            return []
        return [self._build_managed_group(self._group_title, range(len(self._owned)))]

    def _build_managed_group(self, title: str, indices: range | list[int]) -> Adw.PreferencesGroup:
        """Standard managed group: title, ``N <unit>`` description, header "+", rows.

        *indices* is the slice of ``self._owned`` to render. The default
        single-group layout passes the full range; multi-group pages
        (autostart) call this once per keyword with the matching indices.
        """
        idx_list = list(indices)
        n = len(idx_list)
        group = Adw.PreferencesGroup(title=title)
        group.set_description(f"{n} {self._unit_label(n)}")

        add_btn = Gtk.Button(icon_name="list-add-symbolic")
        add_btn.set_valign(Gtk.Align.CENTER)
        add_btn.add_css_class("flat")
        add_btn.set_tooltip_text(self._group_add_tooltip)
        add_btn.connect("clicked", lambda _b: self._on_add())
        group.set_header_suffix(add_btn)

        for idx in idx_list:
            group.add(self._make_row(idx, self._owned[idx]))
        return group

    # ── Undo / Redo (default implementations) ──

    def _capture_undo(self) -> tuple[list[T], list[T | None]]:
        return self._owned.snapshot()

    def _undo_key(self) -> list[str]:
        return [e.to_line() for e in self._owned]

    def _build_undo_entry(
        self,
        old: tuple[list[T], list[T | None]],
        new: tuple[list[T], list[T | None]],
    ) -> SavedListSnapshot:
        old_items, old_baselines = old
        new_items, new_baselines = new
        return SavedListSnapshot(
            page_attr=self._page_attr,
            old_items=old_items,
            new_items=new_items,
            old_baselines=old_baselines,
            new_baselines=new_baselines,
        )

    def restore_snapshot(self, items: list[T], baselines: list[T | None]) -> None:
        """Restore state from an undo/redo snapshot.

        Default behaviour: rewind ``_owned`` and repaint. Pages that need
        runtime side effects (e.g. window-rules re-syncing setprop overrides)
        override this and call ``super``.
        """
        self._owned.restore(items, baselines)
        self._rebuild_list()
        self._notify_dirty()

    # ── SectionPage lifecycle (default implementations) ──

    def is_dirty(self) -> bool:
        return self._owned.is_dirty()

    def mark_saved(self) -> None:
        self._owned.mark_saved()
        self._rebuild_list()

    def discard(self) -> None:
        self._owned.discard_all()
        self._rebuild_list()

    def reload_from_saved(self, saved_sections: dict[str, list[str]]) -> None:
        """Re-load baseline from the given saved sections (after profile switch)."""
        self._load(saved_sections)
        self._rebuild_list()

    # ── Reorder (keyboard + drag-and-drop via RowReorderController) ──

    def _perform_reorder(self, src: int, target: int) -> bool:
        """Apply a reorder (drag-drop or keyboard) and push one undo entry.

        Returns whether the move happened, which keyboard handling propagates
        as the "event consumed" flag so an illegal Alt+arrow falls through to
        default focus traversal. ``RowReorderController`` has already gated the
        move through :meth:`_is_valid_move`; the range/no-op guard here keeps a
        same-position drop from pushing an empty undo entry.
        """
        n = len(self._owned)
        if target == src or not (0 <= src < n and 0 <= target < n):
            return False
        with self._undo_track():
            self._owned.move(src, target)
        self._notify_dirty()
        self._rebuild_list(focus_idx=target)
        return True

    def _is_valid_move(self, src_idx: int, dst_idx: int) -> bool:
        """True if moving *src_idx* to *dst_idx* is a legal reorder.

        Default: any distinct, in-range pair. Override to add cross-group
        constraints — e.g. autostart's same-keyword check that prevents
        flipping ``exec`` ↔ ``exec-once`` by reordering.
        """
        n = len(self._owned)
        if src_idx < 0 or dst_idx < 0:
            return False
        if src_idx == dst_idx:
            return False
        return src_idx < n and dst_idx < n

    @staticmethod
    def _grab_focus_once(widget: Gtk.Widget) -> bool:
        """One-shot ``GLib.idle_add`` callback for post-rebuild focus restore.

        ``Widget.grab_focus`` returns ``True`` on success — an idle handler
        reading that as "fire me again" produces an infinite focus-grab loop
        that freezes Tab navigation, so this wrapper hard-returns
        ``GLib.SOURCE_REMOVE``.
        """
        widget.grab_focus()
        return GLib.SOURCE_REMOVE

    # ── Deleted-baseline detection ──

    def _deleted_baselines(self) -> list[T]:
        """Return saved entries that are no longer in the owned list.

        Shares :func:`iter_item_changes`'s positional baseline tracking
        so an edited item (where ``to_line()`` shifted but the row
        position still carries the original baseline) is reported as a
        modification, not as remove + add. Using set membership on
        ``to_line()`` alone double-counts those edits as deletions —
        the surviving baseline never lines up with the new ``to_line()``.
        """
        baselines = [self._owned.get_baseline(i) for i in range(len(self._owned))]
        changes = iter_item_changes(self._owned.saved, list(self._owned), baselines)
        return [item for kind, _, item, _ in changes if kind == "removed"]

    # ── Reorder / pending-change roll-ups ──

    def is_reordered(self) -> bool:
        """True if the *common* items between saved and current differ in order."""
        return detect_reorder(self._owned.saved, list(self._owned))

    def pending_change_count(self) -> int:
        """Number of distinct pending-change entries the page would surface.

        Drives the sidebar badge; mirrors the iterator the pending-changes
        page uses, so the badge count and pending-list length stay in
        lockstep by construction.
        """
        if not self.is_dirty():
            return 0
        baselines = [self._owned.get_baseline(i) for i in range(len(self._owned))]
        return count_pending_changes(self._owned.saved, list(self._owned), baselines)

    def revert_reorder(self) -> None:
        """Restore the saved order while preserving other dirty changes.

        - Items present in both saved and current are repositioned to
          their saved-order slots; any in-flight value edits to those
          items are kept.
        - Newly-added items (no baseline) keep their values and slot in
          at the end.
        - Items the user removed stay removed; this revert isn't a
          general "undo all".

        Pushes a single undo entry so Ctrl+Z restores the pre-revert
        order in one step.
        """
        # Map saved-line -> (current_item, baseline) for items that
        # originated from the saved snapshot. ``baseline.to_line()`` is
        # the stable identity even if the user has edited the value.
        by_saved_line: dict[str, tuple[T, T | None]] = {}
        new_pairs: list[tuple[T, T | None]] = []

        for idx in range(len(self._owned)):
            item = self._owned[idx]
            baseline = self._owned.get_baseline(idx)
            if baseline is None:
                new_pairs.append((item, baseline))
            else:
                by_saved_line[baseline.to_line()] = (item, baseline)

        rebuilt_items: list[T] = []
        rebuilt_baselines: list[T | None] = []
        for saved in self._owned.saved:
            pair = by_saved_line.get(saved.to_line())
            if pair is None:
                # User removed this entry; not coming back from a reorder revert.
                continue
            item, baseline = pair
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)
        # Newly-added rows keep their existing positions at the end —
        # they have no saved-order to revert to.
        for item, baseline in new_pairs:
            rebuilt_items.append(item)
            rebuilt_baselines.append(baseline)

        with self._undo_track():
            self._owned.restore(rebuilt_items, rebuilt_baselines)
        self._notify_dirty()
        self._rebuild_list()

    # ── Pending changes (subclasses provide the per-item summarizers) ──
    #
    # The added/modified/removed iteration and the reorder roll-up are
    # identical across every SavedList-backed page. Subclasses only
    # implement ``_summarize_item`` (for added/removed entries) and
    # ``_summarize_modified`` (for the baseline → item diff).

    def _summarize_item(self, item: T) -> tuple[str, str]:
        """Return ``(title, subtitle)`` for a fresh or deleted item."""
        raise NotImplementedError

    def _summarize_modified(self, baseline: T, item: T) -> tuple[str, str]:
        """Return ``(title, subtitle)`` for a modified item; subtitle shows the diff."""
        raise NotImplementedError

    def iter_pending_changes(self) -> Iterator[PendingChange]:
        if not self.is_dirty():
            return
        baselines = [self._owned.get_baseline(i) for i in range(len(self._owned))]
        for kind, idx, item, baseline in iter_item_changes(
            self._owned.saved, list(self._owned), baselines
        ):
            if kind == "added":
                title, subtitle = self._summarize_item(item)
                yield self._make_pending(
                    kind="added",
                    title=title,
                    subtitle=f"new · {subtitle}",
                    revert=lambda i=idx: self._discard_at(i),
                )
            elif kind == "modified" and baseline is not None:
                title, subtitle = self._summarize_modified(baseline, item)
                yield self._make_pending(
                    kind="modified",
                    title=title,
                    subtitle=subtitle,
                    revert=lambda i=idx: self._discard_at(i),
                )
            elif kind == "removed":
                title, subtitle = self._summarize_item(item)
                yield self._make_pending(
                    kind="removed",
                    title=title,
                    subtitle=f"deleted · {subtitle}",
                    revert=lambda e=item: self._on_restore_deleted(e),
                )
        if self.is_reordered():
            common = len(
                {e.to_line() for e in self._owned} & {b.to_line() for b in self._owned.saved}
            )
            yield self._make_pending(
                kind="modified",
                title="Reordered",
                subtitle=f"{common} {self._unit_label(common)} in a different order",
                revert=self.revert_reorder,
            )

    def _make_pending(
        self,
        *,
        kind: ChangeKind,
        title: str,
        subtitle: str,
        revert: Callable[[], None],
    ) -> PendingChange:
        """Build a :class:`PendingChange` stamped with the page's class metadata."""
        return PendingChange(
            category=self._pending_category,
            title=title,
            subtitle=subtitle,
            kind=kind,
            revert=revert,
            navigate_to=self._pending_navigate_to,
            icon=self._pending_icon,
        )

    # ── Item CRUD (default vanilla; subclasses override for side effects) ──

    def _commit_appended(self, item: T) -> None:
        """Append *item* to the owned list and repaint."""
        with self._undo_track():
            self._owned.append_new(item)
        self._notify_dirty()
        self._rebuild_list()

    def _commit_replaced(self, idx: int, item: T) -> None:
        """Replace the owned entry at *idx* with *item* and repaint."""
        with self._undo_track():
            self._owned[idx] = item
        self._notify_dirty()
        self._rebuild_list()

    def _on_delete_at(self, idx: int) -> None:
        """Remove the entry at *idx* from the owned list.

        Pure SavedList mutation. Pages that need to propagate side
        effects to the running compositor (e.g. window-rules clearing
        per-window setprop overrides) override this and call ``super``
        — or do their own thing entirely.
        """
        if idx < 0 or idx >= len(self._owned):
            return
        with self._undo_track():
            self._owned.pop_at(idx)
        self._notify_dirty()
        self._rebuild_list()

    def _discard_at(self, idx: int) -> None:
        """Revert the entry at *idx* to its saved value, or delete if unsaved."""
        baseline = self._owned.get_baseline(idx)
        if baseline is None:
            self._on_delete_at(idx)
            return
        with self._undo_track():
            self._owned.discard_at(idx)
        self._notify_dirty()
        self._rebuild_list()

    def _on_restore_deleted(self, item: T) -> None:
        """Restore a previously-deleted *item* to its saved slot.

        Routes through :meth:`SavedList.restore_deleted` so the row
        comes back with its saved baseline at the slot consistent with
        the saved order — a pure delete-then-restore round trip leaves
        the page non-dirty.
        """
        with self._undo_track():
            self._owned.restore_deleted(item)
        self._notify_dirty()
        self._rebuild_list()

    # ── Shared rendering: deleted group + external section ──
    #
    # Subclasses set the noun pair via class attrs and (where relevant)
    # implement ``_deleted_row_summary`` for the default deleted-row
    # render, ``_make_external_row`` for the external-section row, and
    # ``_build_external_hint`` for the inline note above it. Pages with
    # richer rendering can override ``_make_deleted_row`` or the entire
    # ``_build_deleted_group`` (autostart currently does the latter).

    # Noun used in pluralisation copy ("1 rule" / "2 rules", "1 entry"
    # / "2 entries"). Default reads naturally for autostart entries;
    # other pages override.
    _unit_singular: str = "entry"
    _unit_plural: str = "entries"
    # How many subtitle lines the default deleted-row renders.
    # Window/layer rules with long matcher regexes set this to 2.
    _deleted_subtitle_lines: int = 1

    def _unit_label(self, n: int) -> str:
        """Pluralised noun for *n* entries ('1 rule' / '5 rules')."""
        return self._unit_singular if n == 1 else self._unit_plural

    def _build_deleted_group(self, deleted: list[T]) -> Adw.PreferencesGroup:
        """Render the "Removed (pending save)" group for *deleted* items."""
        group = Adw.PreferencesGroup(title="Removed (pending save)")
        n = len(deleted)
        group.set_description(f"{n} {self._unit_label(n)} will be removed on save")
        for item in deleted:
            group.add(self._make_deleted_row(item))
        return group

    def _make_deleted_row(self, item: T) -> Adw.ActionRow:
        """Default deleted-row factory; subclasses override for rich rendering.

        Uses :meth:`_deleted_row_summary` for the (title, subtitle) pair and
        attaches a restore button wired to :meth:`_on_restore_deleted`.
        """
        title, subtitle = self._deleted_row_summary(item)
        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(self._deleted_subtitle_lines)
        row.add_css_class("option-default")
        row.set_opacity(0.65)

        restore_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        restore_btn.set_valign(Gtk.Align.CENTER)
        restore_btn.add_css_class("flat")
        restore_btn.set_tooltip_text(f"Restore this {self._unit_singular}")
        restore_btn.connect("clicked", lambda _b, e=item: self._on_restore_deleted(e))
        row.add_suffix(restore_btn)
        return row

    def _deleted_row_summary(self, item: T) -> tuple[str, str]:
        """``(title, subtitle)`` for the default :meth:`_make_deleted_row`.

        Subclasses that don't override ``_make_deleted_row`` must implement
        this. Defaults to ``(str(item), "")`` so the abstract intent surfaces
        as garbled but legible output rather than an exception if a subclass
        forgets to override.
        """
        return str(item), ""

    # External section — read-only display of items from outside our managed
    # file. Subclasses with external entries populate ``self._external`` in
    # ``_load``; pages without external entries inherit the empty default.

    def _build_external_section(self) -> list[Gtk.Widget]:
        """Build the read-only external-entry display.

        Returns an inline hint widget plus one ``Adw.PreferencesGroup``
        per source file. Subclasses with richer per-rebuild context
        (e.g. env-vars' "overridden by managed" computation) can override
        this method directly and call :meth:`_build_external_file_group`
        for the file-grouped portion.
        """
        widgets: list[Gtk.Widget] = [self._build_external_hint()]
        by_file: dict[Path, list] = {}
        for ext in self._external:
            by_file.setdefault(ext.source_path, []).append(ext)
        for source_path, entries in by_file.items():
            widgets.append(self._build_external_file_group(source_path, entries))
        return widgets

    def _build_external_file_group(
        self,
        source_path: Path,
        entries: list,
    ) -> Adw.PreferencesGroup:
        """A ``PreferencesGroup`` containing every external entry from one file."""
        group = Adw.PreferencesGroup(title=display_path(source_path))
        n = len(entries)
        group.set_description(f"{n} {self._unit_label(n)}")
        for ext in entries:
            group.add(self._make_external_row(ext))
        return group

    def _make_external_row(self, ext: Any) -> Adw.ActionRow:
        """Build a read-only row for one external entry. Subclasses override."""
        raise NotImplementedError

    def _make_readonly_external_row(
        self,
        title: str,
        subtitle: str,
        source_path: Path,
        lineno: int,
    ) -> Adw.ActionRow:
        """Standard read-only external row: dimmed prefix icon, lock-icon suffix.

        Used by window-rules and layer-rules — same shape with only the
        prefix icon differing (set via ``_external_prefix_icon``). Pages
        with richer external rows (env-vars' override button / Overridden
        badge) override :meth:`_make_external_row` directly instead.
        """
        row = Adw.ActionRow(
            title=html_escape(title),
            subtitle=html_escape(subtitle),
        )
        row.set_title_lines(1)
        row.set_subtitle_lines(1)
        row.add_css_class("option-default")
        row.set_opacity(0.65)
        row.set_tooltip_text(f"{source_path}:{lineno}")

        if self._external_prefix_icon:
            prefix = Gtk.Image.new_from_icon_name(self._external_prefix_icon)
            prefix.set_opacity(0.4)
            prefix.set_pixel_size(28)
            row.add_prefix(prefix)

        lock_icon = Gtk.Image.new_from_icon_name("changes-prevent-symbolic")
        lock_icon.set_opacity(0.4)
        lock_icon.set_valign(Gtk.Align.CENTER)
        row.add_suffix(lock_icon)
        return row

    def _build_external_hint(self) -> Gtk.Widget:
        """Inline-hint widget rendered above the external section."""
        raise NotImplementedError
