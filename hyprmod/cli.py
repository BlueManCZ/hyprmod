"""Headless profile commands for the ``hyprmod`` CLI.

Running ``hyprmod`` with no recognised subcommand launches the GTK app.
``hyprmod profile …`` lists and switches saved profiles without opening
the window, so switching can be bound to a keybind or scripted
(discussion #50).
"""

import argparse
import sys

from hyprland_state import HyprlandState

from hyprmod.core import profiles
from hyprmod.core.settings import apply_saved_config_path, open_settings


def _list_profiles() -> int:
    profile_list, active_id = profiles.list_profiles_and_active()
    if not profile_list:
        print("No profiles saved.")
        return 0
    for profile in profile_list:
        marker = "* " if profile["id"] == active_id else "  "
        print(f"{marker}{profile['name']}")
    return 0


def _activate(profile_id: str, name: str) -> int:
    hypr = HyprlandState()
    if not profiles.activate(profile_id, hypr):
        print(f"hyprmod: profile {name!r} has no saved snapshot", file=sys.stderr)
        return 1

    if hypr.online:
        print(f"Switched to {name!r}.")
    else:
        print(f"Switched to {name!r}. Hyprland is not running; applies on next launch.")
    return 0


def _apply_profile(name: str) -> int:
    # Honour a custom config-path before resolving the managed file, so the
    # CLI writes to the same place the GUI does.
    apply_saved_config_path(open_settings())

    profile_list, _ = profiles.list_profiles_and_active()
    profile = profiles.find_by_name(profile_list, name)
    if profile is None:
        print(f"hyprmod: no profile named {name!r}", file=sys.stderr)
        return 1
    return _activate(profile["id"], profile["name"])


def _cycle_profile(*, forward: bool) -> int:
    apply_saved_config_path(open_settings())

    profile_list, active_id = profiles.list_profiles_and_active()
    target_id = profiles.adjacent_id(profile_list, active_id, forward=forward)
    if target_id is None:
        print("hyprmod: no profiles saved", file=sys.stderr)
        return 1

    target = next(p for p in profile_list if p["id"] == target_id)
    if target_id == active_id:
        # Single profile, already active: nothing to cycle to.
        print(f"{target['name']!r} is the only profile.")
        return 0
    return _activate(target_id, target["name"])


def run(argv: list[str]) -> int:
    """Dispatch a recognised CLI command. *argv* excludes the program name."""
    parser = argparse.ArgumentParser(
        prog="hyprmod", description="Manage Hyprland configuration profiles."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    profile = commands.add_parser("profile", help="List and switch configuration profiles")
    profile_commands = profile.add_subparsers(dest="profile_command", required=True)
    profile_commands.add_parser("list", help="List saved profiles (active marked with *)")
    apply_command = profile_commands.add_parser("apply", help="Switch to a saved profile")
    apply_command.add_argument("name", help="Profile name (case-insensitive)")
    profile_commands.add_parser("next", help="Switch to the next profile (alphabetical, wraps)")
    profile_commands.add_parser(
        "previous", aliases=["prev"], help="Switch to the previous profile (alphabetical, wraps)"
    )

    args = parser.parse_args(argv)
    if args.profile_command == "list":
        return _list_profiles()
    if args.profile_command == "apply":
        return _apply_profile(args.name)
    return _cycle_profile(forward=args.profile_command == "next")
