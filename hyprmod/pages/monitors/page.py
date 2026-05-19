"""Monitor management page — orchestrates cards, preview, and Hyprland IPC."""

import copy
from collections.abc import Iterator

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
    resolve_identifier,
    validate_mirror,
)
from hyprland_socket import HyprlandError

from hyprmod.core import config
from hyprmod.core.ownership import OwnershipSet
from hyprmod.core.pending import PendingChange
from hyprmod.core.undo import MonitorsUndoEntry
from hyprmod.pages.monitors.card import MonitorCard
from hyprmod.pages.monitors.confirm_controller import ConfirmController
from hyprmod.pages.section import SectionPage
from hyprmod.ui import clear_children, make_page_layout, try_with_toast
from hyprmod.ui.empty_state import EmptyState
from hyprmod.ui.icons import MONITORS_ICON
from hyprmod.ui.monitor_preview import MonitorLayoutPreview
from hyprmod.ui.timer import Timer

# Fields cleared on managed monitors that aren't present in the saved config.
# Kept aligned with hyprland-monitors's monitor-line serialization.
_EXTRA_FIELDS = (
    "bit_depth",
    "vrr",
    "color_management",
    "sdr_brightness",
    "sdr_saturation",
    "sdr_min_luminance",
    "sdr_max_luminance",
    "min_luminance",
    "max_luminance",
    "max_avg_luminance",
    "mirror_of",
)


