# Nix — maintenance guide

> **nixpkgs status** — a PR to land HyprMod directly in nixpkgs is tracked at
> [NixOS/nixpkgs#505419](https://github.com/NixOS/nixpkgs/pull/505419). Once
> merged you can replace the flake input with a plain `pkgs.hyprmod`.

For installation and usage instructions see the [README](README.md#-nix--nixos-1).

---

## Updating to a new release

Work through the steps below in order after a new HyprMod release is tagged.

### Step 1 — Check which deps changed

Compare `pyproject.toml` at the new tag against the previous one:

```bash
git diff v0.4.0 v0.5.0 -- pyproject.toml
```

Look for version bumps in the `dependencies` list:

```
hyprland-config>=X.Y.Z
hyprland-schema>=X.Y.Z
hyprland-state>=X.Y.Z
hyprland-monitors>=X.Y.Z
hyprland-socket>=X.Y.Z
```

Each `hyprland-*` library that changed needs a new hash in `nix/hyprmod.nix`.

### Step 2 — Regenerate hashes for changed `hyprland-*` deps

For each library whose version changed, fetch its new hash. Replace `REPO` and
`TAG` accordingly. Three equivalent methods — pick whichever suits you:

**Method A — `nix-prefetch-github`:**

```bash
nix run nixpkgs#nix-prefetch-github -- BlueManCZ hyprland-socket --rev v0.13.0
```

The output includes a `sha256` field. Use that value.

**Method B — `nix store prefetch-file` (Nix 2.19+):**

```bash
nix store prefetch-file \
  --hash-type sha256 \
  --unpack \
  "https://github.com/BlueManCZ/hyprland-socket/archive/refs/tags/v0.13.0.tar.gz"
```

**Method C — let Nix tell you (quickest):**

Set the hash to an empty string, run `nix build`, and copy the `got:` value
from the error message:

```
error: hash mismatch in fixed-output derivation ...
  specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
  got:       sha256-<correct hash here>
```

### Step 3 — Update version strings in `nix/hyprmod.nix`

For each changed `hyprland-*` library update both fields:

```nix
version = "X.Y.Z";   # ← new version
tag = "vX.Y.Z";      # ← new git tag
hash = "sha256-..."; # ← hash from step 2
```

Then update the main `hyprmod` derivation version:

```nix
version = "0.5.0";
```

The `src` for hyprmod itself is supplied by the flake as `self`, so no hash
change is needed for the main package — downstream users just run
`nix flake update hyprmod` to pull the new revision.

Also update the `# Last updated for hyprmod vX.Y.Z` comment at the top of
`nix/hyprmod.nix`.

### Step 4 — Update `flake.lock`

```bash
nix flake update
```

Commit the updated `flake.lock` alongside the changes to `nix/hyprmod.nix`.

### Step 5 — Verify the build

```bash
nix build .#hyprmod
./result/bin/hyprmod --version
```

### Step 6 — Check the overlay output

```bash
nix build --expr '
  let
    pkgs = import <nixpkgs> {};
    overlay = (builtins.getFlake (toString ./.)).overlays.default;
    pkgs2 = pkgs.extend overlay;
  in pkgs2.hyprmod
'
```

### Step 7 — Commit

```
nix: update to vX.Y.Z

Bump hyprmod to vX.Y.Z and update all hyprland-* dependency hashes.
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
Follow Step 2 above to regenerate it.

**App launches but Lua config support is broken**
`lua5_4` should be on PATH automatically via the wrapper — if you are running a
custom build, verify that `lua5_4` appears in `preFixup`'s `makeWrapperArgs`.

**GTK warnings about missing GSettings schema**
Ensure `glib` is in `nativeBuildInputs` (it provides `glib-compile-schemas`,
which the `hatch_build.py` hook calls during the wheel build).
