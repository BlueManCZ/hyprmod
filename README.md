# HyprMod

A native GTK4/libadwaita settings app for [Hyprland](https://hyprland.org) — tweak any option, see it change live, save when you're happy.

[![CI](https://github.com/BlueManCZ/hyprmod/actions/workflows/ci.yml/badge.svg)](https://github.com/BlueManCZ/hyprmod/actions/workflows/ci.yml)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![AUR](https://img.shields.io/aur/version/hyprmod?label=AUR)](https://aur.archlinux.org/packages/hyprmod)

<p>
  <img src="data/screenshots/monitors.png" width="49%" alt="Monitor configuration page with a multi-monitor layout preview">
  <img src="data/screenshots/curves.png" width="49%" alt="Bezier curve editor with control points and live animation preview">
</p>

## 🎬 Wall of Video Fame

Huge thanks to the creators below — without you, HyprMod would reach far fewer people. ❤️

<a href="https://youtu.be/PF3qgfR0XP0" target="_blank">
  <img src="https://img.youtube.com/vi/PF3qgfR0XP0/maxresdefault.jpg" alt="Finally a Settings App for Hyprland — by saneAspect" width="24%">
</a>
<a href="https://youtu.be/tvjYWT2LOMk" target="_blank">
  <img src="https://img.youtube.com/vi/tvjYWT2LOMk/maxresdefault.jpg" alt="New GUI Settings For Hyprland!! Game Changer! | HyprMod — by TheBlackDon" width="24%">
</a>
<a href="https://youtu.be/61M_uI4kTo0" target="_blank">
  <img src="https://img.youtube.com/vi/61M_uI4kTo0/maxresdefault.jpg" alt="Hyprmod: The Game-Changing Settings Tool Every Hyprland Beginner Needs — by Mattscreative" width="24%">
</a>
<a href="https://youtu.be/8CzY9Qihip8" target="_blank">
  <img src="https://img.youtube.com/vi/8CzY9Qihip8/maxresdefault.jpg" alt="FIZERAM uma CENTRAL DE CONFIG PRO HYPRLAND (e tá insano de bom kkk) — by Raell Tech" width="24%">
</a>

## ⚡ Highlights

- **Live preview** — changes apply instantly to your running compositor
- **Your config stays untouched** — HyprMod writes only to its own `hyprland-gui.conf`
- **Undo with Ctrl+Z** — step back one change at a time
- **Profiles** — save, name, and share complete configurations as `.conf` files

## ✨ Features

- **Lua config support** — migrate and edit `hyprland.lua` directly (Hyprland 0.55+)
- Bezier curve editor with live animation preview
- Monitor layout editor with VRR, 10-bit detection, and advanced HDR controls with safe defaults
- Keybind editor with interactive key capture, including mouse-drag (`bindm`) binds
- Window rules, layer rules, and workspace rules editors with live preview
- Autostart (`exec` / `exec-once`) and environment variable management
- Pending Changes page — review every unsaved edit before saving
- Cursor theme picker with live preview
- Config DNA — a unique visual fingerprint per profile
- Global search across all options (Ctrl+F)

## 📦 Installation

> HyprMod is in active development and not yet packaged for most distributions.

Requires Python 3.12+, GTK4, and libadwaita.

**Arch Linux** — [`hyprmod`](https://aur.archlinux.org/packages/hyprmod) on the AUR:

```bash
yay -S hyprmod
```

**Gentoo** — via the [`edgets`](https://github.com/BlueManCZ/edgets) overlay:

```bash
emerge -a hyprmod
```

**Other distributions** — one-line installer (auto-detects [`uv`](https://docs.astral.sh/uv) or [`pipx`](https://pipx.pypa.io), bootstraps `uv` if missing):

```bash
# install
curl -LsSf https://raw.githubusercontent.com/BlueManCZ/hyprmod/main/install.sh | sh

# uninstall
curl -LsSf https://raw.githubusercontent.com/BlueManCZ/hyprmod/main/install.sh | sh -s -- --uninstall
```

<details>
<summary>Drive the install yourself</summary>

```bash
# with uv
uv tool install git+https://github.com/BlueManCZ/hyprmod.git
hyprmod --install     # places .desktop + icon under ~/.local/share

# or with pipx
pipx install git+https://github.com/BlueManCZ/hyprmod.git
hyprmod --install
```

To remove: `hyprmod --uninstall`, then `uv tool uninstall hyprmod` or `pipx uninstall hyprmod`. `hyprmod --install` is idempotent and runs automatically on first launch if no desktop entry is visible yet.
</details>

Running from a checkout? See [CONTRIBUTING.md](CONTRIBUTING.md).

## 🗺️ Roadmap

**Next**
- Automatic backups on save, with a history browser
- Translations (gettext)

**Later**
- Pages for the hypr* ecosystem — hyprpaper, hypridle, hyprlock
- Plugin manager (`hyprpm`)
- Command-line interface — `hyprmod profile apply <name>`

**Out of scope** — Wi-Fi, Bluetooth, printing, default apps, and GTK theming belong in a desktop control center, not a Hyprland settings app. See [#15](https://github.com/BlueManCZ/hyprmod/issues/15) for the reasoning.

## 🤝 Contributing

PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, the checks CI runs, and scope notes. For larger changes, please open an issue first so we can discuss the approach.

<a href="https://www.star-history.com/?repos=BlueManCZ%2Fhyprmod&type=date&legend=top-left">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/chart?repos=BlueManCZ/hyprmod&type=date&theme=dark&legend=top-left" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/chart?repos=BlueManCZ/hyprmod&type=date&legend=top-left" />
   <img alt="Star History Chart" src="https://api.star-history.com/chart?repos=BlueManCZ/hyprmod&type=date&legend=top-left" />
 </picture>
</a>
