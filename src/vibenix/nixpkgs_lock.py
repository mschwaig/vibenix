"""Utility for generating nixpkgs flake lock information."""

import subprocess
import json
import time
from typing import Dict, Any


def get_nixpkgs_lock_info(commit: str) -> Dict[str, Any]:
    """Generate flake lock information for a specific nixpkgs commit using nurl.

    Args:
        commit: The git commit hash for nixpkgs

    Returns:
        Dictionary with lock information including narHash and lastModified

    Raises:
        subprocess.CalledProcessError: If nurl command fails
        FileNotFoundError: If nurl is not installed
    """
    # Use nurl with HTTPS URL to get the fetcher info
    cmd = [
        'nurl',
        '--json',
        'https://github.com/nixos/nixpkgs',
        commit
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True
    )

    # Parse the JSON output from nurl
    fetcher_data = json.loads(result.stdout)

    # Extract the hash from args
    nar_hash = fetcher_data['args']['hash']

    return {
        'lastModified': int(time.time()),
        'narHash': nar_hash,
        'rev': commit
    }
