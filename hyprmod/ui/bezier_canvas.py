"""Interactive bezier curve canvas and animation preview widgets.

BezierCanvas renders a cubic bezier curve on a Cairo canvas with two draggable
control points. AnimationPreview shows a dot moving along a track with the
current easing applied.
"""

import math

from gi.repository import GLib, Gtk

from hyprmod.data.bezier_presets import cubic_bezier, ease
from hyprmod.ui import ACCENT_RGB, ACTIVE_RGB, get_cursor_grab, get_cursor_none

HANDLE_RADIUS = 8
CANVAS_PAD = 16  # minimum padding around the visible area


class BezierCanvas(Gtk.DrawingArea):
    """Interactive bezier curve canvas with draggable control points."""

    def __init__(self, on_change=None, on_drag_end=None):
        super().__init__()

        self.x1, self.y1 = 0.25, 0.1
        self.x2, self.y2 = 0.25, 1.0

        self._dragging = None  # "p1" or "p2" or None
        self._on_change = on_change
        self._drag_end_cb = on_drag_end
        self._drag_scale = 1.0
        self._drag_origin_bx = 0.0
        self._drag_origin_by = 0.0
        self._view_y_lo = -0.1
        self._view_y_hi = 1.1

        self.set_content_width(300)
        self.set_content_height(300)
        self.set_draw_func(self._draw)

        # Drag gesture
        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        # Motion for cursor changes
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        self.add_controller(motion)

    @property
    def is_dragging(self) -> bool:
        return self._dragging is not None

    def set_points(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self._update_view_range(expand_only=False)
        self.queue_draw()

    def _notify_change(self):
        if self._on_change:
            self._on_change(self.x1, self.y1, self.x2, self.y2)

    def _notify_drag_end(self):
        if self._drag_end_cb:
            self._drag_end_cb()

    def _ideal_y_range(self):
        """Ideal visible Y range — [0,1] plus control points with margin."""
        margin = 0.1
        y_lo = min(0.0, self.y1, self.y2) - margin
        y_hi = max(1.0, self.y1, self.y2) + margin
        return y_lo, y_hi

    def _update_view_range(self, expand_only=False):
        """Update the viewport Y range.

        expand_only=True: only expand the range, never shrink (prevents jitter).
        expand_only=False (default): snap to ideal range.
        """
        y_lo, y_hi = self._ideal_y_range()
        if expand_only:
            self._view_y_lo = min(self._view_y_lo, y_lo)
            self._view_y_hi = max(self._view_y_hi, y_hi)
        else:
            self._view_y_lo = y_lo
            self._view_y_hi = y_hi

    def _visible_range(self):
        return self._view_y_lo, self._view_y_hi

    def _grid_metrics(self):
        """Return (scale, x_off, y_off, y_lo, y_hi).

        scale  — pixels per bezier unit (same for X and Y to keep square grid)
        x_off  — pixel X of bezier x=0
        y_off  — pixel Y of bezier y=y_hi (top of visible range)
        """
        w = self.get_width()
        h = self.get_height()
        y_lo, y_hi = self._visible_range()
        y_span = y_hi - y_lo

        pad = CANVAS_PAD + HANDLE_RADIUS
        scale_x = (w - 2 * pad) / 1.0  # X always spans [0, 1]
        scale_y = (h - 2 * pad) / y_span
        scale = min(scale_x, scale_y)

        # Centre the content
        drawn_w = 1.0 * scale
        drawn_h = y_span * scale
        x_off = (w - drawn_w) / 2
        y_off = (h - drawn_h) / 2

        return scale, x_off, y_off, y_lo, y_hi

    def _to_canvas(self, bx, by):
        """Convert bezier coords to canvas pixel coords."""
        scale, x_off, y_off, y_lo, y_hi = self._grid_metrics()
        cx = x_off + bx * scale
        cy = y_off + (y_hi - by) * scale  # Y is flipped
        return cx, cy

    def _from_canvas(self, cx, cy):
        """Convert canvas pixel coords to bezier coords."""
        scale, x_off, y_off, y_lo, y_hi = self._grid_metrics()
        bx = (cx - x_off) / scale
        by = y_hi - (cy - y_off) / scale
        return bx, by

    def _hit_test(self, cx, cy):
        """Check if (cx, cy) is near a control point. Returns 'p1', 'p2', or None."""
        p1x, p1y = self._to_canvas(self.x1, self.y1)
        p2x, p2y = self._to_canvas(self.x2, self.y2)

        if (cx - p1x) ** 2 + (cy - p1y) ** 2 <= (HANDLE_RADIUS + 4) ** 2:
            return "p1"
        if (cx - p2x) ** 2 + (cy - p2y) ** 2 <= (HANDLE_RADIUS + 4) ** 2:
            return "p2"
        return None

    def _on_drag_begin(self, gesture, start_x, start_y):
        hit = self._hit_test(start_x, start_y)
        if hit:
            self._dragging = hit
            # Lock scale at drag start so pixel-to-bezier ratio stays constant
            scale, *_ = self._grid_metrics()
            self._drag_scale = scale
            if hit == "p1":
                self._drag_origin_bx = self.x1
                self._drag_origin_by = self.y1
            else:
                self._drag_origin_bx = self.x2
                self._drag_origin_by = self.y2
            # Hide cursor so it doesn't drift from the handle during rescale
            self.set_cursor(get_cursor_none())
        else:
            self._dragging = None

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if not self._dragging:
            return
        # Convert pixel offset using the locked scale
        bx = self._drag_origin_bx + offset_x / self._drag_scale
        by = self._drag_origin_by - offset_y / self._drag_scale
        bx = max(0.0, min(1.0, round(bx, 3)))
        by = round(by, 3)

        if self._dragging == "p1":
            self.x1, self.y1 = bx, by
        else:
            self.x2, self.y2 = bx, by

        self._update_view_range(expand_only=False)
        self.queue_draw()
        self._notify_change()

    def _on_drag_end(self, gesture, offset_x, offset_y):
        self._dragging = None
        self.set_cursor(None)
        self.queue_draw()
        self._notify_drag_end()

    def _on_motion(self, controller, x, y):
        if self._dragging:
            return
        hit = self._hit_test(x, y)
        if hit:
            self.set_cursor(get_cursor_grab())
        else:
            self.set_cursor(None)

    def _draw(self, area, cr, width, height):
        scale, x_off, y_off, y_lo, y_hi = self._grid_metrics()

        # Grid box corners in pixel coords (bezier [0,1]x[0,1])
        gx0, gy0 = self._to_canvas(0, 0)  # bottom-left
        gx1, gy1 = self._to_canvas(1, 1)  # top-right
        grid_w = gx1 - gx0
        grid_h = gy0 - gy1  # gy0 > gy1 because Y is flipped

        # Get theme colors from the widget's style context
        color = self.get_color()
        r, g, b = color.red, color.green, color.blue

        # Background grid
        cr.set_source_rgba(r, g, b, 0.08)
        cr.set_line_width(0.5)
        for i in range(11):
            frac = i / 10
            x = gx0 + frac * grid_w
            y = gy1 + frac * grid_h
            cr.move_to(x, gy1)
            cr.line_to(x, gy0)
            cr.move_to(gx0, y)
            cr.line_to(gx1, y)
        cr.stroke()

        # Border box
        cr.set_source_rgba(r, g, b, 0.25)
        cr.set_line_width(1)
        cr.rectangle(gx0, gy1, grid_w, grid_h)
        cr.stroke()

        # Diagonal (linear reference)
        cr.set_source_rgba(r, g, b, 0.15)
        cr.set_line_width(1)
        cr.set_dash([4, 4])
        p0 = self._to_canvas(0, 0)
        p3 = self._to_canvas(1, 1)
        cr.move_to(*p0)
        cr.line_to(*p3)
        cr.stroke()
        cr.set_dash([])

        # Control point handles (lines from endpoints to control points)
        cr.set_source_rgba(r, g, b, 0.35)
        cr.set_line_width(1.5)
        p1 = self._to_canvas(self.x1, self.y1)
        p2 = self._to_canvas(self.x2, self.y2)
        cr.move_to(*p0)
        cr.line_to(*p1)
        cr.stroke()
        cr.move_to(*p3)
        cr.line_to(*p2)
        cr.stroke()

        # The bezier curve itself
        # Get accent color — use a vivid blue as fallback
        accent_r, accent_g, accent_b = ACCENT_RGB

        cr.set_source_rgba(accent_r, accent_g, accent_b, 1.0)
        cr.set_line_width(2.5)
        cr.move_to(*p0)
        steps = 80
        for i in range(1, steps + 1):
            t = i / steps
            bx = cubic_bezier(t, self.x1, self.x2)
            by = cubic_bezier(t, self.y1, self.y2)
            cx, cy = self._to_canvas(bx, by)
            cr.line_to(cx, cy)
        cr.stroke()

        # Control point circles
        active_r, active_g, active_b = ACTIVE_RGB
        for point_id, (px, py) in [("p1", p1), ("p2", p2)]:
            if self._dragging == point_id:
                cr0, cg0, cb0 = active_r, active_g, active_b
            else:
                cr0, cg0, cb0 = accent_r, accent_g, accent_b
            cr.set_source_rgba(cr0, cg0, cb0, 1.0)
            cr.arc(px, py, HANDLE_RADIUS, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(1, 1, 1, 0.9)
            cr.arc(px, py, HANDLE_RADIUS - 2, 0, 2 * math.pi)
            cr.fill()
            cr.set_source_rgba(cr0, cg0, cb0, 1.0)
            cr.arc(px, py, HANDLE_RADIUS - 4, 0, 2 * math.pi)
            cr.fill()


class AnimationPreview(Gtk.DrawingArea):
    """A small strip showing a dot moving with the current bezier easing."""

    SPEED = 0.012  # progress per ~16ms frame
    PAUSE_DURATION_US = 1_000_000  # 1 second pause at end

    def __init__(self):
        super().__init__()
        self.x1, self.y1 = 0.25, 0.1
        self.x2, self.y2 = 0.25, 1.0
        self._progress = 0.0
        self._pause_until = 0  # frame clock timestamp to resume after
        self._tick_id = 0

        self._ease_min = 0.0
        self._ease_max = 1.0
        self.set_content_height(40)
        self.set_draw_func(self._draw)

    def start(self):
        if self._tick_id == 0:
            self._tick_id = self.add_tick_callback(self._tick)

    def stop(self):
        if self._tick_id != 0:
            self.remove_tick_callback(self._tick_id)
            self._tick_id = 0

    def set_points(self, x1, y1, x2, y2):
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2
        self._update_range()
        self.queue_draw()

    def _update_range(self):
        """Compute the actual min/max of the eased output by sampling."""
        samples = [ease(i / 100, self.x1, self.y1, self.x2, self.y2) for i in range(101)]
        self._ease_min = min(0.0, min(samples))
        self._ease_max = max(1.0, max(samples))

    def _tick(self, widget, frame_clock):
        now = frame_clock.get_frame_time()
        if self._pause_until > 0:
            if now < self._pause_until:
                return GLib.SOURCE_CONTINUE
            self._pause_until = 0
            self._progress = 0.0
            self.queue_draw()
            return GLib.SOURCE_CONTINUE
        self._progress += self.SPEED
        if self._progress >= 1.0:
            self._progress = 1.0
            self._pause_until = now + self.PAUSE_DURATION_US
        self.queue_draw()
        return GLib.SOURCE_CONTINUE

    def _draw(self, area, cr, width, height):
        color = self.get_color()
        r, g, b = color.red, color.green, color.blue

        # Visual range from sampled curve extremes
        dot_r = 6
        pad = dot_r + 2
        span = self._ease_max - self._ease_min
        usable = width - 2 * pad

        def val_to_x(v):
            return pad + (v - self._ease_min) / span * usable

        # Track line (represents [0, 1]) with dashed overshoot extensions
        track_y = height / 2
        cr.set_source_rgba(r, g, b, 0.15)
        cr.set_line_width(2)
        if self._ease_min < 0.0:
            cr.set_dash([4, 4])
            cr.move_to(val_to_x(self._ease_min), track_y)
            cr.line_to(val_to_x(0.0), track_y)
            cr.stroke()
            cr.set_dash([])
        if self._ease_max > 1.0:
            cr.set_dash([4, 4])
            cr.move_to(val_to_x(1.0), track_y)
            cr.line_to(val_to_x(self._ease_max), track_y)
            cr.stroke()
            cr.set_dash([])
        cr.move_to(val_to_x(0.0), track_y)
        cr.line_to(val_to_x(1.0), track_y)
        cr.stroke()

        # Eased position
        eased = ease(self._progress, self.x1, self.y1, self.x2, self.y2)
        dot_x = val_to_x(eased)

        # Dot
        accent_r, accent_g, accent_b = ACCENT_RGB
        cr.set_source_rgba(accent_r, accent_g, accent_b, 1.0)
        cr.arc(dot_x, track_y, dot_r, 0, 2 * math.pi)
        cr.fill()
