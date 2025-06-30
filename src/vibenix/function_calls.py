import subprocess
import requests
import json
import os
from vibenix.ccl_log import get_logger

def search_nixpkgs_for_package(query: str) -> str:
    """Search the nixpkgs repository of Nix code for the given package"""

    print("📞 Function called: search_nixpkgs_for_package with query: ", query)
    get_logger().log_function_call("search_nixpkgs_for_package", query=query)
    
    # Run nix search first, explicitly separate stdout and stderr
    nix_result = subprocess.run(
        ["nix", "search", "--json", "nixpkgs", query],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    if nix_result.returncode != 0 or not nix_result.stdout.strip():
        return f"no results found for query '{query}'"
    
    # Pipe to jq to remove the platform-specific prefix
    # TODO: fix platform dependence here for mac support
    jq_filter = r'with_entries(.key |= sub("legacyPackages\\.x86_64-linux\\."; ""))'
    jq_result = subprocess.run(
        ["jq", jq_filter],
        input=nix_result.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    if jq_result.returncode == 0 and jq_result.stdout.strip():
        return jq_result.stdout
    elif not jq_result.stdout.strip():
        return f"nixpkgs search returned no results for {query}"
    elif jq_result.returncode != 0:
        raise ValueError(f"jq failed with return code {jq_result.returncode}, stderr: {jq_result.stderr}")


def web_search(query: str) -> str:
    """Perform a web search with a query"""
    
    print("📞 Function called: web_search with query: ", query)
    get_logger().log_function_call("web_search", query=query)
    try:
        result = subprocess.run(["ddgr", "--json", query], text=True, capture_output=True, timeout=30)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        else:
            return f"no search results found for query '{query}'"
    except subprocess.TimeoutExpired:
        return "search timed out"
    except FileNotFoundError:
        return "search tool not found, please install ddgr"
    

def fetch_url_content(url: str) -> str:
    """Fetch HTML content from a URL"""
    
    print("📞 Function called: fetch_url_content with url: ", url)
    get_logger().log_function_call("fetch_url_content", url=url)
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text
    except requests.RequestException as e:
        return f"Error fetching URL: {str(e)}"

def search_nix_functions(query: str) -> str:
    """Search for Nix builtin and library functions by name"""
    
    print("📞 Function called: search_nix_functions with query: ", query)
    get_logger().log_function_call("search_nix_functions", query=query)
    
    try:
        # Get the path from environment variable
        function_names_path = os.environ.get('NOOGLE_FUNCTION_NAMES')
        
        if not function_names_path:
            raise RuntimeError("NOOGLE_FUNCTION_NAMES environment variable not set. Please run from nix develop shell.")
        
        if not os.path.exists(function_names_path):
            raise FileNotFoundError(f"Noogle function names file not found at {function_names_path}")
        
        # Use fzf to filter the function names
        result = subprocess.run(
            ["fzf", f"--filter={query}", "--exact", "-i"],
            stdin=open(function_names_path, 'r'),
            text=True,
            capture_output=True
        )
        
        if result.returncode == 0 and result.stdout.strip():
            matches = result.stdout.strip().split('\n')
            # Limit results to prevent overwhelming output
            limited_matches = matches[:50]
            result_text = "\n".join(limited_matches)
            if len(matches) > 50:
                result_text += f"\n\n... and {len(matches) - 50} more results"
            return result_text
        else:
            return f"No Nix functions found matching '{query}'"
            
    except FileNotFoundError:
        return "Error: fzf not found. Please ensure fzf is available in the environment."
    except Exception as e:
        return f"Error searching Nix functions: {str(e)}"
    