class MonitorsPage(SectionPage):
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
        "sdr_brightness",
        "sdr_saturation",
        "sdr_min_luminance",
        "sdr_max_luminance",
        "min_luminance",
        "max_luminance",
        "max_avg_luminance",
        "mirror_of",
        "disabled",
        "identify_by_description",
    )

    def __init__(self, window, on_dirty_changed=None, push_undo=None, saved_sections=None):
        super().__init__(window, on_dirty_changed, push_undo)
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

    def _capture_undo(self):
        """Snapshot current monitors + ownership for undo."""
        return copy.deepcopy(self._monitors), self._ownership.snapshot()

    def _undo_key(self):
        return self._monitors_key()

    def _build_undo_entry(self, old, new):
        old_monitors, old_owned = old
        new_monitors, new_owned = new
        return MonitorsUndoEntry(
            old_monitors=old_monitors,
            new_monitors=new_monitors,
            old_owned=old_owned,
            new_owned=new_owned,
        )

    def _push_undo_from(self, old):
        """Push a MonitorsUndoEntry given a captured 'before' snapshot.

        Used by the preview drag path, where the snapshot is captured at
        drag-start (rather than via ``_undo_track``).
        """
        if self._push_undo is None:
            return
        self._push_undo(self._build_undo_entry(old, self._capture_undo()))

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
        sections = saved_sections if saved_sections is not None else self._window.saved_sections
        saved = config.collect_section(sections, config.KEYWORD_MONITOR)
        if saved:
            merge_saved_state(self._monitors, saved)
        self._ownership = OwnershipSet(self._managed_names_from_lines(saved))
        # Since monitor= is all-or-nothing, our line replaces the user's
        # entire line.  IPC may still report extras from the user's config
        # that our line doesn't include — clear them so the UI shows what
        # our config actually sets.
        self._clear_unmanaged_extras(saved)

    def _resolve_line_to_port(self, line: str) -> str | None:
        """Resolve a ``monitor = ...`` config line's identifier to a live connector name.

        Handles both port-name (``DP-1``) and description (``desc:Acme Pixel``) forms.
        Returns ``None`` when no currently-connected monitor matches.
        """
        cleaned = line.removeprefix(config.KEYWORD_MONITOR).strip().removeprefix("=").strip()
        token = cleaned.split(",")[0].strip()
        if not token:
            return None
        mon = resolve_identifier(token, self._monitors)
        return mon.name if mon else None

    def _clear_unmanaged_extras(self, saved_lines: list[str]):
        """Clear IPC-leaked extras on managed monitors not in our config."""
        saved_extras: dict[str, set[str]] = {}
        for line in saved_lines:
            port = self._resolve_line_to_port(line)
            if port is None:
                continue
            saved_extras[port] = set(parse_extras(line).keys())
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

    def _managed_names_from_lines(self, saved_lines: list[str]) -> set[str]:
        ports = {self._resolve_line_to_port(line) for line in saved_lines}
        ports.discard(None)
        return ports  # type: ignore[return-value]

    def _description_is_unique(self, mon: MonitorState) -> bool:
        """Check if mon's truncated description is unique among connected monitors.

        ``identify_by_description`` only works when no other connected monitor
        starts with the same description prefix — otherwise Hyprland's first-match
        rule could route the config to the wrong monitor.
        """
        if not mon.description:
            return False
        prefix = mon.description.split(",", 1)[0].strip()
        if not prefix:
            return False
        for other in self._monitors:
            if other.name == mon.name:
                continue
            if other.description.startswith(prefix):
                return False
        return True

    # -- UI building --

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic")
        refresh_btn.set_tooltip_text("Refresh monitors")
        refresh_btn.connect("clicked", self._on_refresh)
        page_header.pack_start(refresh_btn)

        toolbar_view, page_box, self._content_box, scrolled = make_page_layout(header=page_header)

        confirm_banner = Adw.Banner(title="")
        self._confirm = ConfirmController(
            confirm_banner,
            is_dirty=self.is_dirty,
            on_revert=self._revert_monitors,
            on_confirmed=self._on_confirmed,
        )
        # Overlay rather than dock — a docked banner shifts every card down
        # on reveal/hide during the countdown.
        confirm_banner.set_valign(Gtk.Align.START)
        overlay = Gtk.Overlay()
        page_box.remove(scrolled)
        overlay.set_child(scrolled)
        overlay.add_overlay(confirm_banner)
        page_box.append(overlay)

        self._rebuild()
        return toolbar_view

    def _rebuild(self):
        if self._content_box is None:
            return
        clear_children(self._content_box)

        if not self._monitors:
            self._content_box.append(
                EmptyState(
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
                desc_unique=self._description_is_unique(mon),
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
        # Arm the confirm controller before notifying the window so the
        # auto-save gate (window._schedule_auto_save) sees is_confirm_pending
        # as True and defers the write until the user keeps or reverts.
        if self._confirm:
            self._confirm.maybe_confirm()
        self._notify_dirty()
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
            # Rebinds the list reference too — callers that replace
            # self._monitors (e.g. revert) don't need to do it themselves.
            self._preview.set_monitors(self._monitors)

    def _update_card_states(self):
        if not self._cards:
            return
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
                if not try_with_toast(
                    self._window.show_toast,
                    "Monitor config failed",
                    lambda: self._window.hypr.monitors.apply(self._monitors),
                    catch=HyprlandError,
                ):
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
        excluded = frozenset({config.managed_path().resolve()})
        user_lines = doc.find_all(config.KEYWORD_MONITOR, exclude_sources=excluded)
        # Find the last matching line (Hyprland semantics).
        # A line refers to ``mon`` if its leading token resolves to it — handles
        # both port-name (``DP-1``) and ``desc:`` forms.
        parts: list[str] = []
        for kw in user_lines:
            p = [s.strip() for s in kw.value.split(",")]
            if p and resolve_identifier(p[0], [mon]) is mon:
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
            # MonitorState and Monitor share the geometry fields used here;
            # update_geometry_from_ipc declares Monitor but tolerates either.
            mon.update_geometry_from_ipc(real)  # type: ignore[arg-type]
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
        self._drag_undo_state = self._capture_undo()

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
            self._push_undo_from(self._drag_undo_state)
            self._drag_undo_state = None

    def _on_refresh(self, _button):
        self._resync_timer.cancel()
        self._reload_monitors()
        # Re-snapshot so dirty tracking compares against the freshly-read disk state.
        # Without this, a monitor that moved to a different port resolves to a new
        # connector name in ownership, but the old snapshot still has the old name —
        # leading to a phantom "pending changes" indicator.
        self._save_snapshot()
        self._save_confirmed_snapshot()
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
        # Update in place — a full rebuild would collapse the Advanced expander.
        try:
            self._push_to_ui()
        finally:
            self._applying = False
        self._on_monitors_changed()
        self._schedule_resync()

    # -- Public interface --

    def confirm_changes(self):
        """Accept the current monitor configuration (e.g. when navigating away)."""
        if self._confirm:
            self._confirm.confirm()

    def is_confirm_pending(self) -> bool:
        # True while a monitor change is mid-confirm (debounce or 15s
        # countdown). Auto-save defers writes during this window —
        # otherwise a broken config would land on disk before the user
        # has a chance to keep or revert, and mark_saved() would cancel
        # the auto-revert.
        return self._confirm is not None and self._confirm.is_pending

    def _save_snapshot(self):
        self._saved_monitors = copy.deepcopy(self._monitors)

    def _save_confirmed_snapshot(self):
        self._confirmed_monitors = copy.deepcopy(self._monitors)

    def is_dirty(self) -> bool:
        managed = [m for m in self._monitors if self._ownership.is_owned(m.name)]
        saved = [m for m in self._saved_monitors if self._ownership.is_saved(m.name)]
        return sorted(lines_from_monitors(managed)) != sorted(lines_from_monitors(saved))

    def iter_pending_changes(self) -> Iterator[PendingChange]:
        if not self.is_dirty():
            return
        saved_by_name = {m.name: m for m in self._saved_monitors}
        for mon in self._monitors:
            is_owned = self._ownership.is_owned(mon.name)
            was_saved = self._ownership.is_saved(mon.name)
            baseline = saved_by_name.get(mon.name)
            if is_owned and not was_saved:
                kind, subtitle = "added", self._summarize_monitor(mon)
            elif was_saved and not is_owned:
                kind, subtitle = "removed", "remove from managed config"
            elif (
                is_owned
                and baseline is not None
                and lines_from_monitors([mon]) != lines_from_monitors([baseline])
            ):
                kind, subtitle = "modified", self._diff_monitor(baseline, mon)
            else:
                continue
            yield PendingChange(
                category="Monitors",
                title=self._monitor_label(mon),
                subtitle=subtitle,
                kind=kind,
                revert=lambda m=mon: self._discard_monitor(m),
                navigate_to="monitors",
                icon=MONITORS_ICON,
            )

    @staticmethod
    def _monitor_label(mon: MonitorState) -> str:
        desc = f"{mon.make} {mon.model}".strip()
        return f"{mon.name} — {desc}" if desc else mon.name

    @staticmethod
    def _summarize_monitor(mon: MonitorState) -> str:
        if mon.disabled:
            return "disabled"
        return (
            f"{mon.width}x{mon.height}@{mon.refresh_rate:.0f}Hz · "
            f"{mon.x},{mon.y} · scale {mon.scale:g}"
        )

    @staticmethod
    def _diff_monitor(baseline: MonitorState, current: MonitorState) -> str:
        diffs: list[str] = []
        if baseline.disabled != current.disabled:
            diffs.append("disabled" if current.disabled else "re-enabled")
        if (baseline.width, baseline.height) != (current.width, current.height):
            diffs.append(f"{baseline.width}x{baseline.height} → {current.width}x{current.height}")
        if abs(baseline.refresh_rate - current.refresh_rate) > 0.01:
            diffs.append(f"{baseline.refresh_rate:.0f}Hz → {current.refresh_rate:.0f}Hz")
        if (baseline.x, baseline.y) != (current.x, current.y):
            diffs.append(f"pos {baseline.x},{baseline.y} → {current.x},{current.y}")
        if abs(baseline.scale - current.scale) > 1e-6:
            diffs.append(f"scale {baseline.scale:g} → {current.scale:g}")
        if baseline.transform != current.transform:
            diffs.append(f"rotate {baseline.transform} → {current.transform}")
        if (baseline.mirror_of or "") != (current.mirror_of or ""):
            diffs.append(f"mirror {baseline.mirror_of or '—'} → {current.mirror_of or '—'}")
        return " · ".join(diffs) if diffs else "updated"

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
