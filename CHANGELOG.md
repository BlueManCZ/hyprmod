# Changelog

All notable changes to HyprMod will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Per-monitor "Identify by description" toggle — emits `monitor=desc:…` instead of `monitor=DP-1, …` so the saved configuration follows the physical monitor across port changes

## [0.2.0] - 2026-05-07

### Added

- Window Rules page — manage `windowrule` entries with a window picker, curated action dropdown, and live preview
- Layer Rules page — manage `layerrule` entries with curated presets and live preview
- Autostart page — manage `exec` and `exec-once` entries with an app picker
- Env Variables page — manage `env` entries with POSIX name validation
- Pending Changes page — review every unsaved edit across the app in one place
- Mouse-drag (`bindm`) keybinds with dispatcher, category, and preset support (#20)
- Profile cards show the last-modified date alongside the option count

### Changed

- Sidebar reorganized by task
- Dwindle, Master, and Scrolling merged into a single Layouts page
- Profiles page redesigned — active profile promoted to a hero card with the saved-profiles list below
- Saving keeps the active profile in sync automatically; use the save split button's "without updating profile" option to intentionally diverge

### Fixed

- Gradient border colors written without `0x` prefix on save, causing Hyprland to reject the config on reload (#21)
- Keybind recorder captured the shifted keysym (e.g. `exclam` for `Shift+1`) instead of the unshifted one Hyprland expects when `SHIFT` is in the modifier mask (#22)

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

[0.2.0]: https://github.com/BlueManCZ/hyprmod/releases/tag/v0.2.0
[0.1.0]: https://github.com/BlueManCZ/hyprmod/releases/tag/v0.1.0
