"""Cursor theme selection page — thumbnails, size, live apply."""

import subprocess
from dataclasses import dataclass
from typing import cast

from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk, Pango

from hyprmod.core import config
from hyprmod.core.cursor_themes import CursorTheme, discover
from hyprmod.core.env_vars import RESERVED_NAMES as _MANAGED_VARS
from hyprmod.core.undo import CursorUndoEntry
from hyprmod.core.xcursor import crop_to_content, load_pointer, pad_to_square, scale_nearest
from hyprmod.pages.section import SectionPage
from hyprmod.ui.managed_row import ManagedRow, make_combo_row

# Theme- vs. size-flavoured cursor env vars. Membership in either tuple
# determines how a parsed value is interpreted (string vs. integer);
# the *union* is the broader contract this page exposes to the env-vars
# page via :data:`hyprmod.core.env_vars.RESERVED_NAMES` (re-exported
# above as ``_MANAGED_VARS``). Keep the per-flavour tuples a strict
# subset of ``_MANAGED_VARS`` if either side ever grows.
_THEME_VARS = ("XCURSOR_THEME", "HYPRCURSOR_THEME")
_SIZE_VARS = ("XCURSOR_SIZE", "HYPRCURSOR_SIZE")
_SYSTEM_DEFAULT = "__system_default__"
_THUMB_SIZE = 24
_DEFAULT_SIZE = 24


@dataclass
class _State:
    theme: str  # theme name, or _SYSTEM_DEFAULT
    size: int


class _ThemeItem(GObject.Object):
    """GObject wrapper so themes can live in a Gio.ListStore."""

    __gtype_name__ = "HyprmodCursorThemeItem"

    def __init__(self, theme: CursorTheme | None, *, missing_name: str = ""):
        super().__init__()
        self.theme = theme  # None for "System default" or missing themes
        self.missing_name = missing_name  # Name from config that's not installed


def _theme_label(item: "_ThemeItem") -> str:
    if item.missing_name:
        return f"{item.missing_name}  (not installed)"
    theme = item.theme
    if theme is None:
        return "System default"
    suffix = {
        (True, True): "  (XCursor + Hyprcursor)",
        (False, True): "  (Hyprcursor)",
    }.get((theme.has_xcursor, theme.has_hyprcursor), "")
    return theme.display_name + suffix


def _iter_env(sections: dict[str, list[str]]):
    """Yield (name, value) for each ``env = NAME,VALUE`` entry."""
    for raw in sections.get(config.KEYWORD_ENV, []):
        body = raw.split("=", 1)[1].strip() if "=" in raw else ""
        name, _, val = body.partition(",")
        yield name.strip(), val.strip()


def _run(*args: str) -> None:
    """Best-effort subprocess call (never raises)."""
    try:
        subprocess.run(args, check=False, capture_output=True, timeout=2)
    except (OSError, subprocess.SubprocessError):
        pass


