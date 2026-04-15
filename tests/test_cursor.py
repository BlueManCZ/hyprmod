"""Tests for cursor theme modules."""

from pathlib import Path

import pytest

from hyprmod.core import cursor_themes, xcursor


@pytest.fixture
def theme_tmpdir(tmp_path, monkeypatch):
    """Stub XDG_DATA_DIRS to an empty tmp dir so discovery is deterministic."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    monkeypatch.setenv("XDG_DATA_DIRS", str(data_dir))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".icons").mkdir(parents=True)
    return tmp_path


def _mk_theme(
    base: Path,
    name: str,
    *,
    xcursor: bool = False,
    hyprcursor: bool = False,
    display: str | None = None,
) -> Path:
    theme = base / name
    theme.mkdir()
    if xcursor:
        (theme / "cursors").mkdir()
        (theme / "cursors" / "default").write_bytes(b"")  # placeholder
    if hyprcursor:
        (theme / "manifest.hl").write_text("")
    if display:
        (theme / "index.theme").write_text(f"[Icon Theme]\nName={display}\n")
    return theme


class TestThemeDiscovery:
    def test_empty_dirs(self, theme_tmpdir):
        assert cursor_themes.discover() == []

    def test_classifies_xcursor(self, theme_tmpdir):
        _mk_theme(theme_tmpdir / "home" / ".icons", "my-theme", xcursor=True)
        themes = cursor_themes.discover()
        assert len(themes) == 1
        assert themes[0].name == "my-theme"
        assert themes[0].has_xcursor and not themes[0].has_hyprcursor

    def test_classifies_hyprcursor(self, theme_tmpdir):
        _mk_theme(theme_tmpdir / "home" / ".icons", "hy-theme", hyprcursor=True)
        themes = cursor_themes.discover()
        assert themes[0].has_hyprcursor and not themes[0].has_xcursor

    def test_display_name_from_index_theme(self, theme_tmpdir):
        _mk_theme(
            theme_tmpdir / "home" / ".icons",
            "fancy",
            xcursor=True,
            display="Fancy Cursors",
        )
        [theme] = cursor_themes.discover()
        assert theme.display_name == "Fancy Cursors"

    def test_dedupes_by_name(self, theme_tmpdir):
        (theme_tmpdir / "data" / "icons").mkdir(parents=True)
        _mk_theme(theme_tmpdir / "home" / ".icons", "dup", xcursor=True)
        _mk_theme(theme_tmpdir / "data" / "icons", "dup", xcursor=True)
        names = [t.name for t in cursor_themes.discover()]
        assert names.count("dup") == 1

    def test_skips_non_cursor_dirs(self, theme_tmpdir):
        plain = theme_tmpdir / "home" / ".icons" / "plain"
        plain.mkdir()
        (plain / "16x16").mkdir()  # regular icon theme, no cursors
        assert cursor_themes.discover() == []


class TestXcursorTransforms:
    def _img(self, w: int, h: int, fill: tuple[int, int, int, int] = (0, 0, 0, 255)):
        b, g, r, a = fill
        return xcursor.CursorImage(w, h, 0, bytes([b, g, r, a] * (w * h)))

    def test_crop_removes_transparent_border(self):
        # 4x4 image with opaque 2x2 center
        w = h = 4
        pixels = bytearray(w * h * 4)
        for y in (1, 2):
            for x in (1, 2):
                pixels[(y * w + x) * 4 : (y * w + x) * 4 + 4] = b"\x00\x00\x00\xff"
        img = xcursor.CursorImage(w, h, 0, bytes(pixels))
        cropped = xcursor.crop_to_content(img)
        assert (cropped.width, cropped.height) == (2, 2)

    def test_crop_all_transparent_returns_input(self):
        img = self._img(4, 4, fill=(0, 0, 0, 0))
        assert xcursor.crop_to_content(img) is img

    def test_pad_to_square(self):
        img = self._img(3, 5)
        padded = xcursor.pad_to_square(img)
        assert padded.width == padded.height == 5

    def test_pad_square_noop(self):
        img = self._img(4, 4)
        assert xcursor.pad_to_square(img) is img

    def test_scale_nearest_target_size(self):
        img = self._img(7, 7)
        scaled = xcursor.scale_nearest(img, 3)
        assert (scaled.width, scaled.height) == (3, 3)
        assert len(scaled.bgra) == 3 * 3 * 4

    def test_pick_closest_prefers_ge(self):
        imgs = [
            xcursor.CursorImage(10, 10, 10, b""),
            xcursor.CursorImage(20, 20, 20, b""),
            xcursor.CursorImage(40, 40, 40, b""),
        ]
        chosen = xcursor.pick_closest(imgs, 15)
        assert chosen is not None
        assert chosen.nominal_size == 20

    def test_pick_closest_falls_back_below(self):
        imgs = [xcursor.CursorImage(10, 10, 10, b"")]
        chosen = xcursor.pick_closest(imgs, 100)
        assert chosen is not None
        assert chosen.nominal_size == 10

    def test_pick_closest_empty(self):
        assert xcursor.pick_closest([], 16) is None


class TestCursorPageEnv:
    """Test the env parsing and env-line emission logic without building a GTK widget."""

    def test_parse_env_reads_xcursor(self):
        from hyprmod.pages.cursor import CursorPage

        sections = {"env": ["env = XCURSOR_THEME,Adwaita", "env = XCURSOR_SIZE,32"]}
        state = CursorPage._parse_env(sections)
        assert state.theme == "Adwaita"
        assert state.size == 32

    def test_parse_env_defaults_when_missing(self):
        from hyprmod.pages.cursor import _SYSTEM_DEFAULT, CursorPage

        state = CursorPage._parse_env({})
        assert state.theme == _SYSTEM_DEFAULT

    def test_parse_env_ignores_unrelated(self):
        from hyprmod.pages.cursor import CursorPage

        sections = {"env": ["env = EDITOR,vim", "env = HYPRCURSOR_THEME,Bibata"]}
        state = CursorPage._parse_env(sections)
        assert state.theme == "Bibata"

    def test_has_managed_env_true(self):
        from hyprmod.pages.cursor import CursorPage

        assert CursorPage.has_managed_env({"env": ["env = XCURSOR_SIZE,24"]})

    def test_has_managed_env_false(self):
        from hyprmod.pages.cursor import CursorPage

        assert not CursorPage.has_managed_env({"env": ["env = EDITOR,vim"]})


@pytest.fixture
def cursor_page(theme_tmpdir, monkeypatch):
    """CursorPage constructed without a GTK window (widget not built)."""
    from hyprmod.core import cursor_themes as ct_mod
    from hyprmod.pages.cursor import CursorPage

    themes = [
        ct_mod.CursorTheme(
            name="Adwaita",
            display_name="Adwaita",
            path=theme_tmpdir / "Adwaita",
            has_xcursor=True,
            has_hyprcursor=False,
        ),
        ct_mod.CursorTheme(
            name="Bibata",
            display_name="Bibata",
            path=theme_tmpdir / "Bibata",
            has_xcursor=True,
            has_hyprcursor=True,
        ),
    ]
    monkeypatch.setattr("hyprmod.pages.cursor.discover", lambda: themes)
    return CursorPage(window=None, saved_sections={})


class TestGetEnvLines:
    def test_empty_when_at_defaults(self, cursor_page):
        assert cursor_page.get_env_lines() == []

    def test_theme_pulls_default_size_along(self, cursor_page):
        # When a theme is set, XCURSOR_SIZE is always emitted so apps that
        # don't share our default (e.g. JetBrains IDEs) render consistently.
        cursor_page._current.theme = "Adwaita"
        assert cursor_page.get_env_lines() == [
            "env = XCURSOR_THEME,Adwaita",
            "env = XCURSOR_SIZE,24",
        ]

    def test_only_size_when_theme_default(self, cursor_page):
        # size_set without theme_set still emits XCURSOR_SIZE (want_xcursor is True for None theme)
        cursor_page._current.size = 32
        assert cursor_page.get_env_lines() == ["env = XCURSOR_SIZE,32"]

    def test_both_when_overridden(self, cursor_page):
        cursor_page._current.theme = "Adwaita"
        cursor_page._current.size = 32
        assert cursor_page.get_env_lines() == [
            "env = XCURSOR_THEME,Adwaita",
            "env = XCURSOR_SIZE,32",
        ]

    def test_hyprcursor_dual_theme(self, cursor_page):
        cursor_page._current.theme = "Bibata"
        cursor_page._current.size = 32
        assert cursor_page.get_env_lines() == [
            "env = XCURSOR_THEME,Bibata",
            "env = XCURSOR_SIZE,32",
            "env = HYPRCURSOR_THEME,Bibata",
            "env = HYPRCURSOR_SIZE,32",
        ]


class TestSearchEntries:
    def test_entries_present(self):
        from hyprmod.pages.cursor import CursorPage

        entries = CursorPage.get_search_entries()
        assert len(entries) >= 1
        for e in entries:
            assert e["_group_id"] == "cursor"


class TestCursorUndoMerge:
    def test_consecutive_entries_merge(self):
        from hyprmod.core.undo import CursorUndoEntry, UndoManager

        mgr = UndoManager()
        mgr.push(CursorUndoEntry("sys", 24, "Adwaita", 24))
        mgr.push(CursorUndoEntry("Adwaita", 24, "Adwaita", 32))
        assert len(mgr._undo_stack) == 1
        merged = mgr._undo_stack[0]
        assert isinstance(merged, CursorUndoEntry)
        assert merged.old_theme == "sys" and merged.old_size == 24
        assert merged.new_theme == "Adwaita" and merged.new_size == 32

    def test_merge_collapses_to_noop(self):
        from hyprmod.core.undo import CursorUndoEntry, UndoManager

        mgr = UndoManager()
        mgr.push(CursorUndoEntry("sys", 24, "Adwaita", 32))
        mgr.push(CursorUndoEntry("Adwaita", 32, "sys", 24))
        assert len(mgr._undo_stack) == 0
