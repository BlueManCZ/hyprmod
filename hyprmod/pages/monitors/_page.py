"""Monitor management page — orchestrates cards, preview, and Hyprland IPC."""

import copy
from contextlib import contextmanager

from gi.repository import Adw, GLib, Gtk
from hyprland_monitors import get_monitor_capabilities
from hyprland_monitors.monitors import (
    MonitorState,
    adjust_neighbors,
    all_monitors_connected,
    compute_valid_scales,
    lines_from_monitors,
    merge_saved_state,
    nearest_scale_index,
    parse_extras,
    parse_mode,
    validate_mirror,
)
from hyprland_socket import HyprlandError

from hyprmod.core import config
from hyprmod.core.ownership import OwnershipSet
from hyprmod.core.undo import MonitorsUndoEntry
from hyprmod.pages.monitors._card import MonitorCard
from hyprmod.pages.monitors._confirm import ConfirmController
from hyprmod.ui import clear_children, make_page_layout
from hyprmod.ui.monitor_preview import MonitorLayoutPreview
from hyprmod.ui.timer import Timer

_EXTRA_FIELDS = ("bit_depth", "vrr", "color_management", "mirror_of")


class MonitorsPage:
    """Builds the monitor management page."""

    _RESTORABLE_FIELDS = (
        "width",
        "height",
        "refresh_rate",
        "x",
        "y",
        "scale",
        "transform",
        "bit_depth",
        "vrr",
        "color_management",
        "mirror_of",
        "disabled",
    )

    def __init__(self, window, on_dirty_changed=None, push_undo=None, saved_sections=None):
        self._window = window
        self._on_dirty_changed = on_dirty_changed
        self._push_undo = push_undo
        self._monitors: list[MonitorState] = []
        self._cards: list[MonitorCard] = []
        self._preview: MonitorLayoutPreview | None = None
        self._drag_hint: Gtk.Label | None = None
        self._gap_banner: Adw.Banner | None = None
        self._confirm: ConfirmController | None = None
        self._applying = False
        self._resync_timer = Timer()
        self._saved_monitors: list[MonitorState] = []
        self._confirmed_monitors: list[MonitorState] = []
        self._content_box: Gtk.Box | None = None
        self._ownership = OwnershipSet()
        self._last_dragged_idx = -1
        self._drag_undo_state = None

        self._reload_monitors(saved_sections=saved_sections)
        self._save_snapshot()
        self._save_confirmed_snapshot()

        window.hypr.on_change(self._on_hypr_change)

    # -- Undo / Redo --

    def _monitors_key(self):
        """Serialized representation of current monitor state for comparison."""
        managed = [m for m in self._monitors if self._ownership.is_owned(m.name)]
        return sorted(lines_from_monitors(managed)), frozenset(self._ownership.owned)

    def _snap_undo_state(self):
        """Capture current monitors + ownership for undo."""
        return copy.deepcopy(self._monitors), self._ownership.snapshot()

    def _push_undo_from(self, old_monitors, old_owned, *, old_key=None):
        """Push a MonitorsUndoEntry given a captured 'before' state."""
        if not self._push_undo:
            return
        if old_key is not None and self._monitors_key() == old_key:
            return
        new_monitors, new_owned = self._snap_undo_state()
        self._push_undo(
            MonitorsUndoEntry(
                old_monitors=old_monitors,
                new_monitors=new_monitors,
                old_owned=old_owned,
                new_owned=new_owned,
            )
        )

    @contextmanager
    def _undo_track(self):
        """Capture before/after monitors state and push an undo entry."""
        old_monitors, old_owned = self._snap_undo_state()
        old_key = self._monitors_key()
        yield
        self._push_undo_from(old_monitors, old_owned, old_key=old_key)

    def restore_snapshot(self, monitor_copies, owned_names):
        """Restore monitors state from an undo/redo snapshot."""
        by_name = {m.name: m for m in monitor_copies}
        for mon in self._monitors:
            saved = by_name.get(mon.name)
            if saved:
                for field in self._RESTORABLE_FIELDS:
                    setattr(mon, field, getattr(saved, field))
        self._ownership.restore(owned_names)
        self._push_to_ui()
        self._commit_to_hyprland()

    # -- Data loading --

    def _on_hypr_change(self, category: str, key: str | None):
        """React to library state changes (e.g. after sync for profile activation)."""
        if category != "monitors" or self._applying:
            return
        self._resync_timer.cancel()
        self._reload_monitors()
        self._save_snapshot()
        self._save_confirmed_snapshot()
        if self._content_box is not None:
            self._rebuild()

    def _reload_monitors(self, saved_sections=None):
        """Fetch monitors from IPC, snap scales, and merge saved config."""
        self._monitors = sorted(self._window.hypr.monitors.get_all() or [], key=lambda m: m.name)
        self._snap_scales()
        if saved_sections is not None:
            saved = config.collect_section(saved_sections, "monitor")
        else:
            _, sections = config.read_all_sections()
            saved = config.collect_section(sections, "monitor")
        if saved:
            merge_saved_state(self._monitors, saved)
        self._ownership = OwnershipSet(self._managed_names_from_lines(saved))
        # Since monitor= is all-or-nothing, our line replaces the user's
        # entire line.  IPC may still report extras from the user's config
        # that our line doesn't include — clear them so the UI shows what
        # our config actually sets.
        self._clear_unmanaged_extras(saved)

    @staticmethod
    def _monitor_name_from_line(line: str) -> str:
        """Extract the monitor name from a ``monitor = NAME, ...`` config line."""
        cleaned = line.removeprefix("monitor").strip().removeprefix("=").strip()
        return cleaned.split(",")[0].strip()

    def _clear_unmanaged_extras(self, saved_lines: list[str]):
        """Clear IPC-leaked extras on managed monitors not in our config."""
        saved_extras: dict[str, set[str]] = {}
        for line in saved_lines:
            extras = parse_extras(line)
            name = self._monitor_name_from_line(line)
            saved_extras[name] = set(extras.keys())
        for mon in self._monitors:
            if not self._ownership.is_owned(mon.name):
                continue
            managed = saved_extras.get(mon.name, set())
            for field in _EXTRA_FIELDS:
                if field not in managed:
                    setattr(mon, field, None)

    def _snap_scales(self):
        """Map 2dp scales from Hyprland to full-precision 1/120 values."""
        for mon in self._monitors:
            vs = compute_valid_scales(mon.width, mon.height)
            si = nearest_scale_index(vs, mon.scale)
            mon.scale = vs[si][0]

    @classmethod
    def _managed_names_from_lines(cls, saved_lines: list[str]) -> set[str]:
        return {cls._monitor_name_from_line(line) for line in saved_lines} - {""}

    # -- UI building --

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh monitors")
        refresh_btn.connect("clicked", self._on_refresh)
        page_header.pack_start(refresh_btn)

        toolbar_view, page_box, self._content_box, _ = make_page_layout(header=page_header)

        confirm_banner = Adw.Banner(title="")
        self._confirm = ConfirmController(
            confirm_banner,
            is_dirty=self.is_dirty,
            on_revert=self._revert_monitors,
            on_confirmed=self._on_confirmed,
        )
        page_box.prepend(confirm_banner)

        self._rebuild()
        return toolbar_view

    def _rebuild(self):
        if self._content_box is None:
            return
        clear_children(self._content_box)

        if not self._monitors:
            self._content_box.append(
                Adw.StatusPage(
                    title="No Monitors Detected",
                    description="Could not read monitor information from Hyprland.",
                    icon_name="computer-symbolic",
                )
            )
            return

        self._preview = MonitorLayoutPreview(
            on_position_changed=self._on_preview_drag,
            on_drag_started=self._on_preview_drag_start,
            on_drag_ended=self._on_preview_drag_end,
        )
        self._preview.set_monitors(self._monitors)
        active = [m for m in self._monitors if not m.disabled and not m.mirror_of]
        multi = len(active) > 1
        self._preview.set_draggable(multi)
        preview_frame = Gtk.Frame()
        preview_frame.set_child(self._preview)

        preview_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        preview_box.append(preview_frame)
        self._drag_hint = Gtk.Label(label="Drag monitors to reposition", visible=multi)
        self._drag_hint.add_css_class("dim-label")
        self._drag_hint.set_margin_top(4)
        preview_box.append(self._drag_hint)
        self._content_box.append(preview_box)

        self._gap_banner = Adw.Banner(
            title="Monitors have gaps between them — cursor won't be able to move across",
        )
        gap_frame = Gtk.Frame()
        gap_frame.add_css_class("gap-banner-frame")
        gap_frame.set_child(self._gap_banner)
        self._content_box.append(gap_frame)
        self._update_gap_warning()

        self._cards = []
        for idx, mon in enumerate(self._monitors):
            caps = get_monitor_capabilities(mon.name)
            others = [
                (m.name, f"{m.make} {m.model}".strip() or m.name)
                for m in self._monitors
                if m.name != mon.name
            ]
            card = MonitorCard(
                mon,
                index=idx + 1,
                on_changed=self._apply_change,
                on_discard=self._discard_monitor,
                on_remove=self._remove_monitor,
                caps=caps,  # type: ignore[arg-type]  # MonitorCapabilities is a TypedDict
                mirror_choices=others,
            )
            self._cards.append(card)
            self._content_box.append(card)

        for widget in self._window.build_schema_group_widgets("monitor_globals"):
            self._content_box.append(widget)

        self._update_card_states()

    # -- State updates --

    def _on_monitors_changed(self):
        """Single hook called after any monitor state change."""
        self._update_card_states()
        self._notify_dirty()
        if self._confirm:
            self._confirm.maybe_confirm()
        self._update_gap_warning()
        self._update_preview_draggable()

    def _update_gap_warning(self):
        if self._gap_banner is not None:
            self._gap_banner.set_revealed(not all_monitors_connected(self._monitors))

    def _update_preview_draggable(self):
        if self._preview is not None:
            active = [m for m in self._monitors if not m.disabled and not m.mirror_of]
            multi = len(active) > 1
            self._preview.set_draggable(multi)
            if self._drag_hint is not None:
                self._drag_hint.set_visible(multi)

    def _push_to_ui(self):
        """Sync all card widgets and preview canvas from the monitor list."""
        for idx, card in enumerate(self._cards):
            if idx < len(self._monitors):
                card.push_from_monitor(self._monitors[idx])
        if self._preview is not None:
            self._preview.queue_draw()

    def _update_card_states(self):
        saved_by_name = {m.name: m for m in self._saved_monitors}
        for card, mon in zip(self._cards, self._monitors, strict=True):
            is_managed = self._ownership.is_owned(mon.name)
            is_saved = self._ownership.is_saved(mon.name)
            baseline = saved_by_name.get(mon.name)
            # Auto-disown if all fields match baseline (change fully reverted)
            if is_managed and not is_saved and baseline is not None:
                if lines_from_monitors([mon]) == lines_from_monitors([baseline]):
                    self._ownership.disown(mon.name)
                    is_managed = False
            card.update_managed_state(baseline, is_managed, is_saved)

    def _notify_dirty(self):
        if self._on_dirty_changed:
            self._on_dirty_changed()

    # -- Applying changes --

    def _apply_change(self, mon: MonitorState, new_vals: dict):
        """Handle a widget change: update Monitor, adjust neighbors, commit."""
        if self._applying:
            return
        if all(getattr(mon, k) == v for k, v in new_vals.items()):
            return

        # Validate mirror target before applying
        if "mirror_of" in new_vals:
            error = validate_mirror(self._monitors, mon, new_vals["mirror_of"])
            if error:
                self._window.show_toast(error, timeout=3)
                return

        with self._undo_track():
            self._ownership.own(mon.name)
            self._applying = True
            try:
                old_w, old_h = mon.effective_size
                for k, v in new_vals.items():
                    setattr(mon, k, v)
                if "width" in new_vals or "height" in new_vals:
                    vs = compute_valid_scales(mon.width, mon.height)
                    si = nearest_scale_index(vs, mon.scale)
                    mon.scale = vs[si][0]
                if "disabled" not in new_vals and "mirror_of" not in new_vals:
                    adjust_neighbors(self._monitors, mon, old_w, old_h)
                # Disabling a monitor clears any monitors mirroring it
                if new_vals.get("disabled"):
                    for other in self._monitors:
                        if other.mirror_of == mon.name:
                            other.mirror_of = None
                            self._ownership.own(other.name)
                try:
                    self._window.hypr.monitors.apply(self._monitors)
                except HyprlandError as e:
                    self._window.show_toast(f"Monitor config failed — {e}", timeout=5)
                    return
                self._push_to_ui()
            finally:
                self._applying = False
        self._on_monitors_changed()
        self._schedule_resync()

    def _commit_to_hyprland(self):
        """Send all monitors to Hyprland, push to UI."""
        self._applying = True
        try:
            self._window.hypr.monitors.apply(self._monitors)
        except HyprlandError as e:
            self._applying = False
            self._window.show_toast(f"Monitor config failed — {e}", timeout=5)
            return
        try:
            self._push_to_ui()
        finally:
            self._applying = False
        self._on_monitors_changed()
        self._schedule_resync()

    def _discard_monitor(self, mon: MonitorState):
        """Revert a single monitor to its saved state."""
        saved_by_name = {m.name: m for m in self._saved_monitors}
        baseline = saved_by_name.get(mon.name)
        if baseline is None:
            self._remove_monitor(mon)
            return
        with self._undo_track():
            for field in self._RESTORABLE_FIELDS:
                setattr(mon, field, getattr(baseline, field))
            self._ownership.discard(mon.name)
            self._commit_to_hyprland()

    def _remove_monitor(self, mon: MonitorState):
        """Remove a monitor from HyprMod management."""
        with self._undo_track():
            self._ownership.disown(mon.name)
            self._apply_monitor_fallback(mon)
            self._commit_to_hyprland()

    def _apply_monitor_fallback(self, mon: MonitorState):
        """Revert a monitor to user-config values (excluding HyprMod).

        Parses the user's own monitor line and restores all fields:
        core geometry (resolution, position, scale, transform) and extras.
        """
        doc = self._window.hypr.document
        if doc is None:
            return
        excluded = frozenset({config.GUI_CONF.resolve()})
        user_lines = doc.find_all("monitor", exclude_sources=excluded)
        # Find the last matching line (Hyprland semantics)
        parts: list[str] = []
        for kw in user_lines:
            p = [s.strip() for s in kw.value.split(",")]
            if p and p[0] == mon.name:
                parts = p
        if len(parts) < 4:
            return
        # Core: NAME, RESxREFRESH, XxY, SCALE
        mode = parse_mode(parts[1])
        mon.width = mode["width"]
        mon.height = mode["height"]
        mon.refresh_rate = mode["refresh_rate"]
        try:
            px, py = parts[2].split("x")
            mon.x, mon.y = int(px), int(py)
        except (ValueError, IndexError):
            pass
        try:
            mon.scale = float(parts[3])
        except ValueError:
            pass
        # Transform + extras from tail key-value pairs
        mon.transform = 0
        tail = parts[4:]
        for i in range(0, len(tail) - 1, 2):
            if tail[i].lower() == "transform":
                try:
                    mon.transform = int(tail[i + 1])
                except ValueError:
                    pass
                break
        extras = parse_extras(",".join(parts))
        for field in _EXTRA_FIELDS:
            setattr(mon, field, extras.get(field))

    # -- IPC resync --

    def _schedule_resync(self):
        self._resync_timer.schedule(200, self._deferred_resync)

    def _deferred_resync(self):
        old_lines = lines_from_monitors(self._monitors)
        actual = self._window.hypr.monitors.get_all()
        if not actual:
            return GLib.SOURCE_REMOVE

        actual_by_name = {m.name: m for m in actual}
        for mon in self._monitors:
            if mon.disabled:
                continue
            real = actual_by_name.get(mon.name)
            if not real:
                continue
            mon.update_geometry_from_ipc(real)
            vs = compute_valid_scales(mon.width, mon.height)
            si = nearest_scale_index(vs, real.scale)
            mon.scale = vs[si][0]

        if lines_from_monitors(self._monitors) != old_lines:
            self._applying = True
            try:
                self._push_to_ui()
            finally:
                self._applying = False
            self._on_monitors_changed()
        return GLib.SOURCE_REMOVE

    # -- Preview drag --

    def _on_preview_drag_start(self):
        self._drag_undo_state = self._snap_undo_state()

    def _on_preview_drag(self, idx: int, x: int, y: int):
        if self._applying:
            return
        if self._confirm:
            self._confirm.cancel_debounce()
        self._last_dragged_idx = idx
        if 0 <= idx < len(self._cards):
            self._cards[idx].set_position_silent(x, y)

    def _on_preview_drag_end(self):
        idx = self._last_dragged_idx
        self._last_dragged_idx = -1
        if 0 <= idx < len(self._monitors):
            self._ownership.own(self._monitors[idx].name)
        self._commit_to_hyprland()
        if self._drag_undo_state is not None:
            self._push_undo_from(*self._drag_undo_state)
            self._drag_undo_state = None

    def _on_refresh(self, _button):
        self._resync_timer.cancel()
        self._reload_monitors()
        self._rebuild()
        self._on_monitors_changed()

    # -- Confirm/revert callbacks --

    def _on_confirmed(self):
        self._save_confirmed_snapshot()
        # Update UI state without re-triggering the confirm flow —
        # the page is still dirty (unsaved to disk) but the IPC change
        # has been accepted, so the banner should stay hidden.
        self._update_card_states()
        self._notify_dirty()
        self._update_gap_warning()

    def _revert_monitors(self):
        if not self._confirmed_monitors:
            return
        self._applying = True
        try:
            self._window.hypr.monitors.apply(self._confirmed_monitors)
        except HyprlandError as e:
            self._applying = False
            self._window.show_toast(f"Monitor revert failed — {e}", timeout=5)
            return
        self._monitors = copy.deepcopy(self._confirmed_monitors)
        self._snap_scales()
        self._applying = False
        self._rebuild()
        self._on_monitors_changed()
        self._schedule_resync()

    # -- Public interface --

    def confirm_changes(self):
        """Accept the current monitor configuration (e.g. when navigating away)."""
        if self._confirm:
            self._confirm.confirm()

    def _save_snapshot(self):
        self._saved_monitors = copy.deepcopy(self._monitors)

    def _save_confirmed_snapshot(self):
        self._confirmed_monitors = copy.deepcopy(self._monitors)

    def is_dirty(self) -> bool:
        managed = [m for m in self._monitors if self._ownership.is_owned(m.name)]
        saved = [m for m in self._saved_monitors if self._ownership.is_saved(m.name)]
        return sorted(lines_from_monitors(managed)) != sorted(lines_from_monitors(saved))

    def dirty_count(self) -> int:
        """Count individual monitors with unsaved changes."""
        saved_by_name = {m.name: m for m in self._saved_monitors}
        count = 0
        for mon in self._monitors:
            is_owned = self._ownership.is_owned(mon.name)
            was_saved = self._ownership.is_saved(mon.name)
            if is_owned != was_saved:
                count += 1
                continue
            if not is_owned:
                continue
            baseline = saved_by_name.get(mon.name)
            if baseline is None:
                count += 1
            elif lines_from_monitors([mon]) != lines_from_monitors([baseline]):
                count += 1
        return count

    def mark_saved(self):
        self._ownership.mark_saved()
        self._save_snapshot()
        self._save_confirmed_snapshot()
        if self._confirm:
            self._confirm.cancel()
        if self._content_box is not None:
            self._update_card_states()

    def discard(self):
        if not self._saved_monitors or not self.is_dirty():
            return
        self._ownership.discard_all()
        self._applying = True
        try:
            self._window.hypr.monitors.apply(self._saved_monitors)
        except HyprlandError as e:
            self._window.show_toast(f"Monitor discard failed — {e}", timeout=5)
            return
        finally:
            self._applying = False
        self._monitors = copy.deepcopy(self._saved_monitors)
        self._snap_scales()
        self._save_confirmed_snapshot()
        self._rebuild()
        self._on_monitors_changed()
        self._schedule_resync()

    def reload_from_saved(self):
        """Re-read ownership from the config file and rebuild.

        Called after profile activation — the config file has changed but
        Hyprland may not fire a monitor change event if only ownership differs.
        """
        self._resync_timer.cancel()
        self._reload_monitors()
        self._save_snapshot()
        self._save_confirmed_snapshot()
        if self._content_box is not None:
            self._rebuild()

    def get_search_entries(self) -> list[dict]:
        """Collect searchable fields from all monitor cards (deduplicated)."""
        seen = set()
        entries = []
        for card in self._cards:
            for title, description in card.searchable_fields:
                if title in seen:
                    continue
                seen.add(title)
                entries.append(
                    {
                        "key": title.lower().replace(" ", "_"),
                        "label": title,
                        "description": description,
                        "_group_id": "monitors",
                        "_group_label": "Monitors",
                        "_section_label": "",
                    }
                )
        return entries

    def get_monitor_lines(self) -> list[str]:
        managed = [m for m in self._monitors if self._ownership.is_owned(m.name)]
        return [f"monitor = {line}" for line in lines_from_monitors(managed)]
