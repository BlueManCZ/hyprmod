"""Verify that the Nix expression pins the dependency versions pyproject requires.

``nix/hyprmod.nix`` pins each ``hyprland-*`` library to an exact version and
source hash. Bumping a floor in ``pyproject.toml`` without rerunning the update
script (see ``NIX.md``) leaves the flake building against a version older than
the code needs, and nothing else in CI builds the flake.
"""

import hashlib
import io
import os
import re
import stat
import tarfile
import tempfile
import tomllib
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent

NIX_PIN = re.compile(
    r'pname = "(hyprland-[\w-]+)";\s*version = "([^"]+)";.*?hash = "([^"]+)";',
    re.DOTALL,
)


def _pyproject_floors() -> dict[str, str]:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        dependencies = tomllib.load(handle)["project"]["dependencies"]
    return dict(dep.split(">=") for dep in dependencies if dep.startswith("hyprland-"))


def _nix_pins() -> dict[str, tuple[str, str]]:
    text = (ROOT / "nix" / "hyprmod.nix").read_text()
    return {name: (version, hash_) for name, version, hash_ in NIX_PIN.findall(text)}


def _write(nar: bytearray, value: str | bytes) -> None:
    """Append one NAR token: an 8-byte little-endian length, then padded bytes."""
    if isinstance(value, str):
        value = value.encode()
    nar += len(value).to_bytes(8, "little")
    nar += value
    nar += b"\0" * (-len(value) % 8)


def _write_node(nar: bytearray, path: Path) -> None:
    _write(nar, "(")
    _write(nar, "type")
    if path.is_symlink():
        _write(nar, "symlink")
        _write(nar, "target")
        _write(nar, os.readlink(path))
    elif path.is_dir():
        _write(nar, "directory")
        for entry in sorted(path.iterdir(), key=lambda p: p.name.encode()):
            _write(nar, "entry")
            _write(nar, "(")
            _write(nar, "name")
            _write(nar, entry.name)
            _write(nar, "node")
            _write_node(nar, entry)
            _write(nar, ")")
    else:
        _write(nar, "regular")
        if path.stat().st_mode & stat.S_IXUSR:
            _write(nar, "executable")
            _write(nar, "")
        _write(nar, "contents")
        _write(nar, path.read_bytes())
    _write(nar, ")")


def _nar_hash(root: Path) -> str:
    """Return the SRI hash Nix would record for *root*, without invoking Nix.

    ``fetchFromGitHub`` hashes the *unpacked* archive, serialised as a NAR:
    a header token followed by one node per filesystem entry, directories
    sorted by name. Only the owner-execute bit is captured; the rest of the
    mode, ownership and timestamps are not part of the archive.
    """
    nar = bytearray()
    _write(nar, "nix-archive-1")
    _write_node(nar, root)
    return "sha256-" + b64encode(hashlib.sha256(nar).digest()).decode()


def _fetch_nar_hash(repo: str, version: str) -> str:
    url = f"https://github.com/BlueManCZ/{repo}/archive/v{version}.tar.gz"
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            archive = response.read()
    except urllib.error.HTTPError as exc:
        pytest.fail(f"{url} is not fetchable: {exc}")
    except OSError as exc:
        pytest.skip(f"no network access for {url}: {exc}")

    with tempfile.TemporaryDirectory() as workdir:
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(workdir, filter="data")
        # fetchzip unpacks with stripRoot, so the hash covers the contents of
        # the archive's single top-level directory rather than the directory.
        (unpacked,) = Path(workdir).iterdir()
        return _nar_hash(unpacked)


PINS = sorted((name, version, hash_) for name, (version, hash_) in _nix_pins().items())


class TestNixPins:
    def test_pins_match_pyproject_floors(self):
        pinned_versions = {name: version for name, (version, _) in _nix_pins().items()}
        assert pinned_versions == _pyproject_floors()

    @pytest.mark.parametrize(("repo", "version", "expected"), PINS, ids=[p[0] for p in PINS])
    def test_hash_matches_the_pinned_tag(self, repo: str, version: str, expected: str):
        """``nix-update`` rewrites version and hash together, so the two drift
        only when a pin is edited by hand. Nothing else notices until someone
        builds the flake and the fetch aborts on a hash mismatch."""
        assert _fetch_nar_hash(repo, version) == expected
