"""Profile library page — save, load, duplicate, delete configuration profiles."""

from html import escape as html_escape

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from hyprmod.core import profiles
from hyprmod.ui import clear_children, confirm, make_page_layout
from hyprmod.ui.dna import DnaWidget
from hyprmod.ui.empty_state import EmptyState


def _option_summary(n: int) -> str:
    if n == 0:
        return "No customizations"
    return f"{n} option{'s' if n != 1 else ''}"


class ProfileCard(Gtk.Box):
    """A card representing a single profile. Click to activate."""

    def __init__(self, profile: dict, is_active: bool, on_action):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.add_css_class("card")
        if is_active:
            self.add_css_class("profile-active")

        self._profile = profile
        self._on_action = on_action

        profile_values = profiles.read_profile_values(profile["id"])

        # Make entire card clickable to activate
        if not is_active:
            click = Gtk.GestureClick()
            click.connect("released", self._on_click)
            self.add_controller(click)
            self.set_cursor_from_name("pointer")

        # ── Single horizontal row ──
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_top(14)
        row.set_margin_bottom(14)
        row.set_margin_start(16)
        row.set_margin_end(10)

        # Left: text column
        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_col.set_hexpand(True)
        text_col.set_valign(Gtk.Align.CENTER)

        name = profile.get("name", "") or profile["id"]
        name_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        name_label = Gtk.Label(label=html_escape(name))
        name_label.set_xalign(0)
        name_label.add_css_class("heading")
        name_label.set_ellipsize(Pango.EllipsizeMode.END)
        name_row.append(name_label)

        if is_active:
            badge = Gtk.Label(label="Active")
            badge.add_css_class("profile-badge-active")
            badge.set_valign(Gtk.Align.CENTER)
            name_row.append(badge)

        text_col.append(name_row)

        meta_label = Gtk.Label(label=_option_summary(len(profile_values)))
        meta_label.set_xalign(0)
        meta_label.add_css_class("dim-label")
        meta_label.add_css_class("caption")
        text_col.append(meta_label)

        row.append(text_col)

        dna = DnaWidget(width=180, height=28)
        dna.set_values(profile_values)
        dna.set_halign(Gtk.Align.END)
        dna.set_valign(Gtk.Align.CENTER)
        row.append(dna)

        # Menu button
        menu_btn = self._build_menu_button(profile, is_active)
        menu_btn.set_valign(Gtk.Align.CENTER)
        row.append(menu_btn)

        self.append(row)

    def _on_click(self, gesture, _n_press, x, y):
        # Don't activate if user clicked on the menu button area
        widget = self.pick(x, y, Gtk.PickFlags.DEFAULT)
        if widget and _is_in_menu_button(widget):
            return
        self._on_action("activate", self._profile["id"])

    def _build_menu_button(self, profile: dict, is_active: bool) -> Gtk.MenuButton:
        menu = Gio.Menu()

        if not is_active:
            menu.append("Activate", f"profile.activate::{profile['id']}")

        if is_active:
            menu.append("Update from current", f"profile.update::{profile['id']}")

        menu.append("Rename", f"profile.rename::{profile['id']}")
        menu.append("Duplicate", f"profile.duplicate::{profile['id']}")

        delete_section = Gio.Menu()
        delete_section.append("Delete", f"profile.delete::{profile['id']}")
        menu.append_section(None, delete_section)

        btn = Gtk.MenuButton()
        btn.set_icon_name("view-more-symbolic")
        btn.add_css_class("flat")
        btn.add_css_class("circular")
        btn.set_menu_model(menu)
        return btn


def _is_in_menu_button(widget: Gtk.Widget) -> bool:
    """Check if a widget is inside a MenuButton."""
    return widget.get_ancestor(Gtk.MenuButton) is not None


