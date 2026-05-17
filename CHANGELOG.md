# Changelog

All notable changes to HyprMod will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Lua config support — read and edit `hyprland.lua` directly, with live preview against Hyprland 0.55+'s Lua runtime
- "Migrate to Lua…" wizard and main-window banner (Hyprland 0.55+) — converts your active `hyprland.conf` to `hyprland.lua`, rewrites the entrypoint include, and is dismissible
- Per-monitor "Identify by description" toggle — emits `monitor=desc:…` instead of `monitor=DP-1, …` so the saved configuration follows the physical monitor across port changes (#26)
- Per-monitor HDR controls — expanded Color Management presets (Auto/sRGB/Adobe/Wide/EDID/HDR/HDR EDID) and new SDR Brightness / SDR Saturation options that appear only when an HDR preset is active (#29)
- Workspaces page — manage `workspace` rules with live preview and live IPC apply (#31)
- Deprecation assistant — detect and migrate deprecated Hyprland syntax with explicit user confirmation and timestamped backups
- Convenience installer (`install.sh`) and `hyprmod --install` / `--uninstall` flags — `pipx`/`uv tool` installs now register a desktop launcher entry and icon under `$XDG_DATA_HOME`, with first-launch self-registration as a fallback

### Changed

- Bind dialog's Manual Edit modifier picker is a compact two-row chip strip instead of stacked switch rows
- Options unavailable on the running Hyprland version are hidden instead of greyed-out; group headers collapse when every row in the group is unavailable
- Profiles transcode on activation — saving in Hyprlang then activating in Lua (or vice-versa) just works
- HyprMod no longer auto-migrates deprecated syntax silently on save; migrations are surfaced via the new deprecation assistant and require explicit user confirmation

### Fixed

- Keybinds using the Hyper modifier (and `CAPS`/`MOD2`/`MOD3`/`MOD5`) are now displayed and recorded correctly — previously `Caps Lock + G` under `caps:hyper` rendered as `+ G` and Record captured only `G` (#27)
- Toggling "Numlock by default" wrote an invalid `input:kb_numlock` option that Hyprland rejected on reload; the option is now written as `input:numlock_by_default` per the schema (#34)
- Rules from your managed config no longer leak into the read-only "external rules" list when `hyprland-gui.conf` is reached through a symlinked path (typical dotfiles setup)

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
