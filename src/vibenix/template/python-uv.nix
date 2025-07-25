{ lib
, python3Packages
, fetchFromGitHub
}:

python3Packages.buildPythonApplication rec {
  pname = "{{ pname }}";
  version = "{{ version }}";

  src = {{ src_fetcher }};

  pyproject = true;

  build-system = with python3Packages; [
    uv-build
  ];

  nativeCheckInputs = with python3Packages; [
    pytestCheckHook
  ];

  pythonImportsCheck = [ pname ];
}