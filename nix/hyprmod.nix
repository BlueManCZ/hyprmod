{ lib
, src
, fetchFromGitHub
, writeShellScript
, glib
, gnome-desktop
, gobject-introspection
, gtk4
, libadwaita
, lua5_4
, python3Packages
, wrapGAppsHook4
}:

# Inline derivations for the five Python deps that are not yet in nixpkgs.
# Track https://github.com/NixOS/nixpkgs/pull/505419 — once merged, these
# can be removed and the dependencies replaced with the upstream nixpkgs attrs.
# Last updated for hyprmod v0.4.0.

let
  hyprland-socket = python3Packages.buildPythonPackage rec {
    pname = "hyprland-socket";
    version = "0.12.2";
    pyproject = true;
    src = fetchFromGitHub {
      owner = "BlueManCZ";
      repo = "hyprland-socket";
      tag = "v${version}";
      hash = "sha256-XPVhHnIwq4Plkuk3uf/IUcg9L0OsZT76cr60x7EG1lc=";
    };
    build-system = [ python3Packages.hatchling ];
    doCheck = false;
    meta.description = "Typed Python library for Hyprland IPC via Unix sockets";
  };

  hyprland-config = python3Packages.buildPythonPackage rec {
    pname = "hyprland-config";
    version = "0.9.14";
    pyproject = true;
    src = fetchFromGitHub {
      owner = "BlueManCZ";
      repo = "hyprland-config";
      tag = "v${version}";
      hash = "sha256-Jgh/X7M+hdp0NPuA0YnfdYU/sxY9hfl/OCihnzobvm8=";
    };
    build-system = [ python3Packages.hatchling ];
    doCheck = false;
    meta.description = "Round-trip parser and editor for Hyprland configuration files";
  };

  hyprland-schema = python3Packages.buildPythonPackage rec {
    pname = "hyprland-schema";
    version = "0.7.1";
    pyproject = true;
    src = fetchFromGitHub {
      owner = "BlueManCZ";
      repo = "hyprland-schema";
      tag = "v${version}";
      hash = "sha256-36nSnuiWtJeCGcxJB2hNlbWEd0t2Ke5hhsUJddoap+w=";
    };
    build-system = [ python3Packages.hatchling ];
    doCheck = false;
    meta.description = "Typed Python schema for every Hyprland configuration option";
  };

  hyprland-monitors = python3Packages.buildPythonPackage rec {
    pname = "hyprland-monitors";
    version = "0.8.0";
    pyproject = true;
    src = fetchFromGitHub {
      owner = "BlueManCZ";
      repo = "hyprland-monitors";
      tag = "v${version}";
      hash = "sha256-a7fEDPPN9XYsrpE99C9c9MZGpqg24ZlY6vvHzgvNtzc=";
    };
    build-system = [ python3Packages.hatchling ];
    dependencies = [ hyprland-socket ];
    doCheck = false;
    meta.description = "Monitor management utilities for Hyprland";
  };

  hyprland-state = python3Packages.buildPythonPackage rec {
    pname = "hyprland-state";
    version = "0.4.5";
    pyproject = true;
    src = fetchFromGitHub {
      owner = "BlueManCZ";
      repo = "hyprland-state";
      tag = "v${version}";
      hash = "sha256-nRKZ1ZXueW7Kees0q+evjTaVDUxzYsvUaotyVma8eQc=";
    };
    build-system = [ python3Packages.hatchling ];
    dependencies = [ hyprland-config hyprland-monitors hyprland-schema hyprland-socket ];
    doCheck = false;
    meta.description = "Live state interface for Hyprland";
  };
in

python3Packages.buildPythonApplication {
  pname = "hyprmod";
  version = "0.4.0";
  pyproject = true;

  # src is passed by the caller so the flake can supply `self` (the repo root)
  # rather than a separate fetchFromGitHub, keeping the built artifact in sync
  # with the exact revision the user has pinned in their flake.lock.
  inherit src;

  build-system = [ python3Packages.hatchling ];

  nativeBuildInputs = [
    glib
    gobject-introspection
    wrapGAppsHook4
  ];

  buildInputs = [
    gnome-desktop
    gtk4
    libadwaita
  ];

  dependencies = with python3Packages; [
    hyprland-config
    hyprland-monitors
    hyprland-schema
    hyprland-socket
    hyprland-state
    pygobject3
    pygobject-stubs
  ];

  # nixpkgs ships a newer pygobject3 than the >=3.56.2 lower bound in
  # pyproject.toml; relax the pin so the build does not fail on a version check.
  pythonRelaxDeps = [ "pygobject" ];

  # lua5_4 must be on PATH at runtime (Lua config support, Hyprland 0.55+).
  # dontWrapGApps + manual preFixup merges the GApps wrapper args with the
  # PATH prefix in a single wrapper call — required for both x86_64 and aarch64.
  dontWrapGApps = true;
  preFixup = ''
    makeWrapperArgs+=("''${gappsWrapperArgs[@]}")
    makeWrapperArgs+=(--prefix PATH : ${lib.makeBinPath [ lua5_4 ]})
  '';

  postInstall = ''
    install -Dm0644 data/applications/io.github.bluemancz.hyprmod.desktop \
      -t $out/share/applications
    install -Dm0644 data/icons/hicolor/scalable/apps/io.github.bluemancz.hyprmod.svg \
      -t $out/share/icons/hicolor/scalable/apps
    install -Dm0644 data/metainfo/io.github.bluemancz.hyprmod.metainfo.xml \
      -t $out/share/metainfo
  '';

  doCheck = false;

  # Expose the five inline deps so nix-update can address each one via the
  # full attribute path: nix-update --flake --version X.Y.Z hyprmod.<dep>
  # The updateScript automates this by reading version floors from pyproject.toml.
  passthru = {
    inherit
      hyprland-config
      hyprland-monitors
      hyprland-schema
      hyprland-socket
      hyprland-state;

    updateScript = writeShellScript "update-hyprmod-deps" ''
      set -euo pipefail
      deps=(hyprland-config hyprland-socket hyprland-schema hyprland-monitors hyprland-state)
      for dep in "''${deps[@]}"; do
        ver=$(python3 -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    d = tomllib.load(f)
print(next(v.split('>=')[1] for v in d['project']['dependencies'] if v.split('>=')[0].strip() == '$dep'))
")
        nix-update --flake --version "$ver" "hyprmod.$dep"
      done
    '';
  };

  meta = with lib; {
    description = "Native GTK4/libadwaita settings application for Hyprland";
    longDescription = ''
      HyprMod is a native GTK4/libadwaita settings application for the Hyprland
      Wayland compositor. It provides live preview of configuration changes via
      IPC, a Bezier curve editor, monitor layout editor, keybind editor, window
      and workspace rules, autostart management, and Lua config support.
    '';
    homepage = "https://github.com/BlueManCZ/hyprmod";
    license = licenses.gpl3Plus;
    platforms = platforms.linux;
    mainProgram = "hyprmod";
  };
}
