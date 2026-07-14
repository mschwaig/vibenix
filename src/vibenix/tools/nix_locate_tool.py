"""Nix-locate tool wrapper for LLM usage."""

import subprocess
from typing import List, Dict, Any, Optional
import re

from vibenix.ccl_log import log_function_call

#@log_function_call("find_nix_packages")
def find_nix_packages(file_path: str, regex: bool = False, exact_match: bool = False, limit: int = 50) -> tuple[List[str], int, int]:
    """
    Search for Nix packages that provide a given file path.
    
    Args:
        file_path: The file path to search for (e.g., "bin/gcc", "lib/libz.so")
        regex: If True, treat file_path as a regular expression
        exact_match: If True, only match files with exact basename
        limit: Maximum number of results to return (default: 50)
        
    Returns:
        A tuple of (list of Nix expressions, total direct packages, total indirect packages)
    """
    print(f"📞 Function called: find_nix_packages with file_path: {file_path}, regex: {regex}, exact_match: {exact_match}")
    cmd = ["nix-locate"]
    
    if regex:
        cmd.append("--regex")
    
    if exact_match:
        cmd.append("--whole-name")
    
    cmd.append(file_path)
    
    # Parse the output
    packages = []
    seen_packages = set()
    
    # Track unique package names for counting
    all_direct_packages = set()
    all_indirect_packages = set()
    
    with subprocess.Popen(
        cmd, 
        stdout=subprocess.PIPE, 
        stderr=subprocess.PIPE, 
        text=True, # Decodes line by line
        errors="replace"
    ) as proc:
        for line in proc.stdout:
            if not line:
                continue
            line = line.strip()

            # Parse the line format: (package.output) or package.output  size type /nix/store/.../path
            match = re.match(r'^(\(?)([^)]+?)(\)?)\s+[\d,]+\s+[rxds]\s+(/nix/store/[^/]+)(.*)', line)
            if not match:
                continue
                
            has_parens = bool(match.group(1))
            package_attr = match.group(2)
            store_path_prefix = match.group(4)
            relative_path = match.group(5)
            
            # Extract base package name for counting
            base_package = package_attr[:-4] if package_attr.endswith('.out') else package_attr
            
            # Track package for counting
            if has_parens:
                all_indirect_packages.add(base_package)
            else:
                all_direct_packages.add(base_package)
            
            # Create the Nix expression if under limit
            if len(packages) < limit:
                if has_parens:
                    # Package is indirect dependency, less reliable
                    if package_attr.endswith('.out'):
                        # Remove .out suffix as it's the default
                        base_package = package_attr[:-4]
                        nix_expr = f'# (indirect) ${{pkgs.{base_package}}}{relative_path}'
                    else:
                        # Keep other suffixes like .dev, .bin, .lib
                        nix_expr = f'# (indirect) ${{pkgs.{package_attr}}}{relative_path}'
                else:
                    # Direct package reference
                    if package_attr.endswith('.out'):
                        # Remove .out suffix as it's the default
                        base_package = package_attr[:-4]
                        nix_expr = f'${{pkgs.{base_package}}}{relative_path}'
                    else:
                        # Keep other suffixes like .dev, .bin, .lib
                        nix_expr = f'${{pkgs.{package_attr}}}{relative_path}'
                
                # Avoid duplicates
                if nix_expr not in seen_packages:
                    packages.append(nix_expr)
                    seen_packages.add(nix_expr)
            else:
                proc.terminate()
                break
    
    return packages, len(all_direct_packages), len(all_indirect_packages)


def nix_locate_for_llm(file_path: str, regex: bool = False, exact_match: bool = False) -> str:
    """
    Wrapper for find_nix_packages that returns a formatted string for LLM consumption.
    
    Args:
        file_path: The file path to search for
        regex: If True, treat file_path as a regular expression
        exact_match: If True, only match files with exact basename
        
    Returns:
        A formatted string with Nix expressions or an informative message
    """
    packages, total_direct, total_indirect = find_nix_packages(file_path, regex=regex, exact_match=exact_match)
    
    if not packages:
        return f"No packages found providing '{file_path}'"
    
    # Separate direct and indirect matches
    direct_matches = []
    indirect_matches = []
    shown_direct_packages = set()
    shown_indirect_packages = set()
    
    for pkg in packages:
        if pkg.startswith("# (indirect)"):
            # Remove the prefix and add quotes
            cleaned_pkg = pkg.replace("# (indirect) ", "")
            indirect_matches.append(f'  "{cleaned_pkg}"')
            # Extract package name for counting
            pkg_match = re.match(r'\$\{pkgs\.([^}]+)\}', cleaned_pkg)
            if pkg_match:
                shown_indirect_packages.add(pkg_match.group(1))
        else:
            direct_matches.append(f'  "{pkg}"')
            # Extract package name for counting
            pkg_match = re.match(r'\$\{pkgs\.([^}]+)\}', pkg)
            if pkg_match:
                shown_direct_packages.add(pkg_match.group(1))
    
    # Format as a Nix attribute set
    result = "{\n"
    
    # Direct matches
    result += "  # Packages that directly provide the file\n"
    result += "  direct_matches = [\n"
    if direct_matches:
        result += "\n".join(direct_matches) + "\n"
    result += "  ];\n"
    
    # Add count message for direct matches if there are more
    remaining_direct = total_direct - len(shown_direct_packages)
    if remaining_direct > 0:
        result += f"  # and {remaining_direct} other packages contain '{file_path}'\n"
    
    # Indirect matches
    result += "\n  # Packages where the file is not contained directly, but found through symlinks (indirect matches)\n"
    result += "  symlink_matches = [\n"
    if indirect_matches:
        result += "\n".join(indirect_matches) + "\n"
    result += "  ];\n"
    
    # Add count message for indirect matches if there are more
    remaining_indirect = total_indirect - len(shown_indirect_packages)
    if remaining_indirect > 0:
        result += f"  # and {remaining_indirect} other packages have indirect matches for '{file_path}'\n"
    
    result += "}"
    
    return result
