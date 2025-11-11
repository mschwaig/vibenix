"""Search for related packages in nixpkgs and other related methods.
Includes:
    - get_builder_functions: Get list of all builder functions available in nixpkgs.
    - find_similar_builder_patterns: Find existing builder functions combinations in nixpkgs and packages for each.
        - created with a factory to capture builder function cache in closure.
"""

import os
from typing import List, Set, Dict, Any, Optional
from vibenix.ccl_log import get_logger, log_function_call
from vibenix.tools.search_nixpkgs_manual_documentation import _list_language_frameworks


def get_nixpkgs_source_path() -> str:
    """Get the nixpkgs source path from the template flake."""
    from vibenix import config
    import subprocess
    try:
        result = subprocess.run(
            ["nix", "build", ".#nixpkgs-src", "--no-link", "--print-out-paths"],
            cwd=config.template_dir,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Failed to get nixpkgs source path: {e}")


@log_function_call("find_builder_functions", do_print=False)
def get_builder_functions() -> List[str]:
    """Returns the list of all builder functions in nixpkgs."""
    print("📞 Function called: get_builder_functions")
    return _get_builder_functions(do_print=True)

def _get_builder_functions(do_print: bool = False) -> List[str]:
    """Returns the list of all builder functions in nixpkgs."""
    import json
    from pathlib import Path
    
    cache_dir = Path("cachedir")
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / "builder_functions.json"
    
    if cache_file.exists():
        try:
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
            functions = cached_data['functions']
            if do_print:
                print(f"♻️ Loaded {len(functions)} builder functions from cache")
            return functions
        except (json.JSONDecodeError, KeyError) as e:
            print(f"⚠️ Cache file corrupted, regenerating: {e}")
    try:
        nixpkgs_path = get_nixpkgs_source_path()
    except Exception as e:
        raise RuntimeError(f"Failed to get nixpkgs source path: {e}")
    
    builders = _extract_builders(nixpkgs_path)
    try:
        cache_data = {
            'functions': builders,
            'timestamp': __import__('time').time(),
            'nixpkgs_path': nixpkgs_path
        }
        with open(cache_file, 'w') as f:
            json.dump(cache_data, f, indent=2)
        print(f"💾 Cached {len(builders)} builder functions")
    except Exception as e:
        print(f"⚠️ Failed to cache results: {e}")
    return builders

def _extract_builders(path: str, cache: List[str] = None) -> List[str]:
    """Extract builder functions from all expressions on a directory or file.

    Args:
        path: Relative path to directory or file to search for builders
        cache: Optional list of already known builders to filter results (performance)
    """
    additional_functions = [ # Not caught by the patterns below
        # appimageTools.wrapType2 # TODO
        'mkDerivation', # Assuming every other relevant builder has 3+ segments
        'buildComposerProject2' # Doesnt fit the patterns
    ]
    blacklist_functions = [ # To remove from results
        'mkPulumiPackage', # TODO ???
        'mkChromiumDerivation', # very specific and actual name is mkDerivation under chromium set
        'buildZipPackage', # would require ...
        'buildNodePackage', # Would require a node2nix or nodeEnv ????? IDK
        'buildNodeShell',
        'buildMaubotPlugin', # Too specific
        'buildAzureCliPackage', # whatever
        'mkFranzDerivation', # whatever
        'mkWmApplication', #??
        'mkAppleDerivation',
        'mkMesonDerivation',
        'mkToolModule',
        'mkAliasDerivation',
        'mkAliasOptionModule',
        'mkChangedOptionModule',
        'mkLocalDerivation',
        'mkMergedOptionModule',
        'mkRemovedOptionModule',
        'mkRenamedOptionModule',
    ]
    # Hardcoded mappings
    helper = { "rust":  "rustPlatform", "dprint": "dprint-plugins", "open": "openmodelica",
               "derivation": "stdenv", "shell": "stdenv" }

    import subprocess
    from pathlib import Path

    def _generate_patterns() -> List[str]:
        """Generate regex patterns for builder function detection."""
        prefixes = ['build', 'mk']
        suffixes = ['Package', 'Application', 'Module', 'Plugin', 'Derivation', 'Shell']

        patterns = []
        for prefix in prefixes:
            for suffix in suffixes:
                patterns.append(rf'\b(\w+\.)*{prefix}[A-Za-z]+{suffix}\b')
        return patterns
    
    def _validate_path(path: str) -> Path:
        """Helper function to validate that the path is within nixpkgs."""
        root_dir = Path("/nix/store").resolve() # Assuming this function is not used freely by the model!
        if not Path(path).is_absolute():
            target_path = root_dir / path
        else:
            target_path = Path(path).resolve()
        
        if not target_path.is_relative_to(root_dir):
            raise ValueError(f"Path '{path}' is outside the allowed root directory '{root_dir}'")
        return target_path

    if not os.path.exists(path):
        # Assume its a nix expression, place it in a temp file
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(mode='w+', prefix="expr", suffix='.nix', delete=False) as tmp:
            tmp.write(path)
            tmp_path = tmp.name
        new_path = tmp_path
        print("🔍 Searching for builder functions in expression.")
    else:
        new_path = _validate_path(path)
        print("🔍 Searching for builder functions in:", new_path)

    builder_data = {}
    patterns = _generate_patterns() if not cache else [b.split(".")[-1] for b in cache]
    for pattern in patterns:
        cmd = [
            'rg', 
            '--type', 'nix',           # Only search .nix files
            '--only-matching',         # Only show the matched part
            '--with-filename',         # Show filenames
            '--no-line-number',        # Don't show line numbers
            pattern,
            str(new_path)
        ]
        
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10 # prevent hanging
            )
            
            if result.returncode == 0:
                # Track which files each function appears in
                matches = result.stdout.strip().split('\n')
                for match in matches:
                    if match.strip() and ':' in match:  # Skip empty lines and ensure format
                        filename, full_match = match.split(':', 1)
                        full_match = full_match.strip()
                        function_name = full_match.split('.')[-1]
                        if function_name not in builder_data:
                            builder_data[function_name] = set()
                        builder_data[function_name].add(filename)
                        
        except subprocess.TimeoutExpired:
            print(f"Warning: Search timed out for pattern {pattern}")
            continue
        except subprocess.CalledProcessError:
            continue # Pattern might not match anything, continue with next pattern
    # Clean up temp file if created
    if not os.path.exists(path):
        os.remove(new_path)

    if not cache:
        # Filter for functions that appear in more than one file and apply blacklist
        filtered_functions = additional_functions.copy()
        for func, files in builder_data.items():
            if len(files) > 1 and func not in blacklist_functions:
                filtered_functions.append(func)

        # Find qualified paths for the functions
        langs = _list_language_frameworks()
        qualified_functions = []

        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future_to_func = {
                executor.submit(_find_qualified_path, func, helper, langs): func 
                for func in filtered_functions
            }
            for future in concurrent.futures.as_completed(future_to_func):
                try:
                    result = future.result()
                    qualified_functions.append(result)
                except Exception as e:
                    raise RuntimeError(f"Error processing function {future_to_func[future]}: {e}")
    else:
        map = {b.split(".")[-1]: b for b in cache}
        qualified_functions = [map[b] for b in set(builder_data.keys())]
    
    # Sort results for consistent output
    sorted_functions = sorted(qualified_functions)
    if sorted_functions:
        return sorted_functions
    else:
        return None

