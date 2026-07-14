"""Semantic search for Nix packages using sentence transformers."""

import os
import pickle
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from vibenix.ccl_log import get_logger, log_function_call
from typing import Optional

@log_function_call("search_nixpkgs_for_package_semantic")
def search_nixpkgs_for_package_semantic(query: str, package_set: Optional[str] = None) -> str:
    """Search the nixpkgs repository using semantic similarity with embeddings.
    
    Args:
        query: The search term
        package_set: Optional package set to search within (e.g. "python3Packages", "haskellPackages")
    
    Uses sentence transformers to find semantically similar package names and descriptions.
    Returns a Nix expression with matching packages grouped by package set.
    """
    print(f"📞 Function called: search_nixpkgs_for_package_semantic with query: {query}, package_set: {package_set}")
    return _search_nixpkgs_for_package_semantic(query, package_set)

def _search_nixpkgs_for_package_semantic(query: str, package_set: Optional[str] = None) -> str:
    """Search the nixpkgs repository using semantic similarity with embeddings.
    
    Args:
        query: The search term
        package_set: Optional package set to search within (e.g. "python3Packages", "haskellPackages")
    
    Uses sentence transformers to find semantically similar package names and descriptions.
    Returns a Nix expression with matching packages grouped by package set.
    """
    
    # Get path to pre-computed embeddings from environment
    embeddings_path = os.environ.get('NIXPKGS_EMBEDDINGS')
    if not embeddings_path:
        return "Error: NIXPKGS_EMBEDDINGS environment variable not set. Please run from nix develop shell."
    
    if not os.path.exists(embeddings_path):
        return f"Error: Pre-computed embeddings not found at {embeddings_path}"
    
    # Load pre-computed embeddings
    try:
        with open(embeddings_path, 'rb') as f:
            data = pickle.load(f)
            embeddings = data['embeddings']
            package_names = data['names']
            packages = data['packages']
    except Exception as e:
        return f"Error loading pre-computed embeddings: {str(e)}"
    
    # Load model for encoding the query
    try:
        model = SentenceTransformer('all-MiniLM-L6-v2')
    except NotImplementedError as e:
        if "Cannot copy out of meta tensor" in str(e):
            # Handle meta tensor issue by forcing CPU device during initialization
            import torch
            model = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
        else:
            return f"Error loading sentence transformer model: {str(e)}"
    
    # Encode query and find similar packages
    query_embedding = model.encode([query])
    similarities = cosine_similarity(query_embedding, embeddings)[0]
    
    # Get top 200 results
    top_indices = np.argsort(similarities)[::-1][:200]
    
    # Build matches list
    matches = []
    # Create package dict from pre-computed data
    package_dict = {entry['key']: entry['value'] for entry in packages}
    
    for idx in top_indices:
        if similarities[idx] < 0.2:  # Skip very low similarity scores
            break
        pkg_name = package_names[idx]
        pkg_info = package_dict.get(pkg_name, {})
        matches.append({
            'name': pkg_name,
            'version': pkg_info.get('version', ''),
            'description': pkg_info.get('description', ''),
            'score': similarities[idx]
        })
    
    if not matches:
        return f"No packages found matching '{query}'"
    
    # Categorize results
    package_sets = {}
    individual_packages = []
    package_set_order = []
    
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
    
    # Filter by package set if specified
    if package_set:
        # Filter matches to only include specified package set
        filtered_matches = []
        for match in matches:
            if '.' in match['name'] and match['name'].split('.', 1)[0] == package_set:
                filtered_matches.append(match)
        matches = filtered_matches
        
        # Re-categorize with filtered matches
        package_sets = {}
        individual_packages = []
        package_set_order = []
        
        for match in matches:
            pkg_name = match['name']
            if '.' in pkg_name:
                package_set_name = pkg_name.split('.', 1)[0]
                if package_set_name not in package_sets:
                    package_sets[package_set_name] = []
                    package_set_order.append(package_set_name)
                package_sets[package_set_name].append(match)
            else:
                individual_packages.append(match)
    
    # Determine limits based on whether package_set is specified
    set_limit = 20 if package_set else 10
    pkg_per_set_limit = 20 if package_set else 3
    individual_limit = 20 if package_set else 5
    
    # Build Nix expression
    nix_lines = ["{"]    
    
    # Add package sets
    for set_idx, set_name in enumerate(package_set_order[:set_limit]):
        packages = package_sets[set_name]
        count = len(packages)
        
        nix_lines.append(f"  {set_name} = {{")
        
        # Show more packages if searching within specific set
        show_limit = min(count, pkg_per_set_limit)
        for pkg in packages[:show_limit]:
            pkg_attr = pkg['name'].split('.')[-1]
            nix_lines.append(f"    {pkg_attr} = {{")
            nix_lines.append(f'      pname = "{pkg_attr}";')
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
    
    # Add individual packages
    if individual_packages and not package_set:
        if package_sets:
            nix_lines.append("")
        nix_lines.append("  # Individual packages")
        for i, pkg in enumerate(individual_packages[:individual_limit]):
            nix_lines.append(f"  {pkg['name']} = {{")
            nix_lines.append(f'    pname = "{pkg["name"]}";')
            nix_lines.append(f'    version = "{pkg["version"]}";')
            desc = pkg['description'].replace('"', '\\"')
            nix_lines.append(f'    description = "{desc}";')
            nix_lines.append("  };")
        
        if len(individual_packages) > individual_limit:
            nix_lines.append(f"  # ... and {len(individual_packages) - individual_limit} more individual packages")
    
    nix_lines.append("}")

    return "\n".join(nix_lines)
