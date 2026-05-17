"""Wizard dialog for reviewing and applying Hyprland config deprecations.

Thin preview-and-confirm shell around :mod:`hyprmod.core.deprecations`:
runs ``scan()`` on present, lists each fixable file with a unified diff
of what would change, and on Apply calls ``apply_to_file`` per selected
plan, writing a timestamped backup beside each original.
"""

import difflib
from collections.abc import Callable
from pathlib import Path

from gi.repository import Adw, Gtk

from hyprmod.core import deprecations
from hyprmod.ui.dialog import SingletonDialogMixin


class DeprecationDialog(SingletonDialogMixin, Adw.Dialog):
    """Preview-and-write wizard for fixable Hyprland config deprecations."""

    def __init__(
        self,
        *,
        managed_path: Path,
        user_root_path: Path,
        hyprland_version: str | None = None,
        on_done: Callable[[list[deprecations.ApplyResult]], None] | None = None,
    ) -> None:
        super().__init__()
        self._managed_path = managed_path
        self._user_root_path = user_root_path
        self._hyprland_version = hyprland_version
        self._on_done = on_done
        self._scan: deprecations.ScanResult | None = None
        self._checkboxes: dict[Path, Gtk.CheckButton] = {}

        self.set_title("Migrate deprecated syntax")
        self.set_content_width(720)
        self.set_content_height(580)

        toolbar = Adw.ToolbarView()

        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_btn)

        self._apply_btn = Gtk.Button(label="Apply selected")
        self._apply_btn.add_css_class("suggested-action")
        self._apply_btn.connect("clicked", self._on_apply)
        self._apply_btn.set_sensitive(False)
        header.pack_end(self._apply_btn)
        toolbar.add_top_bar(header)

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
    # Population
    # ------------------------------------------------------------------

    def _populate(self) -> None:
        scan = deprecations.scan(
            managed_path=self._managed_path,
            user_root_path=self._user_root_path,
            hyprland_version=self._hyprland_version,
        )
        self._scan = scan

        if not scan.has_fixable and not scan.unfixable:
            self._show_empty(
                "No deprecations found",
                "Your Hyprland config is up to date.",
                icon="emblem-ok-symbolic",
            )
            return

        if scan.has_fixable:
            self._build_files_group(scan)
        if scan.unfixable:
            self._build_unfixable_group(scan)
        self._refresh_apply_state()

    def _build_files_group(self, scan: deprecations.ScanResult) -> None:
        group = Adw.PreferencesGroup(title="Fixable files")
        group.set_description(
            "Each file is rewritten in place; a timestamped backup is "
            "saved beside it (.hyprmod-bak-<unix-ts>) before the change."
        )

        for plan in scan.files:
            group.add(self._build_file_row(plan))

        self._content_box.append(group)

    def _build_file_row(self, plan: deprecations.FilePlan) -> Adw.ExpanderRow:
        row = Adw.ExpanderRow(title=plan.path.name)
        row.set_subtitle(str(plan.path.parent))

        # Per-file checkbox — drives whether Apply touches this plan.
        check = Gtk.CheckButton()
        check.set_active(not plan.is_symlink)  # symlinks default off; user opts in
        check.set_valign(Gtk.Align.CENTER)
        check.connect("toggled", lambda *_: self._refresh_apply_state())
        row.add_prefix(check)
        self._checkboxes[plan.path] = check

        if plan.is_managed:
            row.add_suffix(_pill("managed", "accent"))
        if plan.is_symlink:
            row.add_suffix(_pill("symlink", "warning"))

        rule_count = len(plan.rules) or 1
        row.add_suffix(_pill(f"{rule_count} change{'' if rule_count == 1 else 's'}"))

        if plan.is_symlink:
            warning = Adw.ActionRow(title="This path resolves through a symlink")
            warning.set_subtitle(
                "Writing it will modify the symlink target, "
                "which may live in a dotfiles repository."
            )
            warning.add_css_class("warning")
            row.add_row(warning)

        diff_view = self._build_diff_view(plan)
        row.add_row(diff_view)

        return row

    def _build_diff_view(self, plan: deprecations.FilePlan) -> Gtk.Widget:
        diff_text = _unified_diff(plan.original, plan.migrated, plan.path.name)
        buf = Gtk.TextBuffer()
        buf.set_text(diff_text or "(no textual diff)")
        view = Gtk.TextView(buffer=buf)
        view.set_editable(False)
        view.set_monospace(True)
        view.set_cursor_visible(False)
        view.set_top_margin(6)
        view.set_bottom_margin(6)
        view.set_left_margin(12)
        view.set_right_margin(12)
        view.add_css_class("card")
        return view

    def _build_unfixable_group(self, scan: deprecations.ScanResult) -> None:
        group = Adw.PreferencesGroup(title="Detected but not auto-fixable")
        group.set_description(
            "These deprecations need a manual edit — there's no safe automatic rewrite."
        )
        for rule in scan.unfixable:
            title = rule.key or rule.message
            row = Adw.ActionRow(title=title)
            subtitle_parts = []
            if rule.source_name:
                loc = f"{rule.source_name}:{rule.lineno}" if rule.lineno else rule.source_name
                subtitle_parts.append(loc)
            if rule.suggestion:
                subtitle_parts.append(rule.suggestion)
            elif rule.message and rule.message != title:
                subtitle_parts.append(rule.message)
            row.set_subtitle(" — ".join(subtitle_parts))
            row.add_css_class("property")
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
    # Apply
    # ------------------------------------------------------------------

    def _refresh_apply_state(self) -> None:
        selected_count = sum(1 for cb in self._checkboxes.values() if cb.get_active())
        self._apply_btn.set_sensitive(selected_count > 0)

    def _on_apply(self, _btn: Gtk.Button) -> None:
        if self._scan is None:
            return
        results: list[deprecations.ApplyResult] = []
        for plan in self._scan.files:
            cb = self._checkboxes.get(plan.path)
            if cb is None or not cb.get_active():
                continue
            results.append(deprecations.apply_to_file(plan))
        if self._on_done is not None:
            self._on_done(results)
        self.close()


def _unified_diff(original: str, migrated: str, name: str) -> str:
    """Return a compact unified diff between *original* and *migrated*.

    Empty trailing newlines are normalized so the diff doesn't end with a
    spurious "\\ No newline at end of file" marker on inputs that already
    end in a newline.
    """
    original_lines = original.splitlines(keepends=True)
    migrated_lines = migrated.splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        original_lines,
        migrated_lines,
        fromfile=f"{name} (current)",
        tofile=f"{name} (after migration)",
        n=2,
    )
    return "".join(diff_iter)


def _pill(text: str, css_class: str = "dim-label") -> Gtk.Label:
    label = Gtk.Label(label=text)
    label.set_valign(Gtk.Align.CENTER)
    label.add_css_class(css_class)
    return label