def _find_qualified_path(function_name: str, helper_map: dict, langs: List[str]) -> str:
    """
    Attempt to find the fully qualified path of a builder function in nixpkgs.
    Uses the lang_map to determine the language set if available.

    Args:
        function_name: The builder function name (e.g., buildPythonPackage)
        lang_map: Hardcoded language mapping for special cases (e.g. mkDerivation -> stdenv)
        nixpkgs_path: Path to the nixpkgs source
    """
    import re, subprocess
    from vibenix.tools.search_nixpkgs_manual_documentation import _search_keyword_in_documentation
    def _test_language(lang: str) -> Optional[str]:
        test_paths = [
            f'pkgs',                    # pkgs.buildGoModule
            f'pkgs.{lang}Packages',     # pkgs.pythonPackages.buildPythonPackage
            f'pkgs.{lang}',             # pkgs.crystal.buildCrystalPackage
            f'pkgs.{lang}Utils',        # pkgs.kakouneUtils.buildKakounePlugin
            f'pkgs.{lang}Plugins',      # pkgs.?
        ]
        return _try_eval_path_nix(test_paths, function_name)

    def _try_eval_path_nix(paths: List[str], function_name: str) -> str | None:
        from vibenix import config
        cmd = [
            'nix',
            'eval',
            '--impure',
            '--expr',
            f"let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}}; candidates = [{" ".join(f"{{name=\"{path}\"; set=(let r = builtins.tryEval ({path}{" or null" if path != "pkgs" else ""}); in if r.success then r.value else null);}}" for path in paths)}]; found = builtins.filter (c: c.set != null && c.set ? {function_name}) candidates; in if found != [] then (builtins.head found).name else false"
        ]
        try:
            result = subprocess.run(cmd, cwd=config.template_dir, capture_output=True, text=True, check=True)
            if result.returncode == 0 and result.stdout.strip() != 'false':
                return f"{result.stdout.strip().strip('"')}.{function_name}"
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            return None

    # Extract first capitalized segment
    match = re.search(r'[A-Z][a-z0-9]*', function_name)
    if not match:
        raise ValueError(f"Could not extract language from function name: '{function_name}'")
    l = match.group().lower()
    langs = [l]+langs
    if l in helper_map: # Hardcoded mappings
        langs = [helper_map[l]]+langs
    elif l not in langs: # Search builder function across documentation to guess lang
        lang_file = _search_keyword_in_documentation(function_name)
        lang_match = re.search(r'found in: ([a-z]+(?:, [a-z]+)*) documentation.', lang_file)
        if lang_match:
            langs = [lang_match.group(1).split(',')[0].strip()]+langs

    for lang in langs:
        qualified_path = _test_language(lang)
        if qualified_path:
            return qualified_path
    raise ValueError(f"Could not find qualified path for function: '{function_name}' ([{langs[0]}, {langs[1]}, ...])")


