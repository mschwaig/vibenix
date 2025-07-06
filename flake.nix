{
  description = "Application packaged using uv2nix";

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
    magentic = {
     url = "github:jackmpcollins/magentic/v0.39.3";
     flake = false;
    };
    noogle.url = github:nix-community/noogle;
  };

  outputs = { self, nixpkgs, flake-utils, pyproject-nix, uv2nix, magentic, noogle }:
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
        
        # Load the workspace
        workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

        # Create overlay
        overlay = workspace.mkPyprojectOverlay {
          sourcePreference = "wheel";
        };

        # Python package set
        pythonSet = (pkgs.callPackage pyproject-nix.build.packages {
          inherit python;
        }).overrideScope overlay;

        cli-dependencies = with pkgs; [ripgrep fzf jq nurl];
      in
      {
        packages = {
          # Create a virtual environment with all dependencies
          default = pythonSet.mkVirtualEnv "vibenix-env" workspace.deps.default;

          # The vibenix application itself with runtime dependencies
          vibenix = pkgs.symlinkJoin {
            name = "vibenix-wrapped";
            paths = [ pythonSet.vibenix ];
            buildInputs = [ pkgs.makeWrapper ];
            postBuild = ''
              wrapProgram $out/bin/vibenix \
                --set NOOGLE_FUNCTION_NAMES "${noogleFunctionNames}" \
                --prefix PATH : "${pkgs.lib.makeBinPath cli-dependencies}"
            '';
          };
          
          # Preprocessed noogle function names for search
          noogle-function-names = noogleFunctionNames;
        };

        devShells = {
          default = pkgs.mkShell {
            MAGENTIC_BACKEND = "litellm";
            OLLAMA_HOST= "https://hydralisk.van-duck.ts.net:11435";
#            MAGENTIC_LITELLM_MAX_TOKENS = "1024";
#             ANTHROPIC_LOG="debug";

            # Path to preprocessed noogle function names
            NOOGLE_FUNCTION_NAMES = "${noogleFunctionNames}";
            
            # Use only dependencies environment, not the built package
            packages = [
              python
              (pythonSet.mkVirtualEnv "vibenix-dev-deps" workspace.deps.default)
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
