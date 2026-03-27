"""Interactive monitor layout preview — drag monitors to reposition them."""

from gi.repository import Gtk
from hyprland_monitors import MonitorState

from hyprmod.ui import ACCENT_RGB, get_cursor_grab


class MonitorLayoutPreview(Gtk.DrawingArea):
    """Interactive layout preview — drag monitors to reposition them."""

    def __init__(self, on_position_changed=None, on_drag_started=None, on_drag_ended=None):
        super().__init__()
        self._monitors: list[MonitorState] = []
        self._on_position_changed = on_position_changed
        self._on_drag_started = on_drag_started
        self._on_drag_ended = on_drag_ended
        self._draggable = True
        self._dragging_idx = -1
        self._drag_moved = False
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_start_mon_x = 0
        self._drag_start_mon_y = 0

        # Cached layout transform for hit testing
        self._layout_scale = 1.0
        self._layout_ox = 0.0
        self._layout_oy = 0.0
        self._layout_min_x = 0.0
        self._layout_min_y = 0.0

        self.set_content_height(180)
        self.set_draw_func(self._draw)

        self._drag = Gtk.GestureDrag.new()
        self._drag.connect("drag-begin", self._on_drag_begin)
        self._drag.connect("drag-update", self._on_drag_update)
        self._drag.connect("drag-end", self._on_drag_end)
        self.add_controller(self._drag)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    def set_monitors(self, monitors: list[MonitorState]):
        self._monitors = monitors
        self.queue_draw()

    def set_draggable(self, draggable: bool):
        self._draggable = draggable
        phase = Gtk.PropagationPhase.BUBBLE if draggable else Gtk.PropagationPhase.NONE
        self._drag.set_propagation_phase(phase)
        if not draggable:
            self.set_cursor(None)

    def _hit_test(self, cx, cy) -> int:
        """Return monitor index at canvas position, or -1 (skips disabled)."""
        s = self._layout_scale
        if s == 0:
            return -1
        for idx, mon in reversed(list(enumerate(self._monitors))):
            if mon.disabled or mon.mirror_of:
                continue
            ew, eh = mon.effective_size
            mx = self._layout_ox + (mon.x - self._layout_min_x) * s
            my = self._layout_oy + (mon.y - self._layout_min_y) * s
            mw, mh = ew * s, eh * s
            if mx <= cx <= mx + mw and my <= cy <= my + mh:
                return idx
        return -1

    def _on_drag_begin(self, gesture, start_x, start_y):
        idx = self._hit_test(start_x, start_y)
        if idx >= 0:
            self._dragging_idx = idx
            self._drag_moved = False
            if self._on_drag_started:
                self._on_drag_started()
            self._drag_start_x = start_x
            self._drag_start_y = start_y
            self._drag_start_mon_x = self._monitors[idx].x
            self._drag_start_mon_y = self._monitors[idx].y
        else:
            self._dragging_idx = -1

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self._dragging_idx < 0:
            return
        s = self._layout_scale
        if s == 0:
            return
        dx = round(offset_x / s)
        dy = round(offset_y / s)
        new_x = round((self._drag_start_mon_x + dx) / 10) * 10
        new_y = round((self._drag_start_mon_y + dy) / 10) * 10

        new_x, new_y = self._resolve_collisions(self._dragging_idx, new_x, new_y)
        new_x, new_y = self._clamp_to_neighbors(self._dragging_idx, new_x, new_y)

        mon = self._monitors[self._dragging_idx]
        if mon.x != new_x or mon.y != new_y:
            mon.x = new_x
            mon.y = new_y
            self._drag_moved = True
            self.queue_draw()
            if self._on_position_changed:
                self._on_position_changed(self._dragging_idx, new_x, new_y)

    def _resolve_collisions(self, drag_idx: int, x: int, y: int) -> tuple[int, int]:
        """Push the dragged monitor out of any overlapping monitors.

        Uses the drag-start position to determine the *approach axis*
        (horizontal, vertical, or both for corners), then on each valid
        axis uses a center-comparison to decide the push direction.
        This gives symmetric crossing thresholds (same "pressure" in
        both directions) while preventing cross-axis oscillation.
        """
        dragged = self._monitors[drag_idx]
        dw, dh = dragged.effective_size
        sx, sy = self._drag_start_mon_x, self._drag_start_mon_y

        for i, other in enumerate(self._monitors):
            if i == drag_idx or other.mirror_of:
                continue
            ow, oh = other.effective_size
            ox, oy = other.x, other.y

            # AABB overlap check
            if not (x < ox + ow and x + dw > ox and y < oy + oh and y + dh > oy):
                continue

            # Which axes were separated at drag start?
            h_sep = (sx + dw <= ox) or (sx >= ox + ow)
            v_sep = (sy + dh <= oy) or (sy >= oy + oh)

            # On each separated axis, use center-vs-center to pick the
            # push side.  Each candidate: (new_x, new_y, push_distance).
            candidates = []

            if h_sep:
                if x + dw / 2 < ox + ow / 2:
                    candidates.append((ox - dw, y, (x + dw) - ox))
                else:
                    candidates.append((ox + ow, y, (ox + ow) - x))

            if v_sep:
                if y + dh / 2 < oy + oh / 2:
                    candidates.append((x, oy - dh, (y + dh) - oy))
                else:
                    candidates.append((x, oy + oh, (oy + oh) - y))

            if not candidates:
                # Started already overlapping – fall back to center
                # comparison, preferring the axis with more relative
                # displacement.
                if abs(x + dw / 2 - ox - ow / 2) * (dh + oh) >= abs(y + dh / 2 - oy - oh / 2) * (
                    dw + ow
                ):
                    if x + dw / 2 < ox + ow / 2:
                        x = ox - dw
                    else:
                        x = ox + ow
                else:
                    if y + dh / 2 < oy + oh / 2:
                        y = oy - dh
                    else:
                        y = oy + oh
                continue

            best = min(candidates, key=lambda c: c[2])
            x, y = best[0], best[1]

        return x, y

    _MAX_EXTENT_FACTOR = 3

    def _clamp_to_neighbors(self, drag_idx: int, x: int, y: int) -> tuple[int, int]:
        """Clamp so the total bounding box never exceeds a factor of
        the combined monitor content, keeping monitors visible."""
        dragged = self._monitors[drag_idx]
        dw, dh = dragged.effective_size

        active = [
            (i, m) for i, m in enumerate(self._monitors) if not m.disabled and not m.mirror_of
        ]
        if len(active) < 2:
            return x, y

        # Total content span: sum of all monitor sizes on each axis.
        content_w = sum(m.effective_size[0] for _, m in active)
        content_h = sum(m.effective_size[1] for _, m in active)
        max_w = content_w * self._MAX_EXTENT_FACTOR
        max_h = content_h * self._MAX_EXTENT_FACTOR

        # Bounding box of all OTHER active monitors.
        others = [(i, m) for i, m in active if i != drag_idx]
        min_ox = min(m.x for _, m in others)
        min_oy = min(m.y for _, m in others)
        max_ox = max(m.x + m.effective_size[0] for _, m in others)
        max_oy = max(m.y + m.effective_size[1] for _, m in others)

        # Clamp so the combined bbox doesn't exceed the max extent.
        x = max(x, min(min_ox, max_ox - max_w))
        x = min(x, max(max_ox, min_ox + max_w) - dw)
        y = max(y, min(min_oy, max_oy - max_h))
        y = min(y, max(max_oy, min_oy + max_h) - dh)

        return x, y

    def _on_drag_end(self, gesture, offset_x, offset_y):
        moved = self._dragging_idx >= 0 and self._drag_moved
        self._dragging_idx = -1
        if moved and self._on_drag_ended:
            self._on_drag_ended()

    def _on_motion(self, controller, x, y):
        if not self._draggable:
            return
        idx = self._hit_test(x, y)
        if idx >= 0:
            self.set_cursor(get_cursor_grab())
        else:
            self.set_cursor(None)

    def _draw(self, area, cr, width, height):
        if not self._monitors:
            return

        color = self.get_color()
        r, g, b = color.red, color.green, color.blue
        accent_r, accent_g, accent_b = ACCENT_RGB

        # Exclude mirrored monitors from bounding box (they overlay their source)
        independent = [m for m in self._monitors if not m.mirror_of]
        if not independent:
            return

        min_x = min(m.x for m in independent)
        min_y = min(m.y for m in independent)
        max_x = max(m.x + m.effective_size[0] for m in independent)
        max_y = max(m.y + m.effective_size[1] for m in independent)

        total_w = max_x - min_x
        total_h = max_y - min_y
        if total_w == 0 or total_h == 0:
            return

        pad = 16
        scale_x = (width - 2 * pad) / total_w
        scale_y = (height - 2 * pad) / total_h
        scale = min(scale_x, scale_y)

        drawn_w = total_w * scale
        drawn_h = total_h * scale
        ox = (width - drawn_w) / 2
        oy = (height - drawn_h) / 2

        self._layout_scale = scale
        self._layout_ox = ox
        self._layout_oy = oy
        self._layout_min_x = min_x
        self._layout_min_y = min_y

        # Build lookup: source name -> list of mirroring monitor indices
        mirror_indices: dict[str, list[int]] = {}
        for idx, mon in enumerate(self._monitors):
            if mon.mirror_of:
                mirror_indices.setdefault(mon.mirror_of, []).append(idx)

        for idx, mon in enumerate(self._monitors):
            if mon.mirror_of:
                continue  # drawn as badge on source below

            ew, eh = mon.effective_size
            mx = ox + (mon.x - min_x) * scale
            my = oy + (mon.y - min_y) * scale
            mw = ew * scale
            mh = eh * scale
            focused = mon.focused
            disabled = mon.disabled

            if disabled:
                cr.set_source_rgba(r, g, b, 0.03)
            elif focused:
                cr.set_source_rgba(accent_r, accent_g, accent_b, 0.15)
            else:
                cr.set_source_rgba(r, g, b, 0.08)
            cr.rectangle(mx, my, mw, mh)
            cr.fill()

            if disabled:
                cr.set_source_rgba(r, g, b, 0.15)
                cr.set_dash([4.0, 4.0], 0)
            elif focused:
                cr.set_source_rgba(accent_r, accent_g, accent_b, 0.7)
            else:
                cr.set_source_rgba(r, g, b, 0.3)
            cr.set_line_width(1.5)
            cr.rectangle(mx, my, mw, mh)
            cr.stroke()
            if disabled:
                cr.set_dash([], 0)

            number = str(idx + 1)
            cr.set_font_size(min(mw, mh) * 0.35)
            if disabled:
                cr.set_source_rgba(r, g, b, 0.1)
            elif focused:
                cr.set_source_rgba(accent_r, accent_g, accent_b, 0.6)
            else:
                cr.set_source_rgba(r, g, b, 0.25)
            extents = cr.text_extents(number)
            cr.move_to(
                mx + (mw - extents.width) / 2 - extents.x_bearing,
                my + (mh - extents.height) / 2 - extents.y_bearing,
            )
            cr.show_text(number)

            cr.set_font_size(10)
            cr.set_source_rgba(r, g, b, 0.3 if disabled else 0.6)
            extents = cr.text_extents(mon.name)
            cr.move_to(mx + (mw - extents.width) / 2 - extents.x_bearing, my + mh - 6)
            cr.show_text(mon.name)

            # Draw mirror badges in the top-right corner
            mirroring = mirror_indices.get(mon.name, [])
            for badge_i, midx in enumerate(mirroring):
                badge_text = str(midx + 1)
                badge_size = min(mw, mh) * 0.22
                cr.set_font_size(badge_size)
                te = cr.text_extents(badge_text)
                pad_x, pad_y = 4, 2
                bw = te.width + pad_x * 2
                bh = te.height + pad_y * 2
                bx = mx + mw - bw - 4 - badge_i * (bw + 3)
                by = my + 4
                # Badge background
                cr.set_source_rgba(accent_r, accent_g, accent_b, 0.25)
                cr.rectangle(bx, by, bw, bh)
                cr.fill()
                # Badge border
                cr.set_source_rgba(accent_r, accent_g, accent_b, 0.6)
                cr.set_line_width(1.0)
                cr.rectangle(bx, by, bw, bh)
                cr.stroke()
                # Badge text
                cr.set_source_rgba(accent_r, accent_g, accent_b, 0.8)
                cr.move_to(bx + pad_x - te.x_bearing, by + pad_y - te.y_bearing)
                cr.show_text(badge_text)
