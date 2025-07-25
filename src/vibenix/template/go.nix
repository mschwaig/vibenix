{ lib
, buildGoModule
, fetchFromGitHub
}:

buildGoModule rec {
  pname = "{{ pname }}";
  version = "{{ version }}";

  src = {{ src_fetcher }};

  vendorHash = lib.fakeHash;  # Use null if no vendor dependencies

  # Build specific package if not building all:
  # subPackages = [ "cmd/your-app" ];
}