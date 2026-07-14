{
  description = "LLM-assisted packaging with Nix";

  inputs = {
    flake-utils.url = "github:numtide/flake-utils";
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    nix-index-database.url = "github:nix-community/nix-index-database/2025-06-08-034427";
    noogle.url = "github:nix-community/noogle";
  };

  outputs = { self, nixpkgs, flake-utils, pyproject-nix, uv2nix, noogle, nix-index-database }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;


        # Preprocessed noogle function names
        noogleFunctionNames = pkgs.runCommand "noogle-function-names" {
          buildInputs = [ pkgs.jq ];
        } ''
          ${pkgs.jq}/bin/jq -r '.[].meta.title' ${noogle.packages.${system}.data-json} > $out
        '';

        # Pre-computed package embeddings
        packageEmbeddings = pkgs.callPackage ./nix/package-embeddings.nix { inherit nixpkgs; };

        # Load the workspace
        workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

        # Create overlay
        overlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel";
        };


        # Python package set with torch from nixpkgs
        pythonSet = (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope (pkgs.lib.composeManyExtensions [
          overlay
          (final: prev: {
            torch = python.pkgs.torchWithoutCuda;
            pyperclip = python.pkgs.pyperclip;
          })
        ]);

        cli-dependencies = with pkgs; [ ripgrep fzf jq tree nurl nix-update nixos-shell nixfmt-rfc-style statix nil nixf-diagnose nixpkgs-lint-community nix-index-database.packages.${system}.nix-index-with-db ];

        # Build the vibenix package
        vibenixPackage = pythonSet.vibenix.overrideAttrs (old: {
          makeWrapperArgs = (old.makeWrapperArgs or []) ++ [
            "--prefix" "PATH" ":" (pkgs.lib.makeBinPath cli-dependencies)
            "--set" "NOOGLE_FUNCTION_NAMES" "${noogleFunctionNames}"
            "--set" "NIXPKGS_EMBEDDINGS" "${packageEmbeddings}/embeddings.pkl"
          ];
        });

        # Create a virtual environment with vibenix
        vibenixVenv = pythonSet.mkVirtualEnv "vibenix-env" workspace.deps.default;
      in
      {
        packages = {
          default = vibenixPackage;
          vibenix = vibenixPackage;
          noogle-function-names = noogleFunctionNames;

          dockerImage = pkgs.dockerTools.buildLayeredImage {
            name = "vibenix";
            tag = "latest";

            contents = with pkgs; [
              bashInteractive
              coreutils
              which
              nix
              git
              cacert

              vibenixVenv

            ] ++ cli-dependencies;

            config = {
              Cmd = [ "${vibenixVenv}/bin/vibenix" ];
              Env = [
                "PATH=${vibenixVenv}/bin:${pkgs.lib.makeBinPath (cli-dependencies ++ [ pkgs.nix pkgs.git pkgs.bashInteractive pkgs.coreutils ])}"
                "NOOGLE_FUNCTION_NAMES=${noogleFunctionNames}"
                "NIXPKGS_EMBEDDINGS=${packageEmbeddings}/embeddings.pkl"
                "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
                "NIX_SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
                "MAGENTIC_BACKEND=litellm"
              ];
              WorkingDir = "/workspace";
            };

            # Extra configuration for Nix to work in container
            # I think we would would need
            # echo "sandbox = false" > etc/nix/nix.conf
            # to make this run in an unprivileged container,
            # which I think would defeat the point.
            # So for deployment this should be connected with a remote builder
            # that has a proper sandbox.
            extraCommands = ''
              mkdir -p etc/nix
              echo "experimental-features = nix-command flakes" >> etc/nix/nix.conf

              # Create workspace directory
              mkdir -p workspace
              chmod 755 workspace
            '';
          };
        };

        devShells = {
          default = pkgs.mkShell {
            # Environment variables for development
            OLLAMA_HOST = "https://hydralisk.van-duck.ts.net:11435";

            # Path to preprocessed noogle function names
            NOOGLE_FUNCTION_NAMES = "${noogleFunctionNames}";

            # Path to pre-computed package embeddings
            NIXPKGS_EMBEDDINGS = "${packageEmbeddings}/embeddings.pkl";

            # Use only dependencies environment, not the built package, plus torch
            packages = [
              python
              (pythonSet.mkVirtualEnv "vibenix-dev-deps" workspace.deps.default)
              python.pkgs.torchWithoutCuda
              python.pkgs.sentence-transformers
            ];

            # Point to source files for development
            PYTHONPATH = "src";

            # Ensure CLI tools are on PATH
            shellHook = ''
              export PATH="${pkgs.lib.makeBinPath cli-dependencies}:$PATH"
            '';
          };

          # Impure shell for generating uv.lock
          impure = pkgs.mkShell {
            packages = [
              python
              pkgs.uv
            ] ++ cli-dependencies;
            shellHook = ''
              unset PYTHONPATH
            '';
            UV_PYTHON_DOWNLOADS = "never";
            UV_PYTHON = "${python}/bin/python";
            LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath [
              pkgs.stdenv.cc.cc.lib
            ];
          };
        };
      });
}
