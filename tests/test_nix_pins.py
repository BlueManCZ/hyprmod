"""Verify that the Nix expression pins the dependency versions pyproject requires.

``nix/hyprmod.nix`` pins each ``hyprland-*`` library to an exact version.
Bumping a floor in ``pyproject.toml`` without rerunning the update script
(see ``NIX.md``) leaves the flake building against a version older than the
code needs, and nothing else in CI builds the flake.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent

NIX_PIN = re.compile(r'pname = "(hyprland-[\w-]+)";\s*version = "([^"]+)";')


def _pyproject_floors() -> dict[str, str]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    return dict(dep.split(">=") for dep in dependencies if dep.startswith("hyprland-"))


def _nix_pins() -> dict[str, str]:
    return dict(NIX_PIN.findall((ROOT / "nix" / "hyprmod.nix").read_text()))


class TestNixPins:
    def test_pins_match_pyproject_floors(self):
        assert _nix_pins() == _pyproject_floors()
