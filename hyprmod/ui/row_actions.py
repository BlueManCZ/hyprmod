"""Shared discard / remove-override button pair for option, animation, and bind rows."""

from collections.abc import Callable

from gi.repository import Gtk


class RowActions:
    """Two-button action strip: *Discard changes* and *Remove override*.

    Manages the CSS classes ``option-default``, ``option-managed``, and
    ``option-dirty`` on the parent row widget, and controls button visibility.

    Parameters
    ----------
    row:
        The Adw row widget that owns this action strip.
    on_discard:
        Callback for the "Discard changes" button.
    on_reset:
        Callback for the "Remove override" / "Delete" button.
    reset_icon:
        Icon name for the reset button (default ``user-trash-symbolic``).
    reset_tooltip:
        Tooltip for the reset button (default ``"Remove override"``).
    """

    def __init__(
        self,
        row: Gtk.Widget,
        *,
        on_discard: Callable[[], object],
        on_reset: Callable[[], object] | None = None,
        reset_icon: str = "user-trash-symbolic",
        reset_tooltip: str = "Remove override",
    ):
        self._row = row

        self._box = Gtk.Box(spacing=2)
        self._box.set_valign(Gtk.Align.CENTER)
        self._box.add_css_class("reset-button")

        self._discard_btn = Gtk.Button(icon_name="edit-undo-symbolic")
        self._discard_btn.set_valign(Gtk.Align.CENTER)
        self._discard_btn.set_tooltip_text("Discard changes")
        self._discard_btn.add_css_class("flat")
        self._discard_btn.set_visible(False)
        self._discard_btn.connect("clicked", lambda _: on_discard())
        self._box.append(self._discard_btn)

        self._reset_btn: Gtk.Button | None = None
        if on_reset is not None:
            self._reset_btn = Gtk.Button(icon_name=reset_icon)
            self._reset_btn.set_valign(Gtk.Align.CENTER)
            self._reset_btn.set_tooltip_text(reset_tooltip)
            self._reset_btn.add_css_class("flat")
            self._reset_btn.connect("clicked", lambda _: on_reset())
            self._box.append(self._reset_btn)

        row.add_css_class("option-default")

    @property
    def box(self) -> Gtk.Box:
        """The container widget to add as a row suffix."""
        return self._box

    def update(
        self,
        *,
        is_managed: bool,
        is_dirty: bool,
        is_saved: bool,
        show_discard: bool | None = None,
        show_reset: bool | None = None,
    ):
        """Update CSS classes and button visibility.

        Parameters
        ----------
        is_managed:
            Option is currently under HyprMod control (drives CSS).
        is_dirty:
            Option has unsaved changes (orange edge, takes CSS priority).
        is_saved:
            Option has a persisted override in the config file.
        show_discard:
            Override discard-button visibility (default: ``is_dirty``).
        show_reset:
            Override reset-button visibility
            (default: ``is_saved and is_managed``).
        """
        # CSS class cycling
        self._row.remove_css_class("option-default")
        self._row.remove_css_class("option-managed")
        self._row.remove_css_class("option-dirty")

        if is_dirty:
            self._row.add_css_class("option-dirty")
        elif is_managed:
            self._row.add_css_class("option-managed")
        else:
            self._row.add_css_class("option-default")

        # Button visibility
        if show_discard is None:
            show_discard = is_dirty
        if show_reset is None:
            show_reset = is_saved and is_managed
        self._discard_btn.set_visible(show_discard)
        if self._reset_btn is not None:
            self._reset_btn.set_visible(show_reset)

    def reorder_first(self):
        """Move the actions box to be the first child in the suffixes box.

        Useful for OptionRow where the value widget is added as a suffix
        before the actions box.
        """
        suffixes_box = self._box.get_parent()
        if suffixes_box is not None:
            suffixes_box.reorder_child_after(self._box, None)  # type: ignore[attr-defined]
