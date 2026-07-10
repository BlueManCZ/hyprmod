{
  description = "Native GTK4/libadwaita settings application for Hyprland";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAllSystems (system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          hyprmod = pkgs.callPackage ./nix/hyprmod.nix { src = self; };
          default = self.packages.${system}.hyprmod;
        }
      );

      # Overlay that injects hyprmod into a nixpkgs package set.
      # Add to nixpkgs.overlays in your NixOS or home-manager config to make
      # pkgs.hyprmod available everywhere without threading the flake input.
      overlays.default = final: _prev: {
        hyprmod = final.callPackage ./nix/hyprmod.nix { src = self; };
      };
    };
}
