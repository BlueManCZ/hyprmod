"""Consolidated empty-state widget used throughout the app.

Wraps :class:`Adw.StatusPage` with the project's conventions for empty
lists, "no results" placeholders, and pre-onboarding screens:

- ``vexpand=True`` by default so the widget centers vertically when it's
  the only thing in a page's content box.
- Optional ``primary_action`` (pill + ``suggested-action``) and
  ``secondary_action`` (pill) buttons centered below the description.
- Buttons accept a plain ``(label, callback)`` tuple — no extra
  per-callsite glue for the recurring pattern.

Centralising these decisions means a copy or styling tweak (e.g.
swapping "pill" for a different button shape, changing default
description font weight) touches one file rather than ten.

Implementation note: ``AdwStatusPage`` is marked GObject-final, so we
compose rather than inherit. ``EmptyState`` is an :class:`Adw.Bin`
that owns a ``StatusPage`` child and forwards the title/description
setters used by callers that swap copy on the fly (``window_picker``).
"""

from collections.abc import Callable

from gi.repository import Adw, Gtk

ActionSpec = tuple[str, Callable[[], None]]


class EmptyState(Adw.Bin):
    """Adwaita StatusPage with HyprMod's empty-state conventions baked in."""

    def __init__(
        self,
        *,
        title: str,
        description: str | None = None,
        icon_name: str | None = None,
        primary_action: ActionSpec | None = None,
        secondary_action: ActionSpec | None = None,
    ):
        super().__init__()

        page = Adw.StatusPage(title=title)
        if description:
            page.set_description(description)
        if icon_name:
            page.set_icon_name(icon_name)

        if primary_action is not None or secondary_action is not None:
            button_box = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL,
                spacing=12,
                halign=Gtk.Align.CENTER,
            )
            if primary_action is not None:
                button_box.append(self._build_button(primary_action, suggested=True))
            if secondary_action is not None:
                button_box.append(self._build_button(secondary_action, suggested=False))
            page.set_child(button_box)

        self._page = page
        self.set_child(page)
        # vexpand makes the empty state center vertically when it's the
        # only widget in its page's content box. Page-internal callers
        # that compose the empty state alongside other rows can override
        # via ``set_vexpand(False)`` after construction.
        self.set_vexpand(True)

    # ── Forwarders for runtime copy swaps ──
    #
    # ``window_picker`` flips between "No Open Windows" and "No Matches"
    # on the same instance; the inner StatusPage holds the title and
    # description, so forward there rather than letting callers reach
    # into the private widget.

    def set_title(self, title: str) -> None:
        self._page.set_title(title)

    def set_description(self, description: str) -> None:
        self._page.set_description(description)

    @staticmethod
    def _build_button(spec: ActionSpec, *, suggested: bool) -> Gtk.Button:
        label, callback = spec
        btn = Gtk.Button(label=label)
        btn.add_css_class("pill")
        if suggested:
            btn.add_css_class("suggested-action")
        btn.connect("clicked", lambda _b: callback())
        return btn
