# Contributing to HyprMod

Thanks for your interest in contributing! HyprMod is a growing project and PRs are welcome.

## Development setup

System dependencies (Debian/Ubuntu names; adapt for your distro):

```
libcairo2-dev libgirepository-2.0-dev libgtk-4-dev libadwaita-1-dev gir1.2-gnomedesktop-4.0
```

Then:

```bash
git clone https://github.com/BlueManCZ/hyprmod.git
cd hyprmod
uv sync
uv run hyprmod
```

Requires Python 3.12+ and a running Hyprland instance for full manual testing.

## Before submitting a PR

Run the same four checks that CI runs:

```bash
uv run ruff check --fix hyprmod/ tests/
uv run ruff format hyprmod/ tests/
uv run pyright hyprmod/ tests/
uv run pytest tests/ -v
```

All four must pass before your PR can be merged.

## Code style

- Ruff enforces `E`, `F`, `W`, `I` rules with a line length of 100. Always run with `--fix`.
- Pyright must pass clean. Don't use `assert` for type narrowing — restructure the code instead.
- Follow existing patterns for option rows, pages, and widgets rather than inventing new ones.

## Scope

- Check the [Roadmap](README.md#-roadmap) before proposing new features.
- For larger changes, open an issue first so we can discuss the approach.
- System settings (Wi-Fi, Bluetooth, theming, printing, etc.) are out of scope — see [#15](https://github.com/BlueManCZ/hyprmod/issues/15).

## The `hyprland-*` library stack

Parsing, IPC, schema, and state logic live in separate repositories under [BlueManCZ](https://github.com/BlueManCZ):

- [`hyprland-config`](https://github.com/BlueManCZ/hyprland-config) — round-trip parser
- [`hyprland-socket`](https://github.com/BlueManCZ/hyprland-socket) — typed IPC client
- [`hyprland-schema`](https://github.com/BlueManCZ/hyprland-schema) — versioned option catalog
- [`hyprland-state`](https://github.com/BlueManCZ/hyprland-state) — unified high-level API
- [`hyprland-monitors`](https://github.com/BlueManCZ/hyprland-monitors) — scale/geometry/EDID utilities
- [`hyprland-events`](https://github.com/BlueManCZ/hyprland-events) — typed event dispatch

If your change belongs in one of those libraries (parsing, IPC, schema data, etc.), please open the PR in that repository instead.

## Reporting bugs

Open a [GitHub issue](https://github.com/BlueManCZ/hyprmod/issues) and include:

- Hyprland version (`hyprctl version`)
- Steps to reproduce
- Relevant log output, if any