def _create_find_similar_builder_patterns(cache: List[str]):
    """Factory function that returns find_similar_builder_patterns with cache captured in closure."""
    from vibenix.flake import get_package_contents
    @log_function_call("find_similar_builder_patterns")
    def find_similar_builder_patterns(builders: List[str] = None, keyword: str = None) -> str:
        """
        CRITICAL FOR MULTI-LANGUAGE PROJECTS AND CONSULTING SIMILAR PACKAGES ON KEYWORD USAGE.
        Searches nixpkgs for packages that use any combination of builder functions (e.g. `buildNpmPackage`, `buildNpmPackage + buildPythonPackage`), and optionally that include the given keyword.
        This is the primary tool to understand how to structure a complex package that uses multiple builders.
        But also, to understand how similar packages use a specific keyword or attribute (e.g. `cargoLock`, `wrapQtAppsHook`).
        
        Args:
            builders: (Optional) list of builder functions (in their fully qualified form). By default, the builders used in the current packaging expression.
            keyword: (Optional) packaging keyword to filter packages by (e.g., a dependency name)
            
        Returns:
            Returns the existing builder combinations in nixpkgs and file paths to respective packages for inspection.
        """
        if not builders:
            builders = _extract_builders(get_package_contents(), cache)
            if not builders:
                return "Unable to determine currently used builder functions in packaging expression."
        else:
            # Get the fully qualified names for the provided builders in case they are not (models might ignore this instruction)
            qualified_builders = _get_builder_functions()
            builder_map = {b.split('.')[-1]: b for b in qualified_builders}
            parsed_builders = []
            for b in builders:
                name = b.split('.')[-1]
                if builder_map.get(name, None) is None:
                    return f"Specified builder function '{b}' is not recognized in nixpkgs. Choose from:\n{str(_get_builder_functions())}\n\n"
                parsed_builders.append(builder_map[name])
            builders = parsed_builders

        print(f"📞 Function called: find_similar_builder_patterns with builders: {builders}{' and keyword: ' + keyword if keyword else ''}")
        return _get_builder_combinations(builders, keyword)
    return find_similar_builder_patterns

