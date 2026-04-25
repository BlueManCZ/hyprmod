"""Side-by-side and unified config diff widgets.

Renders a unified diff between two text blobs using GTK ``TextView``
with custom tags. Each hunk gets a faint header rule, added lines a
green tint, removed lines a red tint, and unchanged context is dimmed
so the eye lands on the changes.
"""

import difflib

from gi.repository import Gtk, Pango


class ConfigDiffWidget(Gtk.Box):
    """A modern unified-diff viewer for plain-text config files.

    Use ``set_texts(old, new, ...)`` to (re)render the diff. When the
    two texts are equal, the widget shows a friendly "no changes"
    placeholder instead of the empty text view.
    """

    def __init__(self, *, monospace: bool = True):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("config-diff")

        # Header strip showing the file name + +/- summary. The padding
        # is set via CSS (``.config-diff-header``) so the bottom border line
        # appears below the padded area; this is what creates the visual
        # gap above the first diff line.
        self._header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self._header.add_css_class("config-diff-header")

        self._title_label = Gtk.Label(xalign=0)
        self._title_label.add_css_class("heading")
        self._title_label.set_hexpand(True)
        self._header.append(self._title_label)

        self._added_label = Gtk.Label(xalign=1)
        self._added_label.add_css_class("config-diff-added-count")
        self._header.append(self._added_label)

        self._removed_label = Gtk.Label(xalign=1)
        self._removed_label.add_css_class("config-diff-removed-count")
        self._header.append(self._removed_label)

        self.append(self._header)

        # Content stack: text view <-> empty placeholder
        self._stack = Gtk.Stack()
        self._stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self._stack.set_transition_duration(150)
        self._stack.set_vexpand(True)
        self.append(self._stack)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self._text_view = Gtk.TextView()
        self._text_view.set_editable(False)
        self._text_view.set_cursor_visible(False)
        self._text_view.set_left_margin(12)
        self._text_view.set_right_margin(12)
        # Combined with the header's padding + border-bottom, this produces
        # a comfortable gap between the +/- counter strip and the first
        # diff line below.
        self._text_view.set_top_margin(10)
        self._text_view.set_bottom_margin(8)
        self._text_view.add_css_class("config-diff-view")
        if monospace:
            self._text_view.set_monospace(True)

        self._buffer = self._text_view.get_buffer()
        self._init_tags()
        scrolled.set_child(self._text_view)
        self._stack.add_named(scrolled, "diff")

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty.set_margin_top(36)
        empty.set_margin_bottom(36)
        icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        icon.set_pixel_size(36)
        icon.add_css_class("dim-label")
        empty.append(icon)
        empty_label = Gtk.Label(label="Saved config is up to date")
        empty_label.add_css_class("dim-label")
        empty.append(empty_label)
        self._stack.add_named(empty, "empty")

        self._title_label.set_label("Resulting config")
        self._added_label.set_label("")
        self._removed_label.set_label("")
        self._stack.set_visible_child_name("empty")

    def _init_tags(self) -> None:
        table = self._buffer.get_tag_table()
        # Use named tags so styles can be themed via CSS rules on the view.
        # Pango weight + foreground gives us colored markers even when the
        # CSS hooks aren't loaded (e.g. in tests).
        added = Gtk.TextTag.new("diff-added")
        added.set_property("paragraph-background", "rgba(38, 162, 105, 0.18)")
        added.set_property("foreground", "#26a269")
        added.set_property("weight", Pango.Weight.NORMAL)
        table.add(added)

        removed = Gtk.TextTag.new("diff-removed")
        removed.set_property("paragraph-background", "rgba(224, 27, 36, 0.18)")
        removed.set_property("foreground", "#c01c28")
        table.add(removed)

        hunk = Gtk.TextTag.new("diff-hunk")
        hunk.set_property("paragraph-background", "rgba(120, 120, 120, 0.10)")
        hunk.set_property("foreground", "#7a7f87")
        hunk.set_property("style", Pango.Style.ITALIC)
        table.add(hunk)

        context = Gtk.TextTag.new("diff-context")
        context.set_property("foreground", "#888a8f")
        table.add(context)

        meta = Gtk.TextTag.new("diff-meta")
        meta.set_property("foreground", "#7a7f87")
        meta.set_property("style", Pango.Style.ITALIC)
        table.add(meta)

    # ── Public API ──

    def set_texts(
        self,
        old_text: str,
        new_text: str,
        *,
        old_label: str = "saved",
        new_label: str = "next save",
        title: str | None = None,
    ) -> None:
        """Render the unified diff between *old_text* and *new_text*."""
        if title is not None:
            self._title_label.set_label(title)

        old_lines = old_text.splitlines(keepends=True)
        new_lines = new_text.splitlines(keepends=True)

        if old_text == new_text:
            self._added_label.set_label("")
            self._removed_label.set_label("")
            self._buffer.set_text("")
            self._stack.set_visible_child_name("empty")
            return

        diff_lines = list(
            difflib.unified_diff(
                old_lines,
                new_lines,
                fromfile=old_label,
                tofile=new_label,
                n=3,
                lineterm="",
            )
        )

        added = sum(1 for line in diff_lines if line.startswith("+") and not line.startswith("+++"))
        removed = sum(
            1 for line in diff_lines if line.startswith("-") and not line.startswith("---")
        )
        self._added_label.set_label(f"+{added}")
        self._removed_label.set_label(f"−{removed}")

        self._buffer.set_text("")
        end = self._buffer.get_end_iter()

        for line in diff_lines:
            tag = self._classify(line)
            text = line if line.endswith("\n") else line + "\n"
            self._buffer.insert_with_tags_by_name(end, text, tag)

        # Trim the trailing newline we appended for the last line so the
        # paragraph background doesn't bleed into an empty row.
        end = self._buffer.get_end_iter()
        start_of_trailer = end.copy()
        if start_of_trailer.backward_char() and end.get_offset() > 0:
            ch = start_of_trailer.get_char()
            if ch == "\n":
                self._buffer.delete(start_of_trailer, end)

        self._stack.set_visible_child_name("diff")

    @staticmethod
    def _classify(line: str) -> str:
        if line.startswith("@@"):
            return "diff-hunk"
        if line.startswith("+++") or line.startswith("---"):
            return "diff-meta"
        if line.startswith("+"):
            return "diff-added"
        if line.startswith("-"):
            return "diff-removed"
        return "diff-context"