class CursorPage(SectionPage):
    """Manages the cursor theme picker widget on the Cursor page."""

    def __init__(
        self,
        window,
        on_dirty_changed=None,
        push_undo=None,
        saved_sections: dict | None = None,
    ):
        super().__init__(window, on_dirty_changed, push_undo)
        self._themes: list[CursorTheme] = discover()
        self._thumb_cache: dict[str, Gdk.Texture | None] = {}

        self._baseline = self._parse_env(saved_sections or {})
        self._current = _State(self._baseline.theme, self._baseline.size)
        self._last_pushed = _State(self._current.theme, self._current.size)
        self._suspend_undo = False

        self._model: Gio.ListStore | None = None
        self._size_adjustment: Gtk.Adjustment | None = None
        self._size_spin: Gtk.SpinButton | None = None
        self._theme_row: Adw.ComboRow | None = None
        self._field: ManagedRow | None = None
        self._apply_timer_id: int = 0

    @staticmethod
    def _parse_env(sections: dict[str, list[str]]) -> _State:
        theme, size = _SYSTEM_DEFAULT, _DEFAULT_SIZE
        for name, val in _iter_env(sections):
            if name in _THEME_VARS and val:
                theme = val
            elif name in _SIZE_VARS and val.isdigit():
                size = int(val)
        return _State(theme, size)

    # ── Widget ──

    def build_widget(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            title="Theme",
            description=(
                "Applies instantly to Hyprland and GTK apps. "
                "Other apps pick up changes on relaunch."
            ),
        )

        self._model = Gio.ListStore.new(_ThemeItem)
        self._model.append(_ThemeItem(None))
        for theme in self._themes:
            self._model.append(_ThemeItem(theme))

        # If the saved theme isn't installed, keep a placeholder item so the
        # user can see what's configured and revert without losing the name.
        known = {t.name for t in self._themes}
        baseline = self._baseline.theme
        if baseline != _SYSTEM_DEFAULT and baseline not in known:
            self._model.append(_ThemeItem(None, missing_name=baseline))

        factory = Gtk.SignalListItemFactory()
        factory.connect("setup", self._factory_setup)
        factory.connect("bind", self._factory_bind)

        row = make_combo_row(
            "Cursor",
            subtitle="Theme and size",
            model=self._model,
            factory=factory,
            selected=self._index_for(self._current.theme),
        )
        row.connect("notify::selected", self._on_theme_selected)
        self._theme_row = row

        self._size_adjustment = Gtk.Adjustment(
            value=self._current.size, lower=8, upper=128, step_increment=1, page_increment=4
        )
        self._size_spin = Gtk.SpinButton(adjustment=self._size_adjustment, digits=0)
        self._size_spin.set_valign(Gtk.Align.CENTER)
        self._size_spin.set_sensitive(self._current.theme != _SYSTEM_DEFAULT)
        self._size_spin.set_tooltip_text("Cursor size (pixels)")
        self._size_spin.connect("value-changed", self._on_size_changed)
        row.add_suffix(self._size_spin)

        self._field = ManagedRow(
            row,
            default=(_SYSTEM_DEFAULT, _DEFAULT_SIZE),
            baseline=(self._baseline.theme, self._baseline.size),
            get_value=lambda: (self._current.theme, self._current.size),
            set_value_silent=self._set_state_silent,
            on_value_set=lambda _v: self._changed(),
        )
        group.add(row)

        return group

    def _index_for(self, theme_name: str) -> int:
        # Model has "System default" at index 0, installed themes at 1+,
        # and optionally a trailing placeholder for an uninstalled baseline.
        if theme_name == _SYSTEM_DEFAULT:
            return 0
        for i, t in enumerate(self._themes):
            if t.name == theme_name:
                return i + 1
        if self._model is not None:
            last = self._model.get_n_items() - 1
            if last > 0:
                item = cast(_ThemeItem, self._model.get_item(last))
                if item.missing_name == theme_name:
                    return last
        return 0

    # ── Factory (thumbnail + label) ──

    def _factory_setup(self, _factory, list_item):
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_valign(Gtk.Align.CENTER)
        image = Gtk.Image(pixel_size=_THUMB_SIZE, valign=Gtk.Align.CENTER)
        label = Gtk.Label(xalign=0.0, ellipsize=Pango.EllipsizeMode.END, valign=Gtk.Align.CENTER)
        box.append(image)
        box.append(label)
        list_item.set_child(box)

    def _factory_bind(self, _factory, list_item):
        item = cast(_ThemeItem, list_item.get_item())
        box = cast(Gtk.Box, list_item.get_child())
        image = cast(Gtk.Image, box.get_first_child())
        label = cast(Gtk.Label, image.get_next_sibling())
        label.set_label(_theme_label(item))
        texture = self._get_thumb(item.theme) if item.theme else None
        if texture is not None:
            image.set_from_paintable(texture)
        elif item.missing_name:
            image.set_from_icon_name("dialog-warning-symbolic")
        else:
            image.clear()

    def _get_thumb(self, theme: CursorTheme) -> Gdk.Texture | None:
        if theme.name in self._thumb_cache:
            return self._thumb_cache[theme.name]

        texture: Gdk.Texture | None = None
        img = load_pointer(theme.path, _THUMB_SIZE * 2)
        if img is not None:
            img = scale_nearest(pad_to_square(crop_to_content(img)), _THUMB_SIZE)
            texture = Gdk.MemoryTexture.new(
                img.width,
                img.height,
                Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                GLib.Bytes.new(img.bgra),
                img.width * 4,
            )
        self._thumb_cache[theme.name] = texture
        return texture

    # ── Silent setter (used by ManagedRow discard/reset + restore_snapshot) ──

    def _set_state_silent(self, value: tuple[str, int]) -> None:
        theme, size = value
        self._current = _State(theme, size)
        self._suspend_undo = True
        try:
            if self._theme_row is not None:
                self._theme_row.set_selected(self._index_for(theme))
            if self._size_adjustment is not None:
                self._size_adjustment.set_value(size)
        finally:
            self._suspend_undo = False
        if self._size_spin is not None:
            self._size_spin.set_sensitive(theme != _SYSTEM_DEFAULT)

    # ── Callbacks ──

    def _on_theme_selected(self, row, _pspec):
        if self._model is None:
            return
        item = cast(_ThemeItem, self._model.get_item(row.get_selected()))
        if item.theme:
            new_theme = item.theme.name
        elif item.missing_name:
            new_theme = item.missing_name
        else:
            new_theme = _SYSTEM_DEFAULT
        if new_theme == self._current.theme:
            return
        self._current.theme = new_theme
        if self._size_spin is not None:
            self._size_spin.set_sensitive(new_theme != _SYSTEM_DEFAULT)
        self._changed()

    def _on_size_changed(self, spin):
        new_size = int(spin.get_value())
        if new_size == self._current.size:
            return
        self._current.size = new_size
        self._changed()

    def _changed(self) -> None:
        if self._push_undo and not self._suspend_undo:
            self._push_undo(
                CursorUndoEntry(
                    old_theme=self._last_pushed.theme,
                    old_size=self._last_pushed.size,
                    new_theme=self._current.theme,
                    new_size=self._current.size,
                )
            )
            self._last_pushed = _State(self._current.theme, self._current.size)
        if self._field is not None:
            self._field.refresh()
        self._schedule_apply()
        self._notify_dirty()

    # ── Live apply (debounced) ──

    def _schedule_apply(self):
        if self._apply_timer_id:
            GLib.source_remove(self._apply_timer_id)
        self._apply_timer_id = GLib.timeout_add(150, self._apply_now)

    def _apply_now(self) -> bool:
        self._apply_timer_id = 0
        if self._current.theme == _SYSTEM_DEFAULT:
            return False
        theme, size = self._current.theme, str(self._current.size)
        _run("hyprctl", "setcursor", theme, size)
        _run("gsettings", "set", "org.gnome.desktop.interface", "cursor-theme", theme)
        _run("gsettings", "set", "org.gnome.desktop.interface", "cursor-size", size)
        # Hide then restore this window's cursor so Hyprland sees a shape
        # change and repaints with the just-applied theme.
        self._window.set_cursor(Gdk.Cursor.new_from_name("none", None))
        GLib.timeout_add(30, lambda: self._window.set_cursor(None) or False)
        return False

    # ── SectionPage protocol ──

    def is_dirty(self) -> bool:
        return self._current != self._baseline

    def mark_saved(self) -> None:
        self._baseline = _State(self._current.theme, self._current.size)
        self._last_pushed = _State(self._current.theme, self._current.size)
        if self._field is not None:
            self._field.set_baseline((self._baseline.theme, self._baseline.size))

    def discard(self) -> None:
        self.restore_snapshot(self._baseline.theme, self._baseline.size)

    def restore_snapshot(self, theme: str, size: int) -> None:
        """Set state + UI to (theme, size) without pushing an undo entry."""
        self._set_state_silent((theme, size))
        self._last_pushed = _State(theme, size)
        if self._field is not None:
            self._field.refresh()
        self._notify_dirty()
        self._schedule_apply()

    def reload_from_saved(self, saved_sections: dict[str, list[str]]) -> None:
        """Re-read baseline from config (e.g. after profile switch)."""
        self._baseline = self._parse_env(saved_sections)
        if self._field is not None:
            self._field.set_baseline((self._baseline.theme, self._baseline.size))
        self.restore_snapshot(self._baseline.theme, self._baseline.size)

    def get_env_lines(self) -> list[str]:
        """Return env= lines for the cursor vars.

        When a theme is set, size is always emitted alongside — otherwise apps
        that don't assume the same default size (e.g. JetBrains IDEs) render
        the cursor at their own fallback. When only size differs from the
        default, emit just ``XCURSOR_SIZE``.
        """
        theme_set = self._current.theme != _SYSTEM_DEFAULT
        size_set = self._current.size != _DEFAULT_SIZE
        if not theme_set and not size_set:
            return []

        theme = next((t for t in self._themes if t.name == self._current.theme), None)
        name, size = self._current.theme, str(self._current.size)
        want_xcursor = theme is None or theme.has_xcursor
        want_hyprcursor = theme is not None and theme.has_hyprcursor
        emit_size = theme_set or size_set

        lines: list[str] = []
        if theme_set and want_xcursor:
            lines.append(f"env = XCURSOR_THEME,{name}")
        if emit_size and want_xcursor:
            lines.append(f"env = XCURSOR_SIZE,{size}")
        if theme_set and want_hyprcursor:
            lines.append(f"env = HYPRCURSOR_THEME,{name}")
        if emit_size and want_hyprcursor:
            lines.append(f"env = HYPRCURSOR_SIZE,{size}")
        return lines

    @staticmethod
    def has_managed_env(sections: dict[str, list[str]]) -> bool:
        """True if config has any of our managed env vars."""
        return any(name in _MANAGED_VARS for name, _ in _iter_env(sections))

    @staticmethod
    def get_search_entries() -> list[dict]:
        """Entries for the global search page."""
        return [
            {
                "key": "cursor_theme",
                "label": "Cursor",
                "description": "Cursor theme and size",
                "_group_id": "cursor",
                "_group_label": "Cursor",
                "_section_label": "Theme",
            },
        ]