class ProfilesPage:
    """Builds the profile library page."""

    def __init__(self, window):
        self._window = window
        self._last_toast: Adw.Toast | None = None
        self._cached_profiles: list[dict] = []
        self._cached_active_id: str | None = None
        # DNA widget for the "Current configuration" card. Built lazily in
        # ``build()`` and kept across rebuilds so we can update its values
        # without recreating the row.
        self._current_dna: DnaWidget | None = None
        self._profiles_box: Gtk.Box | None = None

    def build(self, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        page_header = header or Adw.HeaderBar()

        save_current_btn = Gtk.Button(icon_name="list-add-symbolic")
        save_current_btn.set_tooltip_text("Save current as new profile")
        save_current_btn.connect("clicked", self._on_save_current)
        page_header.pack_start(save_current_btn)

        toolbar_view, _, self._content_box, _ = make_page_layout(header=page_header, spacing=6)

        # "Current configuration" card — moved here from the sidebar so the
        # DNA fingerprint sits next to the profile list it's meant to be
        # compared against. Built once and kept across rebuilds; only the
        # DNA values change when the user saves.
        self._content_box.append(self._build_current_config_card())

        # Profiles get rebuilt on every change; isolate them in their own
        # box so ``rebuild()`` can clear just this sub-container without
        # nuking the current-config card above.
        self._profiles_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._content_box.append(self._profiles_box)

        self._install_actions()
        self.rebuild()

        return toolbar_view

    def _build_current_config_card(self) -> Gtk.Widget:
        """Build the always-present "Current configuration" header card."""
        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        card.add_css_class("card")
        card.set_margin_top(0)
        card.set_margin_bottom(2)

        text_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_col.set_hexpand(True)
        text_col.set_valign(Gtk.Align.CENTER)
        text_col.set_margin_top(14)
        text_col.set_margin_bottom(14)
        text_col.set_margin_start(16)

        title = Gtk.Label(label="Current configuration", xalign=0)
        title.add_css_class("heading")
        text_col.append(title)

        subtitle = Gtk.Label(
            label="Fingerprint of the values currently saved on disk",
            xalign=0,
        )
        subtitle.add_css_class("dim-label")
        subtitle.add_css_class("caption")
        text_col.append(subtitle)

        card.append(text_col)

        self._current_dna = DnaWidget(width=180, height=28)
        self._current_dna.set_halign(Gtk.Align.END)
        self._current_dna.set_valign(Gtk.Align.CENTER)
        self._current_dna.set_margin_end(26)
        card.append(self._current_dna)

        return card

    def update_dna(self, values: dict) -> None:
        """Refresh the current-config DNA fingerprint."""
        if self._current_dna is not None:
            self._current_dna.set_values(values)

    def _install_actions(self):
        group = Gio.SimpleActionGroup()

        for name, handler in [
            ("activate", self._action_activate),
            ("update", self._action_update),
            ("rename", self._action_rename),
            ("duplicate", self._action_duplicate),
            ("delete", self._action_delete),
        ]:
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", handler)
            group.add_action(action)

        self._window.insert_action_group("profile", group)

    # ── Actions ──

    def _action_activate(self, _action, param):
        profile_id = param.get_string()
        self._do_activate(profile_id)

    def _action_update(self, _action, param):
        profile_id = param.get_string()
        self._window.save()
        profiles.update(profile_id)
        self.rebuild()
        self._show_toast("Profile updated")

    def _action_rename(self, _action, param):
        profile_id = param.get_string()
        prof = self._find_profile(profile_id)
        if not prof:
            return

        rename_body = "Choose a new name for this profile"
        dialog = Adw.AlertDialog(heading="Rename Profile")
        dialog.set_body(rename_body)
        dialog.set_body_use_markup(False)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Rename")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        taken = self._existing_names(exclude_id=profile_id)

        group = Adw.PreferencesGroup()
        name_row = Adw.EntryRow(title="Name")
        name_row.set_text(prof.get("name", ""))
        name_row.connect(
            "changed",
            lambda _: self._validate_name(name_row, dialog, taken, rename_body),
        )
        group.add(name_row)
        dialog.set_extra_child(group)

        def on_response(dlg, response):
            if response == "save":
                new_name = name_row.get_text().strip()
                profiles.rename(profile_id, new_name)
                self.rebuild()

        name_row.connect(
            "entry-activated",
            lambda _: self._confirm_dialog(dialog, "save"),
        )

        dialog.connect("response", on_response)
        dialog.present(self._window)
        name_row.grab_focus()

    def _action_duplicate(self, _action, param):
        profile_id = param.get_string()
        profiles.duplicate(profile_id)
        self.rebuild()
        self._show_toast("Profile duplicated")

    def _action_delete(self, _action, param):
        profile_id = param.get_string()
        self._confirm_delete(profile_id)

    # ── Build ──

    def rebuild(self):
        # Only clear the profiles sub-container — the current-config card
        # at the top of ``_content_box`` is built once in ``build()`` and
        # updated in place via ``update_dna()``.
        if self._profiles_box is None:
            return
        clear_children(self._profiles_box)

        profile_list, active_id = profiles.list_profiles_and_active()
        self._cached_profiles = profile_list
        self._cached_active_id = active_id

        if not profile_list:
            self._profiles_box.append(
                EmptyState(
                    title="No Profiles",
                    description=(
                        "Profiles let you save and switch between configurations "
                        "instantly. Save your current setup to create your first one."
                    ),
                    icon_name="user-bookmarks-symbolic",
                    primary_action=("Save Current as Profile", self._on_save_current),
                )
            )
            return

        # Natural order — don't re-sort
        for prof in profile_list:
            is_active = prof["id"] == active_id
            card = ProfileCard(prof, is_active=is_active, on_action=self._on_card_action)
            self._profiles_box.append(card)

    def _on_card_action(self, action: str, profile_id: str):
        if action == "activate":
            self._do_activate(profile_id)

    def _do_activate(self, profile_id: str):
        if self._window.has_dirty():
            self._confirm_activate(profile_id)
        else:
            self._activate_now(profile_id)

    def _confirm_activate(self, profile_id: str):
        name = self._profile_name(profile_id)

        confirm(
            self._window,
            "Unsaved Changes",
            "You have unsaved changes that will be lost "
            f"when switching to \u201c{html_escape(name)}\u201d.",
            "Discard & Switch",
            lambda: self._activate_now(profile_id),
        )

    def _activate_now(self, profile_id: str):
        name = self._profile_name(profile_id)
        profiles.activate(profile_id, self._window.hypr)
        self._window.reload_after_profile()
        self.rebuild()
        self._show_toast(f"Switched to {name}")

    def _confirm_delete(self, profile_id: str):
        name = self._profile_name(profile_id, fallback="this profile")

        def do_delete():
            profiles.delete(profile_id)
            self.rebuild()
            self._show_toast("Profile deleted")

        confirm(
            self._window,
            "Delete Profile?",
            f"\u201c{html_escape(name)}\u201d will be permanently deleted.",
            "Delete",
            do_delete,
        )

    def _on_save_current(self, _button=None):
        self._show_save_dialog(navigate_to_profiles=False)

    def save_as_new_and_navigate(self):
        """Show name dialog, save as new profile, navigate to profiles page."""
        self._show_save_dialog(navigate_to_profiles=True)

    def _show_save_dialog(self, *, navigate_to_profiles: bool):
        save_body = "Enter a name for the new profile"
        dialog = Adw.AlertDialog(heading="Save Current as Profile")
        dialog.set_body(save_body)
        dialog.set_body_use_markup(False)

        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("save", False)

        taken = self._existing_names()

        group = Adw.PreferencesGroup()

        name_row = Adw.EntryRow(title="Name")
        name_row.connect(
            "changed",
            lambda _: self._validate_name(name_row, dialog, taken, save_body),
        )
        group.add(name_row)

        dialog.set_extra_child(group)

        name_row.connect(
            "entry-activated",
            lambda _: self._confirm_dialog(dialog, "save"),
        )

        def on_response(dlg, response):
            if response == "save":
                name = name_row.get_text().strip()
                # Save pending changes to disk if any, then snapshot
                if self._window.has_dirty():
                    self._window.save()
                profiles.save_current_as(name)
                self.rebuild()
                self._show_toast(f"Profile \u2018{name}\u2019 saved")
                if navigate_to_profiles:
                    self._window.show_page("profiles")

        dialog.connect("response", on_response)
        dialog.present(self._window)
        name_row.grab_focus()

    def _existing_names(self, exclude_id: str | None = None) -> set[str]:
        """Return set of profile names (lowercased), optionally excluding one."""
        names: set[str] = set()
        for p in self._cached_profiles:
            if p["id"] == exclude_id:
                continue
            name = p.get("name", "").strip().lower()
            if name:
                names.add(name)
        return names

    def _validate_name(self, name_row, dialog, taken_names: set[str], default_body: str = ""):
        """Enable/disable save and update dialog body based on name state."""
        name = name_row.get_text().strip()
        is_taken = name.lower() in taken_names
        has_text = bool(name)
        dialog.set_response_enabled("save", has_text and not is_taken)
        if is_taken:
            name_row.add_css_class("error")
            dialog.set_body("A profile with this name already exists")
            dialog.set_body_use_markup(False)
            dialog.add_css_class("profile-name-error")
        else:
            name_row.remove_css_class("error")
            dialog.set_body(default_body)
            dialog.set_body_use_markup(False)
            dialog.remove_css_class("profile-name-error")

    @staticmethod
    def _confirm_dialog(dialog: Adw.AlertDialog, response_id: str):
        """Programmatically confirm a dialog if the response is enabled."""
        if dialog.get_response_enabled(response_id):
            dialog.emit("response", response_id)
            dialog.force_close()

    def _find_profile(self, profile_id: str) -> dict | None:
        return next((p for p in self._cached_profiles if p["id"] == profile_id), None)

    def _profile_name(self, profile_id: str, *, fallback: str = "profile") -> str:
        """Look up a profile's display name, returning *fallback* if missing."""
        prof = self._find_profile(profile_id)
        return prof.get("name", fallback) if prof else fallback

    def _show_toast(self, message: str):
        if self._last_toast is not None:
            self._last_toast.dismiss()
        toast = Adw.Toast(title=message)
        toast.set_timeout(2)
        self._last_toast = toast
        self._window.add_toast(toast)


__all__ = ["ProfileCard", "ProfilesPage"]