def _get_builder_combinations(chosen_builders: List[str], keyword: str = None) -> str:
    try:
        nixpkgs_path = get_nixpkgs_source_path()
    except Exception as e:
        raise RuntimeError(f"Failed to get nixpkgs source path: {e}")

    if len(chosen_builders) < 1:
        return "At least one builder function must be specified."
    elif len(chosen_builders) > 5:
        return "A maximum of 5 builder functions can be specified."
    
    all_builders = list(set(chosen_builders))
    
    print(f"Analyzing nixpkgs for builder usage: {all_builders}")
    
    from collections import defaultdict
    
    import subprocess
    from pathlib import Path
    # Search for each builder function in .nix files
    all_package_to_builders = defaultdict(set)
    keyword_package_to_builders = defaultdict(set)
    
    for builder in all_builders:
        function_name = builder.split('.')[-1]  # e.g., mkDerivation
        try:
            # Get all packages with this builder
            result = subprocess.run([
                "rg",
                "--type", "nix",
                "--files-with-matches",
                rf"\b{function_name}\b",
                nixpkgs_path
            ], capture_output=True, text=True, check=True)
            
            files = result.stdout.strip().split('\n') if result.stdout.strip() else []
            for file_path in files:
                rel_path = str(Path(file_path).relative_to(nixpkgs_path))
                all_package_to_builders[rel_path].add(builder)
            
            if keyword:
                cmd = [
                    "bash", "-c",
                    f"rg --type nix --files-with-matches '\\b{function_name}\\b' '{nixpkgs_path}' | xargs -r -I {{}} rg -l '\\b{keyword}\\b' '{{}}'"
                ]
                result = subprocess.run(cmd, capture_output=True, text=True)
                filtered_files = result.stdout.strip().split('\n') if result.stdout.strip() else []
                for file_path in filtered_files:
                    rel_path = str(Path(file_path).relative_to(nixpkgs_path))
                    keyword_package_to_builders[rel_path].add(builder)
                
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Error searching for builder '{builder}': {e}")
    
    # Generate combinations and their frequencies
    all_combination_counts = defaultdict(set)
    keyword_combination_counts = defaultdict(set)
    
    for package, builders in all_package_to_builders.items():
        if len(builders) > 0:
            combination_name = " + ".join(sorted(builders))
            all_combination_counts[combination_name].add(package)
    
    if keyword:
        for package, builders in keyword_package_to_builders.items():
            if len(builders) > 0:
                combination_name = " + ".join(sorted(builders))
                keyword_combination_counts[combination_name].add(package)
    
    sorted_combinations = sorted(
        all_combination_counts.items(),
        key=lambda x: len(x[1]),
        reverse=True # Descending order
    )
    if not sorted_combinations:
        return "No packages found with the specified builders and keyword."
    
    # Format results
    result_lines = []
    result_lines.append("= BUILDER FUNCTION COMBINATION ANALYSIS =")
    
    iter = 0
    for combination, all_packages in sorted_combinations:
        if keyword:
            packages_to_show = keyword_combination_counts.get(combination, set())
        else:
            packages_to_show = all_packages
        result_lines.append(f"\n{combination} ({len(all_packages)} total packages){f" ({len(packages_to_show)} with keyword '{keyword}')" if keyword else ""}:")
        result_lines.append("-" * (len(combination) + 20))
        
        # Randomize package order and limit to first 5 for readability
        import random
        randomized_packages = list(packages_to_show)
        PACKAGE_LIMIT = 5
        random.shuffle(randomized_packages)
        result_lines.extend([f"  {package}" for package in randomized_packages[:PACKAGE_LIMIT]])
        
        if len(randomized_packages) > PACKAGE_LIMIT and len(randomized_packages) - PACKAGE_LIMIT > 0:
            result_lines.append(f"  ... and {len(randomized_packages) - PACKAGE_LIMIT} more packages")
        iter += 1
    result_lines.append("\nUse `nixpkgs_read_file_contents` to inspect any of the above packages.")
    if not keyword:
        result_lines.append("No other combinations between the chosen builders are present in nixpkgs.")
    
    return "\n".join(result_lines)
