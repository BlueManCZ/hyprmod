"""Discovery of installed ``.desktop`` applications for the autostart picker.

Wraps ``Gio.AppInfo.get_all()`` and exposes a clean dataclass list with
placeholder-stripped commands ready to feed into Hyprland's ``exec*``
keywords. Pure helpers (placeholder stripping) live alongside so they
can be unit-tested without a GTK environment.

The XDG Desktop Entry spec defines a handful of ``%X`` field codes that
launchers substitute at run time (``%U`` = list of URIs, ``%f`` = a
single file path, etc.). Hyprland's ``exec`` keyword passes its argument
straight to ``/bin/sh -c``, so any unsubstituted code would either be
silently swallowed or break the command. We strip them all.
"""

import os
import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass

from gi.repository import Gio

# Per the Desktop Entry spec, field codes are a single ``%`` followed by
# one of ``f F u U d D n N i c k v m`` (lowercase or upper). ``%%`` is the
# literal ``%`` and *must* be preserved — handled by negating the lookahead.
# We also swallow surrounding whitespace so stripping doesn't leave double
# spaces in the middle of commands.
_FIELD_CODE = re.compile(r"\s*(?<!%)%[fFuUdDnNickvm]\s*")


@dataclass(slots=True, frozen=True)
class DesktopApp:
    """A single installed application, ready to be added to autostart."""

    id: str  # ``Gio.AppInfo.get_id()`` — e.g. ``"firefox.desktop"``
    name: str
    description: str  # may be empty if the .desktop file has no Comment
    icon_name: str  # may be empty if no icon is set
    command: str  # field codes stripped, ready to feed Hyprland


def list_apps() -> list[DesktopApp]:
    """Return all installed apps the user should be able to pick from.

    Filters:

    - ``NoDisplay`` / ``Hidden`` entries (handled by ``should_show()``)
    - duplicate IDs (rare, but possible across XDG_DATA_DIRS)
    - entries with no ``Exec`` line, since there'd be nothing to run

    Sorted alphabetically by display name (case-insensitive).
    """
    seen: set[str] = set()
    result: list[DesktopApp] = []
    for app in Gio.AppInfo.get_all():
        app_id = app.get_id() or ""
        if app_id in seen:
            continue
        if not app.should_show():
            continue
        cmdline = app.get_commandline() or ""
        command = strip_field_codes(cmdline)
        if not command:
            continue
        seen.add(app_id)
        result.append(
            DesktopApp(
                id=app_id,
                name=app.get_name() or app_id,
                description=app.get_description() or "",
                icon_name=_icon_name(app.get_icon()),
                command=command,
            )
        )
    result.sort(key=lambda a: a.name.lower())
    return result


def strip_field_codes(cmdline: str) -> str:
    """Remove XDG Desktop Entry field codes (``%U``, ``%f``, …) from ``cmdline``.

    ``%%`` (a literal percent) is preserved. Surrounding whitespace is
    swallowed along with the code so we don't leave double spaces, and
    the result is right-trimmed.
    """
    return _FIELD_CODE.sub(" ", cmdline).strip()


def _icon_name(icon: object) -> str:
    """Best-effort icon-name extraction from a ``GIcon``.

    Themed icons return their name directly via ``to_string()``; file
    icons return the path. Either is acceptable input for
    ``Gtk.Image.new_from_icon_name`` (file paths fall through to a
    generic fallback if the theme can't resolve them).
    """
    if icon is None:
        return ""
    to_string = getattr(icon, "to_string", None)
    if to_string is None:
        return ""
    return to_string() or ""


def match_command(command: str, apps: Iterable[DesktopApp]) -> DesktopApp | None:
    """Match an arbitrary command string against a list of installed apps.

    Two-tier lookup:

    1. **Exact stripped-command match** — what the picker produces
       (e.g. ``/usr/bin/google-chrome-stable --enable-features=…``).
    2. **Basename match on the first token** — covers the case where
       the user typed ``firefox`` or ``/usr/bin/firefox`` manually and
       we want to recognise it as the installed Firefox app. ``shlex``
       handles quoted arguments correctly; ``os.path.basename`` so
       absolute and bare paths both resolve.

    Returns ``None`` if neither tier matches.

    Pure: takes the apps list explicitly so callers can cache discovery
    once (``list_apps()`` walks the whole XDG tree) and reuse the
    result across many lookups.
    """
    if not command.strip():
        return None
    apps_list = list(apps)

    # Tier 1: exact match on the full stripped command.
    for app in apps_list:
        if app.command == command:
            return app

    # Tier 2: basename of the first token. We bail if shlex can't
    # parse the command (e.g. unmatched quote) — better to show the
    # raw command in the row than to false-match on a partial parse.
    target = _first_token_basename(command)
    if not target:
        return None
    for app in apps_list:
        if _first_token_basename(app.command) == target:
            return app
    return None


def _first_token_basename(command: str) -> str:
    """Basename of the first token of *command*, or ``""`` if unparseable."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        return ""
    if not tokens:
        return ""
    return os.path.basename(tokens[0])
