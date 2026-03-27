"""Unsaved-changes banner with save/discard buttons and save animation."""

from gi.repository import Adw, Gio, Gtk

from hyprmod.ui import clear_children
from hyprmod.ui.timer import Timer


class DirtyBanner(Gtk.Revealer):
    """A slide-up banner shown when there are unsaved changes.

    Signals / callbacks:
        on_save:              called for a plain save (no active profile)
        on_save_update:       called to save and update the active profile
        on_save_without_update: called to save and deactivate the profile
        on_discard:           called when the user clicks "Discard"
    """

    def __init__(
        self,
        *,
        on_save=None,
        on_save_update=None,
        on_save_without_update=None,
        on_save_as_new=None,
        on_discard=None,
    ):
        super().__init__()
        self.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.set_reveal_child(False)

        self._on_save = on_save
        self._on_save_update = on_save_update
        self._on_save_without_update = on_save_without_update
        self._on_save_as_new = on_save_as_new
        self._on_discard = on_discard
        self._has_active_profile = False
        self._transition_timer = Timer()
        self._reset_timer = Timer()

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        box.add_css_class("toolbar")
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(4)
        box.set_margin_bottom(4)

        self._icon = Gtk.Image.new_from_icon_name("dialog-warning-symbolic")
        self._icon.add_css_class("warning")
        box.append(self._icon)

        self._label = Gtk.Label(label="Unsaved changes \u2014 applied live, not saved to disk")
        self._label.set_hexpand(True)
        self._label.set_xalign(0)
        box.append(self._label)

        self._discard_button = Gtk.Button(label="Discard")
        self._discard_button.connect("clicked", self._on_discard_clicked)
        box.append(self._discard_button)

        # Save button area — holds either a plain Button or a SplitButton
        self._save_area = Gtk.Box()
        box.append(self._save_area)

        self._save_widget: Adw.SplitButton | None = None
        self._build_save_button()
        self.set_child(box)

    # -- Public API --

    def set_active_profile(self, has_profile: bool):
        """Update whether a profile is active, rebuilding the save button."""
        if has_profile == self._has_active_profile:
            return
        self._has_active_profile = has_profile
        self._build_save_button()

    def show_dirty(self):
        """Reveal the banner in its default 'unsaved changes' state."""
        if self._transition_timer.active:
            self._cancel_transition()
        self.set_reveal_child(True)

    def hide(self):
        """Hide the banner immediately."""
        self.set_reveal_child(False)

    def show_saved(self):
        """Transition to 'saved' state, then auto-hide after a delay."""
        if self._save_widget is None:
            return
        self._save_widget.set_sensitive(False)
        self._discard_button.set_visible(False)
        self._label.set_label("Changes saved to disk")
        self._icon.set_from_icon_name("check-plain-symbolic")
        self._icon.remove_css_class("warning")
        self._icon.add_css_class("accent")
        self._transition_timer.schedule(1500, self._begin_hide)

    # -- Internal --

    def _build_save_button(self):
        """Build a SplitButton with profile-aware menu options."""
        clear_children(self._save_area)

        menu = Gio.Menu()
        if self._has_active_profile:
            menu.append("Save without updating profile", "banner.save-no-update")
        menu.append("Save as new profile", "banner.save-as-new")

        group = Gio.SimpleActionGroup()
        for name, handler in [
            ("save-no-update", self._on_save_clicked_without_update),
            ("save-as-new", self._on_save_clicked_as_new),
        ]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            group.add_action(action)
        self._save_area.insert_action_group("banner", group)

        # Primary click: update profile if active, plain save otherwise
        if self._has_active_profile:
            primary = self._on_save_clicked_update
        else:
            primary = self._on_save_clicked
        split = Adw.SplitButton(label="Save now")
        split.add_css_class("suggested-action")
        split.set_menu_model(menu)
        split.connect("clicked", primary)
        self._save_widget = split

        self._save_area.append(self._save_widget)

    def _on_save_clicked(self, *_args):
        if self._on_save:
            self._on_save()

    def _on_save_clicked_update(self, *_args):
        if self._on_save_update:
            self._on_save_update()

    def _on_save_clicked_without_update(self, *_args):
        if self._on_save_without_update:
            self._on_save_without_update()

    def _on_save_clicked_as_new(self, *_args):
        if self._on_save_as_new:
            self._on_save_as_new()

    def _on_discard_clicked(self, *_args):
        if self._on_discard:
            self._on_discard()

    def _cancel_transition(self):
        self._transition_timer.cancel()
        self._reset_timer.cancel()
        self._reset()

    def _begin_hide(self):
        self.set_reveal_child(False)
        transition_ms = self.get_transition_duration()
        self._reset_timer.schedule(transition_ms, self._reset)

    def _reset(self):
        if self._save_widget is None:
            return
        self._save_widget.set_sensitive(True)
        self._discard_button.set_visible(True)
        self._label.set_label("Unsaved changes \u2014 applied live, not saved to disk")
        self._icon.set_from_icon_name("dialog-warning-symbolic")
        self._icon.remove_css_class("accent")
        self._icon.add_css_class("warning")
