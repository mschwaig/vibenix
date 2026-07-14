"""Tool to upgrade the nixpkgs release in flake.nix to the next stable release."""

import re
import requests
from typing import Optional, Tuple
from vibenix.ccl_log import get_logger, log_function_call
from vibenix.flake import get_package_path
from vibenix import config
from vibenix.ui.conversation import coordinator_message, ask_user_direct

@log_function_call("upgrade_nixpkgs")
def upgrade_nixpkgs(reason: str) -> str:
    """
    Upgrade the nixpkgs release used to the next stable release.
    **Useful when a dependency or dependency version is not present in the nixpkgs used**
    Triple check that required dependency really is not in current nixpkgs before calling this tool.

    Args:
        reason: A string concisely describing the reason for the upgrade (e.g., "Require jellyfin version > 1.23, current version is 1.21")
    
    Returns:
        A success or error message describing the upgrade result.
    """
    print(f"📞 Function called: upgrade_nixpkgs with reason: `{reason}`")
    # Ask user for confirmation with 30 second timeout
    user_resp = ask_user_direct(
        f"Received request to upgrade nixpkgs release. Reason: {reason}\n\n Accept (y) or reject (N) this request?",
        timeout=30
    )
    if user_resp is None:
        return "Error: Upgrade request rejected due to timeout"
    if user_resp.strip().lower() != 'y':
        return "Error: Upgrade request rejected by user"

    return _upgrade_nixpkgs()


def _get_next_nixpkgs_release(current_release: str) -> Optional[str]:
    """Fetch stable releases from GitHub API until the current release is found, then return the next one.
    
    Args:
        current_release: The current nixpkgs release tag (e.g., 'nixpkgs-23.11')
    
    Returns:
        The next stable release, or None if current is already the latest.
    """
    try:
        # Fetch releases from GitHub API, sorted by creation date descending (most recent first)
        # Use releases endpoint which supports sorting
        url = "https://api.github.com/repos/nixos/nixpkgs/releases"
        
        stable_pattern = re.compile(r'^nixpkgs-(\d{2})\.(\d{2})$')
        previous_release = None
        page = 1
        
        while True:
            params = {"per_page": 100, "page": page, "sort": "created", "direction": "desc"}
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            if not data:
                # Reached end without finding current release
                return None
            
            for release in data:
                tag_name = release['tag_name']
                
                # Only consider stable releases
                if not stable_pattern.match(tag_name):
                    continue
                
                # If we found the current release, the previous one is the next one
                if tag_name == current_release:
                    # Verify that previous_release is indeed more recent than current
                    # by ensuring both version numbers are equal or higher
                    if previous_release:
                        match_curr = stable_pattern.match(current_release)
                        match_prev = stable_pattern.match(previous_release)
                        if match_curr and match_prev:
                            curr_yy, curr_mm = int(match_curr.group(1)), int(match_curr.group(2))
                            prev_yy, prev_mm = int(match_prev.group(1)), int(match_prev.group(2))
                            # Verify previous is more recent: year equal or higher, and month equal or higher
                            if (prev_yy > curr_yy) or (prev_yy == curr_yy and prev_mm > curr_mm):
                                return previous_release
                            else:
                                # This shouldn't happen with descending order, but log it
                                raise RuntimeError(f"Found next release '{previous_release}' is not more recent than current '{current_release}'")
                    return previous_release
                
                # Track this as a candidate for the next release
                if previous_release is None:
                    previous_release = tag_name
            
            page += 1
        
    except requests.RequestException as e:
        raise RuntimeError(f"Failed to fetch nixpkgs releases from GitHub API: {e}")


def _get_current_nixpkgs_release() -> Optional[str]:
    """Extract the current nixpkgs release from flake.nix."""
    try:
        flake_path = config.flake_dir / "flake.nix"
        
        with open(flake_path, 'r') as f:
            content = f.read()
        
        # Look for patterns like:
        # nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-23.11";
        # or
        # nixpkgs.url = "github:nixos/nixpkgs/nixpkgs-unstable";
        match = re.search(r'nixpkgs\.url\s*=\s*"github:nixos/nixpkgs/([^"]+)"', content)
        
        if match:
            return match.group(1)
        
        return None
    
    except Exception as e:
        raise RuntimeError(f"Failed to read flake.nix: {e}")


def _upgrade_nixpkgs() -> str:
    """Perform the nixpkgs upgrade."""
    try:
        # Get current release
        current_release = _get_current_nixpkgs_release()
        if not current_release:
            return "Error: Could not determine current nixpkgs release from flake.nix"
        
        # Unstable releases cannot be upgraded
        if current_release == "nixpkgs-unstable":
            return "Error: Current release is nixpkgs-unstable. Cannot upgrade to a specific stable release."
        
        # Get the next stable release
        next_release = _get_next_nixpkgs_release(current_release)
        if not next_release:
            return f"Error: Current release '{current_release}' is already the latest stable release or was not found in the releases list"
        
        # Update flake.nix
        flake_path = config.flake_dir / "flake.nix"
        
        with open(flake_path, 'r') as f:
            content = f.read()
        
        # Replace the old release with the new one
        updated_content = re.sub(
            rf'(nixpkgs\.url\s*=\s*"github:nixos/nixpkgs/){current_release}(")',
            rf'\1{next_release}\2',
            content
        )
        
        if updated_content == content:
            return f"Error: Failed to update flake.nix with new release '{next_release}'"
        
        # Write the updated content
        with open(flake_path, 'w') as f:
            f.write(updated_content)
        
        return f"Successfully upgraded nixpkgs from '{current_release}' to '{next_release}'"
    
    except RuntimeError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: Unexpected error during upgrade: {str(e)}"
