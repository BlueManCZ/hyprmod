"""Tests for desktop-entry field-code stripping.

The full ``list_apps()`` path goes through ``Gio.AppInfo.get_all()`` and
returns whatever apps happen to be installed — not stable enough to
unit-test directly. We test the pure helpers and dataclass instead.
"""

import pytest

from hyprmod.core.desktop_apps import DesktopApp, match_command, strip_field_codes


class TestStripFieldCodes:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Trailing field codes — the most common case for real .desktop files.
            ("firefox %u", "firefox"),
            ("firefox %U", "firefox"),
            ("nautilus %F", "nautilus"),
            ("smplayer %f", "smplayer"),
            ("vlc --started-from-file %U", "vlc --started-from-file"),
            # Internal field codes (rarer, but spec-legal).
            ("wineconsole --backend=user %s %f", "wineconsole --backend=user %s"),
            # All deprecated codes — d, D, n, N, m, v.
            ("oldapp %d", "oldapp"),
            ("oldapp %D", "oldapp"),
            ("oldapp %n", "oldapp"),
            ("oldapp %N", "oldapp"),
            ("oldapp %v", "oldapp"),
            ("oldapp %m", "oldapp"),
            # Currently-spec'd codes from §B of the Desktop Entry spec.
            ("withicon %i app", "withicon app"),
            ("transname %c app", "transname app"),
            ("location %k app", "location app"),
            # Multiple codes.
            ("multi %f %u %U", "multi"),
            # Surrounding whitespace is collapsed.
            ("  spaced  %u  ", "spaced"),
        ],
    )
    def test_strips_codes(self, raw: str, expected: str):
        assert strip_field_codes(raw) == expected

    def test_preserves_literal_double_percent(self):
        # %% is the escape for a literal '%' and must survive stripping.
        assert strip_field_codes("printf %%s %u") == "printf %%s"
        assert strip_field_codes("echo %% hello") == "echo %% hello"

    def test_preserves_unrelated_text(self):
        # Field codes are *only* the spec-defined letters; everything
        # else passes through untouched, including unusual characters.
        assert strip_field_codes("script.sh --foo='bar baz' %U") == "script.sh --foo='bar baz'"

    def test_unknown_letter_after_percent_is_kept(self):
        # ``%s`` and ``%x`` aren't standard field codes — leave them
        # alone rather than guessing. (Real .desktop files rarely
        # contain non-spec codes, but the regex shouldn't be greedy.)
        assert strip_field_codes("foo %s bar") == "foo %s bar"
        assert strip_field_codes("foo %x bar") == "foo %x bar"

    def test_empty_returns_empty(self):
        assert strip_field_codes("") == ""
        assert strip_field_codes("   ") == ""

    def test_only_a_field_code_returns_empty(self):
        assert strip_field_codes("%U") == ""
        assert strip_field_codes("  %U  ") == ""


class TestDesktopApp:
    def test_is_frozen(self):
        app = DesktopApp(
            id="x.desktop",
            name="X",
            description="",
            icon_name="",
            command="x",
        )
        with pytest.raises((AttributeError, TypeError)):
            app.command = "y"  # type: ignore[misc]

    def test_equality_uses_all_fields(self):
        a = DesktopApp(id="x", name="X", description="", icon_name="", command="x")
        b = DesktopApp(id="x", name="X", description="", icon_name="", command="x")
        c = DesktopApp(id="x", name="X", description="", icon_name="", command="y")
        assert a == b
        assert a != c


# ---------------------------------------------------------------------------
# match_command
# ---------------------------------------------------------------------------


def _app(name: str, command: str, icon: str = "") -> DesktopApp:
    return DesktopApp(
        id=f"{name.lower()}.desktop",
        name=name,
        description="",
        icon_name=icon,
        command=command,
    )


@pytest.fixture
def apps() -> list[DesktopApp]:
    """A handful of representative apps for matching tests."""
    return [
        _app("Firefox", "/usr/lib/firefox/firefox", icon="firefox"),
        _app(
            "Google Chrome",
            "/usr/bin/google-chrome-stable --enable-features=UseOzonePlatform",
            icon="google-chrome",
        ),
        _app("Waybar", "waybar", icon="waybar"),
        _app("VLC media player", "/usr/bin/vlc --started-from-file", icon="vlc"),
    ]


class TestMatchCommand:
    def test_exact_match_wins(self, apps):
        # The picker stores the canonical stripped command; a saved
        # entry that matches it byte-for-byte should resolve cleanly.
        result = match_command(
            "/usr/bin/google-chrome-stable --enable-features=UseOzonePlatform",
            apps,
        )
        assert result is not None
        assert result.name == "Google Chrome"

    def test_basename_fallback_matches_typed_binary(self, apps):
        # User typed just the binary name — should still resolve to
        # the installed app by basename of the first token.
        result = match_command("firefox", apps)
        assert result is not None
        assert result.name == "Firefox"

    def test_basename_match_strips_path(self, apps):
        # ``/usr/bin/firefox`` should resolve to the Firefox app even
        # though the app's command lives under ``/usr/lib/firefox/``.
        result = match_command("/usr/bin/firefox", apps)
        assert result is not None
        assert result.name == "Firefox"

    def test_basename_match_preserves_trailing_args(self, apps):
        # User typed ``firefox --new-window`` — first token still
        # matches even though the full command differs from the app's.
        result = match_command("firefox --new-window https://example.com", apps)
        assert result is not None
        assert result.name == "Firefox"

    def test_no_match_returns_none(self, apps):
        assert match_command("nonexistent-app", apps) is None
        assert match_command("/path/to/script.sh --foo", apps) is None

    def test_empty_command_returns_none(self, apps):
        assert match_command("", apps) is None
        assert match_command("   ", apps) is None

    def test_unparseable_command_falls_through(self, apps):
        # shlex.split fails on unmatched quotes — we should bail out
        # rather than false-match on a partial parse.
        assert match_command('foo "unclosed', apps) is None

    def test_exact_match_takes_priority_over_basename(self):
        # Two apps share a basename; an exact command match should
        # win deterministically over a tier-2 basename collision.
        apps = [
            _app("Firefox Stable", "firefox --profile-stable"),
            _app("Firefox", "firefox"),
        ]
        result = match_command("firefox", apps)
        assert result is not None
        assert result.name == "Firefox"

    def test_empty_apps_list_returns_none(self):
        assert match_command("firefox", []) is None
