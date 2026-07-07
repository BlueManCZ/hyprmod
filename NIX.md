# Nix / NixOS

HyprMod ships a `flake.nix` that exposes a ready-to-use package and a nixpkgs
overlay for `x86_64-linux` and `aarch64-linux`.

> **nixpkgs status** — a PR to land HyprMod directly in nixpkgs is tracked at
> [NixOS/nixpkgs#505419](https://github.com/NixOS/nixpkgs/pull/505419). Once
> merged you can replace everything here with a plain `pkgs.hyprmod`.

---

## Using the flake in your project

### 1. Add the input

In your `flake.nix`:

```nix
inputs = {
  nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  hyprmod.url  = "github:BlueManCZ/hyprmod";

  # Optional but recommended: keep hyprmod using the same nixpkgs as your
  # system so you don't pull in a second copy of the world.
  hyprmod.inputs.nixpkgs.follows = "nixpkgs";
};
```

### 2a. Direct package reference (simplest)

Pass the input through to any module that needs it:

```nix
# In your NixOS configuration.nix or a NixOS module:
{ inputs, pkgs, ... }:
{
  environment.systemPackages = [
    inputs.hyprmod.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}

# In a home-manager module:
{ inputs, pkgs, ... }:
{
  home.packages = [
    inputs.hyprmod.packages.${pkgs.stdenv.hostPlatform.system}.default
  ];
}
```

Make sure `inputs` is forwarded into the module — in a standard flake-based
NixOS setup this is done with `specialArgs = { inherit inputs; }` (NixOS) or
`extraSpecialArgs = { inherit inputs; }` (home-manager).

### 2b. Overlay (recommended for larger configs)

The overlay inserts `hyprmod` into the `pkgs` namespace so it is available
everywhere without threading the flake input through every module:

```nix
# In the NixOS module that configures nixpkgs:
{ inputs, ... }:
{
  nixpkgs.overlays = [ inputs.hyprmod.overlays.default ];
}

# After that, anywhere pkgs is in scope:
{ pkgs, ... }:
{
  home.packages = [ pkgs.hyprmod ];
  # or
  environment.systemPackages = [ pkgs.hyprmod ];
}
```

#### Full minimal NixOS flake example

```nix
{
  inputs = {
    nixpkgs.url     = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager    = { url = "github:nix-community/home-manager"; inputs.nixpkgs.follows = "nixpkgs"; };
    hyprmod         = { url = "github:BlueManCZ/hyprmod";          inputs.nixpkgs.follows = "nixpkgs"; };
  };

  outputs = { self, nixpkgs, home-manager, hyprmod, ... }: {
    nixosConfigurations.mymachine = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        { nixpkgs.overlays = [ hyprmod.overlays.default ]; }
        home-manager.nixosModules.home-manager
        {
          home-manager.users.alice = { pkgs, ... }: {
            home.packages = [ pkgs.hyprmod ];
          };
        }
        ./configuration.nix
      ];
    };
  };
}
```

---

## Building from source locally

```bash
# Clone the repo
git clone https://github.com/BlueManCZ/hyprmod.git
cd hyprmod

# Build
nix build .#hyprmod

# Run directly without installing
nix run .#hyprmod

# Or enter a dev shell with all system deps available
nix shell .#hyprmod
```

---

## Updating to a new release (maintainer guide)

This section documents every step needed to ship the Nix package after a new
HyprMod release is tagged. Work through them in order.

### Step 1 — Check which deps changed

Compare `pyproject.toml` at the new tag against the previous one:

```bash
git diff v0.4.0 v0.5.0 -- pyproject.toml
```

Look for version bumps in the `dependencies` list:

```
pygobject>=3.56.2
hyprland-config>=X.Y.Z
hyprland-schema>=X.Y.Z
hyprland-state>=X.Y.Z
hyprland-monitors>=X.Y.Z
hyprland-socket>=X.Y.Z
```

Each `hyprland-*` library that changed needs a new hash in `nix/hyprmod.nix`.

### Step 2 — Regenerate hashes for changed `hyprland-*` deps

For each library whose version changed, fetch its new hash using the Nix
tooling. Replace `OWNER`, `REPO`, and `TAG` accordingly.

**Method A — `nix-prefetch-github` (requires the `nix-prefetch-github` package):**

```bash
nix run nixpkgs#nix-prefetch-github -- BlueManCZ hyprland-socket --rev v0.13.0
```

The output includes a `sha256` field. Use that value.

**Method B — `nix store prefetch-file` (built into Nix 2.19+):**

```bash
nix store prefetch-file \
  --hash-type sha256 \
  --unpack \
  "https://github.com/BlueManCZ/hyprland-socket/archive/refs/tags/v0.13.0.tar.gz"
```

Copy the `sha256` from the output.

**Method C — let Nix tell you (quickest):**

Set the hash to an empty string, run `nix build`, and Nix will print:

```
error: hash mismatch in fixed-output derivation ...
  specified: sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
  got:       sha256-<correct hash here>
```

Copy the `got:` value into `nix/hyprmod.nix`.

### Step 3 — Update version strings in `nix/hyprmod.nix`

For each changed `hyprland-*` library update both:

```nix
version = "X.Y.Z";   # ← new version
tag = "vX.Y.Z";      # ← new git tag
hash = "sha256-..."; # ← hash from step 2
```

Then update the main `hyprmod` derivation:

```nix
version = "0.5.0";   # ← new hyprmod version
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

This fetches the latest commits for all inputs and rewrites `flake.lock`.
Commit the updated `flake.lock` alongside the changes to `nix/hyprmod.nix`.

### Step 5 — Verify the build

```bash
nix build .#hyprmod
```

A successful build produces `./result/bin/hyprmod`. Smoke-test it:

```bash
./result/bin/hyprmod --version
# or, if a running Hyprland session is available:
./result/bin/hyprmod
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

## Hash reference — all packages at a glance

The table below lists every pinned package in `nix/hyprmod.nix` and the
commands to regenerate each hash. Run these after a version bump.

| Package | Repo | Hash field in `nix/hyprmod.nix` |
|---|---|---|
| `hyprland-socket` | `BlueManCZ/hyprland-socket` | `hyprland-socket.src.hash` |
| `hyprland-config` | `BlueManCZ/hyprland-config` | `hyprland-config.src.hash` |
| `hyprland-schema` | `BlueManCZ/hyprland-schema` | `hyprland-schema.src.hash` |
| `hyprland-monitors` | `BlueManCZ/hyprland-monitors` | `hyprland-monitors.src.hash` |
| `hyprland-state` | `BlueManCZ/hyprland-state` | `hyprland-state.src.hash` |

Generic command (replace `OWNER/REPO` and `vX.Y.Z`):

```bash
nix run nixpkgs#nix-prefetch-github -- OWNER REPO --rev vX.Y.Z
```

---

## Troubleshooting

**`error: attribute 'hyprmod' missing`**
You either haven't added the overlay or haven't passed `inputs` into the module.
See [2b. Overlay](#2b-overlay-recommended-for-larger-configs) and make sure
`nixpkgs.overlays = [ inputs.hyprmod.overlays.default ]` is evaluated before
the module that references `pkgs.hyprmod`.

**`error: hash mismatch in fixed-output derivation`**
A `hyprland-*` dep was bumped but its hash in `nix/hyprmod.nix` was not
updated. Follow [Step 2](#step-2--regenerate-hashes-for-changed-hyprland--deps)
to regenerate it.

**App launches but Lua config support is broken**
`lua5_4` is not on `PATH`. This should not happen with the Nix package because
the wrapper script prepends it automatically, but if you are running a custom
build double-check that `lua5_4` is in `preFixup`'s `makeWrapperArgs`.

**GTK warnings about missing GSettings schema**
The GSettings schema is compiled and installed by `hatch_build.py` during the
wheel build. If you see these warnings the build hook did not run. Ensure
`glib` is in `nativeBuildInputs` (it provides `glib-compile-schemas`).
