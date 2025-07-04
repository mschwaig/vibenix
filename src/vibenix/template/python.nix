{ lib
, python3Packages
, fetchFromGitHub
}:

python3Packages.buildPythonApplication rec {
  pname = ...;
  version = ...;

  src = fetchFromGitHub {
    owner = ...;
    repo = ...;
    rev = ...;
    hash = lib.fakeHash;
  };

  # For pure Python packages, use format:
  # format = "setuptools";  # or "wheel", "pyproject", etc.

  # For packages using pyproject.toml:
  # pyproject = true;

  # build-system = with python3Packages; [
  #   setuptools
  #   wheel
  # ];

  dependencies = with python3Packages; [
    # List runtime dependencies here
  ];

  pythonImportsCheck = [
    # ... # add correct package name here
  ];

  # Optional: test dependencies
  # nativeCheckInputs = with python3Packages; [
  #   pytestCheckHook
  # ];

  # Optional: disable tests if they don't work in sandbox
  # doCheck = false;

  # Optional: specific tests to run
  # pytestFlagsArray = [
  #   "tests/"
  #   "-k 'not test_network'"
  # ];
}
