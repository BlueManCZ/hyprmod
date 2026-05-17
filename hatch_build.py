"""Hatchling build hook.

Two jobs: compile the GSettings schema, and mirror repo-root data files
(``.desktop`` entry, AppStream metainfo) into ``hyprmod/data/`` so they
ship inside the wheel. The repo-root ``data/`` tree stays canonical for
distro packagers; the build hook just makes the same files available to
``hyprmod --install`` for pipx/uv users.
"""

import subprocess
from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    PLUGIN_NAME = "custom"

    def initialize(self, version, build_data):
        root = Path(self.root)
        pkg_data = root / "hyprmod" / "data"

        subprocess.run(["glib-compile-schemas", str(pkg_data)], check=True)
        self._bundle(build_data, pkg_data / "gschemas.compiled", "hyprmod/data/gschemas.compiled")

        for src in (root / "data" / "applications").glob("*.desktop"):
            self._bundle(build_data, src, f"hyprmod/data/applications/{src.name}")
        for src in (root / "data" / "metainfo").glob("*.xml"):
            self._bundle(build_data, src, f"hyprmod/data/metainfo/{src.name}")

    @staticmethod
    def _bundle(build_data, src: Path, wheel_path: str) -> None:
        build_data["force_include"][str(src)] = wheel_path
