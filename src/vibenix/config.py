import os
import atexit
from pathlib import Path
import tempfile
from collections import deque
from typing import List, TYPE_CHECKING

if TYPE_CHECKING:
    from vibenix.errors import NixBuildResult

error_stack: List['NixBuildResult']
build_timeout: str
nixpkgs_commit: str

def init():
    global error_stack
    error_stack = []

    global template_dir

    script_dir = os.path.dirname(os.path.abspath(__file__))
    reference_directory_name = 'template'
    template_dir = Path(script_dir, reference_directory_name)

    global flake_dir

    flake_dir_obj = tempfile.TemporaryDirectory()
    #atexit.register(flake_dir_obj.cleanup)

    flake_dir = Path(flake_dir_obj.name)
    # time out build after 7 minutes
    global build_timeout
    build_timeout = str(7*60)

    # Default nixpkgs commit (matches main project's nixpkgs)
    global nixpkgs_commit
    nixpkgs_commit = "d3d2d80a2191a73d1e86456a751b83aa13085d7d"
   
