"""Wizard dialog for migrating a Hyprlang config to Lua.

Thin preview-and-confirm shell around :mod:`hyprland_config`'s
conversion API: parses the user's Hyprlang entrypoint (following
sources), shows a summary of what would be written and what couldn't
be migrated, and on "Migrate" calls ``execute_conversion`` to write
the new ``.lua`` files. Never touches the originals — the wizard
refuses by default to overwrite an existing ``.lua`` and the user can
opt in per-conflict with the ``Overwrite`` toggle.
"""

from collections.abc import Callable

from gi.repository import Adw, Gtk
from hyprland_config import (
    ConversionPlan,
    ConversionResult,
    ParseError,
    SourceCycleError,
    analyze_conversion,
    default_hyprlang_entrypoint,
    execute_conversion,
)

from hyprmod.core import config
from hyprmod.ui.dialog import SingletonDialogMixin


class LuaMigrationDialog(SingletonDialogMixin, Adw.Dialog):
    """Preview-and-write wizard for the Hyprlang → Lua migration."""

    def __init__(self, *, on_done: Callable[[ConversionResult], None] | None = None):
        super().__init__()
        self._on_done = on_done
        self._plan: ConversionPlan | None = None
        self._overwrite_switch: Adw.SwitchRow | None = None

        self.set_title("Migrate to Lua")
        self.set_content_width(640)
        self.set_content_height(560)

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        self._migrate_btn = Gtk.Button(label="Migrate")
        self._migrate_btn.add_css_class("suggested-action")
        self._migrate_btn.connect("clicked", self._on_migrate)
        self._migrate_btn.set_sensitive(False)
        header.pack_end(self._migrate_btn)
        toolbar.add_top_bar(header)

        # Scrollable content — preview groups can get tall on configs split
        # across a lot of sourced files.
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_vexpand(True)

        self._content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self._content_box.set_margin_top(18)
        self._content_box.set_margin_bottom(18)
        self._content_box.set_margin_start(18)
        self._content_box.set_margin_end(18)
        scroller.set_child(self._content_box)

        toolbar.set_content(scroller)
        self.set_child(toolbar)

        self._populate()

    # ------------------------------------------------------------------
    # Population — analyze the source and lay out the preview
    # ------------------------------------------------------------------

    def _populate(self) -> None:
        """Run :func:`analyze_conversion` and build the preview (or an empty state)."""
        if config.is_lua_mode():
            self._show_empty(
                "You're already on Lua",
                "An existing hyprland.lua was found, so Hyprland is in Lua mode. "
                "Migrate the file by hand or delete it first to re-run the wizard.",
                icon="emblem-ok-symbolic",
            )
            return

        source = default_hyprlang_entrypoint()
        if not source.exists():
            self._show_empty(
                "No hyprland.conf found",
                f"Looked for {source}. Nothing to migrate.",
            )
            return

        try:
            plan = analyze_conversion(source)
        except (ParseError, SourceCycleError, OSError) as exc:
            self._show_empty("Couldn't parse hyprland.conf", str(exc))
            return

        self._plan = plan
        self._build_summary(plan)
        self._build_unmapped(plan)
        self._build_preview(plan)
        self._refresh_migrate_state()

    def _build_summary(self, plan: ConversionPlan) -> None:
        group = Adw.PreferencesGroup(title="Summary")
        group.set_description(
            "Your existing hyprland.conf stays untouched. The wizard writes "
            "hyprland.lua and one .lua per sourced file alongside the originals."
        )

        files_row = Adw.ActionRow(title="Files to write")
        files_row.set_subtitle(self._files_to_write_subtitle(plan))
        files_row.add_suffix(_count_pill(len(plan.output_files)))
        group.add(files_row)

        if plan.sourced_count:
            sourced_row = Adw.ActionRow(title="Sourced sub-configs")
            sourced_row.set_subtitle("Each gets its own .lua next to the original .conf.")
            sourced_row.add_suffix(_count_pill(plan.sourced_count))
            group.add(sourced_row)

        if plan.existing_lua:
            self._overwrite_switch = Adw.SwitchRow(title="Overwrite existing .lua files")
            self._overwrite_switch.set_subtitle(self._conflict_subtitle(plan.existing_lua))
            self._overwrite_switch.connect(
                "notify::active",
                lambda *_: self._refresh_migrate_state(),
            )
            group.add(self._overwrite_switch)

        self._content_box.append(group)

    def _build_unmapped(self, plan: ConversionPlan) -> None:
        if not plan.unmapped:
            return
        group = Adw.PreferencesGroup(title="Won't migrate")
        group.set_description(
            "The Lua API doesn't have a direct equivalent for these — they'll be "
            "left out of the generated .lua and you'll need to port them by hand."
        )
        for unmapped in plan.unmapped:
            row = Adw.ActionRow(title=unmapped.line)
            row.set_subtitle(unmapped.source.name)
            row.add_css_class("property")
            group.add(row)
        self._content_box.append(group)

    def _build_preview(self, plan: ConversionPlan) -> None:
        """Show the first 40 lines of each emitted file in an expander."""
        group = Adw.PreferencesGroup(title="Preview")
        for path in sorted(plan.output_files):
            row = Adw.ExpanderRow(title=path.name)
            row.set_subtitle(str(path.parent))

            buf = Gtk.TextBuffer()
            buf.set_text(_truncate_preview(plan.output_files[path]))
            view = Gtk.TextView(buffer=buf)
            view.set_editable(False)
            view.set_monospace(True)
            view.set_cursor_visible(False)
            view.set_top_margin(6)
            view.set_bottom_margin(6)
            view.set_left_margin(12)
            view.set_right_margin(12)
            view.add_css_class("card")

            row.add_row(view)
            group.add(row)
        self._content_box.append(group)

    def _show_empty(
        self, title: str, description: str, *, icon: str = "emblem-default-symbolic"
    ) -> None:
        status = Adw.StatusPage()
        status.set_icon_name(icon)
        status.set_title(title)
        status.set_description(description)
        status.set_vexpand(True)
        self._content_box.append(status)

    # ------------------------------------------------------------------
    # Migrate action
    # ------------------------------------------------------------------

    def _refresh_migrate_state(self) -> None:
        """Enable the Migrate button only when the plan can run safely."""
        if self._plan is None:
            self._migrate_btn.set_sensitive(False)
            return
        if self._plan.existing_lua and not self._wants_overwrite():
            self._migrate_btn.set_sensitive(False)
            self._migrate_btn.set_tooltip_text(
                "Existing .lua files would block the write. "
                "Toggle 'Overwrite existing .lua files' to continue."
            )
            return
        self._migrate_btn.set_sensitive(True)
        self._migrate_btn.set_tooltip_text(None)

    def _wants_overwrite(self) -> bool:
        return bool(self._overwrite_switch and self._overwrite_switch.get_active())

    def _on_migrate(self, _btn: Gtk.Button) -> None:
        if self._plan is None:
            return
        result = execute_conversion(self._plan, overwrite=self._wants_overwrite())
        if self._on_done is not None:
            self._on_done(result)
        self.close()

    # ------------------------------------------------------------------
    # Subtitle helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _files_to_write_subtitle(plan: ConversionPlan) -> str:
        primary = plan.primary_output.name if plan.primary_output else "hyprland.lua"
        if plan.sourced_count == 0:
            return primary
        return f"{primary} plus {plan.sourced_count} sub-configs"

    @staticmethod
    def _conflict_subtitle(existing: list) -> str:
        if len(existing) == 1:
            return f"{existing[0].name} already exists — will overwrite when enabled."
        return f"{len(existing)} .lua files already exist — will overwrite when enabled."


def _count_pill(count: int) -> Gtk.Label:
    label = Gtk.Label(label=str(count))
    label.set_valign(Gtk.Align.CENTER)
    label.add_css_class("dim-label")
    return label


_PREVIEW_LINES = 40


def _truncate_preview(text: str) -> str:
    """Trim previews to a fixed line count with an ellipsis marker."""
    lines = text.splitlines()
    if len(lines) <= _PREVIEW_LINES:
        return text
    head = "\n".join(lines[:_PREVIEW_LINES])
    omitted = len(lines) - _PREVIEW_LINES
    return f"{head}\n-- … {omitted} more lines"
