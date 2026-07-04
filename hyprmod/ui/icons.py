"""Shared icon-name constants for sidebar and pending-changes rows.

Both ``ui.sidebar`` and ``pages.pending`` need to render the same icon
for a given page (Keybinds, Monitors, Autostart, …). Keeping the
strings in one place means a future icon swap touches one line, not
every list that mirrors the sidebar.
"""

# Fixed (non-schema) sidebar pages. Pages whose group lives in the
# schema (general/decoration/etc.) read their icons from there at
# runtime; ``ANIMATIONS_ICON`` / ``CURSOR_ICON`` mirror the schema for
# section pages that want to surface their icon outside the sidebar
# (notably the pending-changes rows).
BINDS_ICON = "keyboard-shortcuts-symbolic"
MONITORS_ICON = "display-symbolic"
WORKSPACES_ICON = "view-paged-symbolic"
AUTOSTART_ICON = "media-playback-start-symbolic"
ENV_VARS_ICON = "utilities-terminal-symbolic"
WINDOW_RULES_ICON = "window-rules-symbolic"
LAYER_RULES_ICON = "overlapping-windows-symbolic"
LAYOUTS_ICON = "view-grid-symbolic"
ANIMATIONS_ICON = "bounce-symbolic"
CURSOR_ICON = "hyprmod-cursor-symbolic"
PLUGINS_ICON = "application-x-addon-symbolic"

PENDING_ICON = "view-list-symbolic"
PROFILES_ICON = "user-bookmarks-symbolic"
SETTINGS_ICON = "emblem-system-symbolic"

# Used by pages/pending.py when a change can't be matched to a known
# group_id (defensive — should not happen in practice).
FALLBACK_ICON = "preferences-system-symbolic"
