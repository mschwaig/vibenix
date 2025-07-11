{
  description = "A very basic flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
  };

  outputs = { self, nixpkgs }:
  let
    pkgs = import nixpkgs { system = "x86_64-linux"; };
    ciStdenv = pkgs.stdenv // {
      mkDerivation = args: pkgs.stdenv.mkDerivation (args // {
        preBuild = ''
          ${args.preBuild or ""}
          # CI/Non-interactive environment
          export CI=true
          export DEBIAN_FRONTEND=noninteractive
          export NONINTERACTIVE=1
          
          # Terminal and color control
          export TERM=dumb
          export NO_COLOR=1
          export FORCE_COLOR=0
          export CLICOLOR=0
          export CLICOLOR_FORCE=0
          
          # Disable progress bars and spinners
          export PROGRESS_NO_TRUNC=1
        '';
      });
    };
  in
   {

    packages.x86_64-linux.default = pkgs.lib.callPackageWith (
      pkgs // { stdenv = ciStdenv; }) ./package.nix {};
    packages.x86_64-linux.nixpkgs-src = nixpkgs.outPath;

  };
}
