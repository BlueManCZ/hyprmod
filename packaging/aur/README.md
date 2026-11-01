# AUR update notes

The AUR package is maintained outside this repository. These are the changes
needed for the Hyprland 0.57 / HyprMod 0.6.0 release.

## Required changes

- Bump `pkgver` to `0.6.0` and `pkgrel` to `1`.
- Regenerate `sha256sums` after the `v0.6.0` tag exists.
- Add `lua` to `depends`: the Lua reader executes `lua` to parse
  `hyprland.lua`.
- Require the library versions that understand Hyprland 0.57:
  - `python-hyprland-config >= 0.9.20`
  - `python-hyprland-schema >= 0.7.3`
  - `python-hyprland-state >= 0.4.7`
- Keep the existing `gtk4`, `libadwaita`, `python-gobject`, and
  `python-hyprland-*` dependencies.

## Verify

```bash
makepkg -si
pacman -Q hyprmod
```

Open HyprMod on Hyprland 0.57 and confirm that the Decoration page shows
`Blur variant`, `Wobble`, and `Glow`, and that opening the app does not reload
the compositor.
