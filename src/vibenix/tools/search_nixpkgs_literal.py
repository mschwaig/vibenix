"""Literal/fuzzy search for Nix packages using fzf."""

import subprocess
import json
from vibenix.ccl_log import get_logger, log_function_call
from typing import Optional

@log_function_call("search_nixpkgs_for_package_literal")
def search_nixpkgs_for_package_literal(query: str, package_set_unique: Optional[str] = None) -> str:
    """Search the nixpkgs repository of Nix code for the given package using fuzzy search.
    Try separating compound word package names into substrings for more results (e.g. "nvimtreesitter" -> "nvim treesitter", "fast-ssh" -> "fast ssh").
    
    Args:
        query: The search term
        package_set_unique: Optional package set to search within (e.g. "python3Packages", "haskellPackages")
    
    Returns a Nix expression with matching packages grouped by package set.
    """
    print(f"📞 Function called: search_nixpkgs_for_package_literal with query: {query}, package_set_unique: {package_set_unique}")
    return _search_nixpkgs_for_package_literal(query, package_set_unique)

def _search_nixpkgs_for_package_literal(query: str, package_set_unique: Optional[str] = None) -> str:
    """Search the nixpkgs repository of Nix code for the given package using fuzzy search."""
    
    # Get all packages (using ^ to match everything)
    from vibenix.defaults import get_settings_manager
    from vibenix import config
    if get_settings_manager().get_setting_enabled("strict_lock_env"):
        nix_result = subprocess.run(
            ["nix", "search", "--json", "--inputs-from", ".", "nixpkgs", "^"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=config.flake_dir  # Run in the package directory to use its lock file
        )
    else:
        nix_result = subprocess.run(
            ["nix", "search", "--json", "nixpkgs", "^"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
    
    if nix_result.returncode != 0 or not nix_result.stdout.strip():
        return f"Failed to fetch package list from nixpkgs"
    
    # Convert JSON to lines with format: package_name|json_entry
    # This makes it easier to parse after fzf
    jq_filter = r'''
    to_entries 
    | map(
        (.key |= sub("legacyPackages\\.x86_64-linux\\."; ""))
        | "\(.key)|\(.value | @json)"
      )
    | .[]
    '''
    
    jq_result = subprocess.run(
        ["jq", "-r", jq_filter],
        input=nix_result.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    if jq_result.returncode != 0:
        return f"Failed to process package list: {jq_result.stderr}"
    
    # Try exact substring search first, then fuzzy as fallback
    exact_result = subprocess.run(
        ["fzf", f"--filter={query}", "-i", "--delimiter=|", "--with-nth=1", "--exact"],
        input=jq_result.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    # If exact search gives good results, use those; otherwise fall back to fuzzy
    if exact_result.returncode == 0 and len(exact_result.stdout.strip().split('\n')) >= 3:
        fzf_result = exact_result
        print(f"Using exact search - found {len(exact_result.stdout.strip().split('\n'))} matches for query")
    else:
        # Fuzzy search as fallback, but keep sorted for best matches first  
        fzf_result = subprocess.run(
            ["fzf", f"--filter={query}", "-i", "--delimiter=|", "--with-nth=1"],
            input=jq_result.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print(f"Using fuzzy search - found {len(fzf_result.stdout.strip().split('\n')) if fzf_result.stdout.strip() else 0} matches for query")
    
    # Parse fzf results
    fuzzy_matches = []
    if fzf_result.returncode == 0 and fzf_result.stdout.strip():
        for line in fzf_result.stdout.strip().split('\n')[:200]:  # Limit to top 200 matches
            if not line or '|' not in line:
                continue
            try:
                pkg_name, json_str = line.split('|', 1)
                pkg_info = json.loads(json_str)
                fuzzy_matches.append({
                    'name': pkg_name,
                    'version': pkg_info.get('version', ''),
                    'description': pkg_info.get('description', ''),
                })
            except (json.JSONDecodeError, ValueError):
                continue
    
    # If fuzzy search returns few results, also do substring matching
    substring_matches = []
    if len(fuzzy_matches) < 20:
        query_lower = query.lower()
        for line in jq_result.stdout.strip().split('\n'):
            if not line or '|' not in line:
                continue
            try:
                pkg_name, json_str = line.split('|', 1)
                if query_lower in pkg_name.lower():
                    # Check if already in fuzzy matches
                    if not any(m['name'] == pkg_name for m in fuzzy_matches):
                        pkg_info = json.loads(json_str)
                        substring_matches.append({
                            'name': pkg_name,
                            'version': pkg_info.get('version', ''),
                            'description': pkg_info.get('description', ''),
                        })
                        if len(substring_matches) >= 50:  # Limit substring matches
                            break
            except (json.JSONDecodeError, ValueError):
                continue
    
    # Combine matches: fuzzy matches first, then substring matches
    matches = fuzzy_matches + substring_matches
    
    if not matches:
        return f"No packages found matching '{query}'."
    
    # Categorize results while preserving fzf's ranking
    package_sets = {}  # package_set -> list of packages
    individual_packages = []
    package_set_order = []  # Track order of first appearance
    
    for match in matches:
        pkg_name = match['name']
        if '.' in pkg_name:
            package_set = pkg_name.split('.', 1)[0]
            if package_set not in package_sets:
                package_sets[package_set] = []
                package_set_order.append(package_set)
            package_sets[package_set].append(match)
        else:
            individual_packages.append(match)
   
    nix_lines = []
    # Filter by package set if specified
    if package_set_unique:
        package_sets_temp = {k: v for k, v in package_sets.items() if k == package_set_unique}
        if package_sets_temp:
            package_set_order = [s for s in package_set_order if s == package_set_unique]
        else:
            nix_lines.extend([f"# No packages found in set '{package_set_unique}' matching '{query}', showing all matches instead."])
            package_set_unique = None  # Reset to show all
    
    # Determine limits based on whether package_set is specified
    set_limit = 20 if package_set_unique else 10
    pkg_per_set_limit = 20 if package_set_unique else 3
    individual_limit = 20 if package_set_unique else 5
    
    # Build Nix expression
    nix_lines.extend(["{"])

    # Add individual packages
    if individual_packages and not package_set_unique:
        for i, pkg in enumerate(individual_packages[:individual_limit]):
            nix_lines.append(f"  {pkg['name']} = {{")
            nix_lines.append(f'    pname = "{pkg["name"]}";')
            nix_lines.append(f'    version = "{pkg["version"]}";')
            desc = pkg['description'].replace('"', '\\"')
            nix_lines.append(f'    description = "{desc}";')
            nix_lines.append("  };")
        
        if len(individual_packages) > individual_limit:
            nix_lines.append(f"  # ... and {len(individual_packages) - individual_limit} more individual packages")
        if package_sets:
            nix_lines.append("")
    
    # Add package sets (preserving fzf ranking order)
    for set_idx, set_name in enumerate(package_set_order[:set_limit]):
        packages = package_sets[set_name]
        count = len(packages)
        
        nix_lines.append(f"  {set_name} = {{")
        
        # Show more packages if searching within specific set, avoiding duplicates
        show_limit = min(count, pkg_per_set_limit)
        shown_attrs = set()
        shown_count = 0
        
        for pkg in packages:
            if shown_count >= show_limit:
                break
                
            # Use everything after the package set name (e.g., vimPlugins.nvim-treesitter-parsers.meson -> nvim-treesitter-parsers.meson)
            pkg_parts = pkg['name'].split('.', 1)  # Split on first dot only
            if len(pkg_parts) > 1:
                pkg_attr = pkg_parts[1]  # Everything after package set
            else:
                pkg_attr = pkg['name']  # Fallback to full name
            
            # Skip if we've already shown this attribute (shouldn't happen with full paths, but just in case)
            if pkg_attr in shown_attrs:
                continue
                
            shown_attrs.add(pkg_attr)
            shown_count += 1
            
            nix_lines.append(f"    \"{pkg_attr}\" = {{")
            nix_lines.append(f'      pname = "{pkg_attr.split(".")[-1]}";')
            nix_lines.append(f'      version = "{pkg["version"]}";')
            # Escape quotes in description
            desc = pkg['description'].replace('"', '\\"')
            nix_lines.append(f'      description = "{desc}";')
            nix_lines.append("    };")
        
        if count > show_limit:
            nix_lines.append(f"    # ... and {count - show_limit} more packages")
        
        nix_lines.append("  };")
        if set_idx < len(package_set_order[:set_limit]) - 1 or individual_packages:
            nix_lines.append("")
    
    nix_lines.append("}")
    
    return "\n".join(nix_lines)
