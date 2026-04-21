# Changelog

All notable changes to HyprMod will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-04-21

Initial release.

### Added

- Native GTK4/libadwaita settings app for Hyprland with live preview via IPC
- Config isolation — HyprMod writes only to its own `hyprland-gui.conf`; the user's `hyprland.conf` is never modified
- Undo/redo with Ctrl+Z
- Profiles — save, name, and share complete configurations as `.conf` files
- Config DNA — a unique visual fingerprint per profile
- Bezier curve editor with draggable control points, live animation preview, and a preset library
- Monitor configuration with per-monitor resolution, refresh rate, position, scale, transform, and mirroring. VRR, HDR, and 10-bit detection
- Keybind editor with modifier toggles, interactive key capture, and dispatcher selection
- Cursor theme picker with live previews
- Master, Dwindle, and Scrolling layout options
- Global search across all options (Ctrl+F) with highlight-pulse navigation
- Configurable config path and an auto-save toggle
- About dialog with version info and debug details
- Keyboard shortcuts overlay
- In-app link to report issues on GitHub
- Version-aware schema resolution — loads the option catalog matching the running Hyprland version, falling back to the bundled schema on mismatch
- Automatic migration of deprecated Hyprland syntax on save
- Desktop integration: application icon, `.desktop` file, and AppStream metainfo
