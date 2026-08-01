# Nix — maintenance guide

> **nixpkgs status** — this is a temporary in-repo flake until
> [NixOS/nixpkgs#505419](https://github.com/NixOS/nixpkgs/pull/505419) lands.
> Once that PR merges, the five inline `hyprland-*` derivations can be replaced
> with upstream nixpkgs attrs and this flake can be thinned to a re-export or
> removed entirely.

For installation and usage instructions see the [README](README.md#-nix--nixos-1).

---

## Updating to a new release

The five `hyprland-*` deps are exposed via `passthru` and are independently
addressable for `nix-update` as `hyprmod.<dep>`. Each dep uses
`buildPythonPackage rec` with `tag = "v${version}"` so `nix-update` rewrites
both the version string and the hash in one step.

`tests/test_nix_pins.py` fails if a pin here drifts from the matching `>=`
floor in `pyproject.toml`, or if a pinned hash does not match the tag it
claims: it downloads each tag from GitHub and recomputes the NAR hash that
`fetchFromGitHub` records. Either mistake surfaces in CI instead of at build
time. The hash checks skip themselves when the downloads fail, so the suite
still runs offline.

### Option A — automated (via passthru.updateScript)

```bash
NIX_PATH=nixpkgs=flake:nixpkgs \
  nix-update --use-update-script --flake hyprmod
```

> **NIX_PATH caveat.** In flake mode `nix-update --use-update-script` uses
> `with import <nixpkgs> {}` internally (confirmed in
> [`nix_update/update.py`](https://github.com/Mic92/nix-update/blob/main/nix_update/update.py)).
> On flakes-only NixOS systems where `NIX_PATH` is unset this fails with:
> `error: file 'nixpkgs' was not found in the Nix search path`
> Setting `NIX_PATH=nixpkgs=flake:nixpkgs` as shown above pins it to the
> flake registry entry and avoids the error. If you prefer not to set
> `NIX_PATH`, use Option B instead.

### Option B — manual (reliable on all setups)

**1. Check which dep versions changed at the new tag:**

```bash
git diff vOLD vNEW -- pyproject.toml
```

Look for bumps in the `>=X.Y.Z` floors of the five `hyprland-*` entries.

**2. For each dep whose floor changed, run (substitute the real version):**

```bash
nix-update --flake --version 0.9.15  hyprmod.hyprland-config
nix-update --flake --version 0.12.3  hyprmod.hyprland-socket
nix-update --flake --version 0.6.4   hyprmod.hyprland-schema
nix-update --flake --version 0.8.1   hyprmod.hyprland-monitors
nix-update --flake --version 0.4.4   hyprmod.hyprland-state
```

Each command rewrites both `version` and `hash` in `nix/hyprmod.nix` for that
dep. Deps whose floor did not change need no update.

**3. Bump the hyprmod version itself** (no hash — `src = self`):**

```bash
nix-update --flake --version 0.5.0 hyprmod
```

### After updating (both options)

**Update `flake.lock`:**

```bash
nix flake update
```

**Verify the build:**

```bash
nix build .#hyprmod
./result/bin/hyprmod --version
```

**Verify the overlay:**

```bash
nix build --impure --expr '
  let
    flake = builtins.getFlake (builtins.toString ./.);
    pkgs = flake.inputs.nixpkgs.legacyPackages.x86_64-linux;
  in (pkgs.extend flake.overlays.default).hyprmod
'
```

**Commit:**

```
nix: update to vX.Y.Z

Bump hyprmod to vX.Y.Z and update hashes for <changed deps>.
Regenerate flake.lock.
```

---

## Hash reference

Every pinned package in `nix/hyprmod.nix` and the command to regenerate its hash:

| Package | Repo |
|---|---|
| `hyprland-socket` | `BlueManCZ/hyprland-socket` |
| `hyprland-config` | `BlueManCZ/hyprland-config` |
| `hyprland-schema` | `BlueManCZ/hyprland-schema` |
| `hyprland-monitors` | `BlueManCZ/hyprland-monitors` |
| `hyprland-state` | `BlueManCZ/hyprland-state` |

Generic command (replace `OWNER/REPO` and `vX.Y.Z`):

```bash
nix run nixpkgs#nix-prefetch-github -- OWNER REPO --rev vX.Y.Z
```

---

## Troubleshooting

**`error: attribute 'hyprmod' missing`**
You either haven't added the overlay or haven't passed `inputs` into the module.
Make sure `nixpkgs.overlays = [ inputs.hyprmod.overlays.default ]` is evaluated
before the module that references `pkgs.hyprmod`.

**`error: hash mismatch in fixed-output derivation`**
A `hyprland-*` dep was bumped but its hash in `nix/hyprmod.nix` was not updated.
Run the relevant `nix-update --flake --version X.Y.Z hyprmod.<dep>` command from
Option B above.

**App launches but Lua config support is broken**
`lua5_4` should be on PATH automatically via the wrapper — if you are running a
custom build, verify that `lua5_4` appears in `preFixup`'s `makeWrapperArgs`.

**GTK warnings about missing GSettings schema**
Ensure `glib` is in `nativeBuildInputs` (it provides `glib-compile-schemas`,
which the `hatch_build.py` hook calls during the wheel build).
