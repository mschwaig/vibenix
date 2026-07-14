"""Maintenance mode for vibenix - analyze and fix existing Nix package directories."""

from pathlib import Path
import subprocess
import re
from typing import Optional
from vibenix.ui.logging_config import logger

from vibenix.ccl_log import init_logger, get_logger, close_logger, enum_str
from vibenix.git_info import get_git_info
from vibenix.ui.conversation import coordinator_message, coordinator_error, coordinator_progress
from vibenix.ui.conversation_templated import get_model_prompt_manager
from vibenix.flake import init_flake, get_package_contents, get_current_system, get_package_path
from vibenix.packaging_flow.model_prompts import analyze_package_failure, classify_packaging_failure, PackagingFailure
from vibenix.packaging_flow.refinement import refine_package
from vibenix import config


def _rewrite_src_ref_attributes(file_path: Path, line_num: int, replacement: str,
                                keys: tuple[str, ...], force_rev_key: bool = False) -> bool:
    """Rewrite matching src reference attributes and return whether any replacement was made."""
    with open(file_path, 'r+') as f:
        lines = f.readlines()
        depth, started = 0, False
        replaced = False
        key_pattern = "|".join(re.escape(k) for k in keys)

        for i in range(line_num - 1, len(lines)):
            depth += lines[i].count('{') - lines[i].count('}')
            if '{' in lines[i]:
                started = True

            match = re.match(rf'^(\s*)({key_pattern})\s*=.*', lines[i])
            if match:
                indent, key = match.groups()
                target_key = "rev" if force_rev_key else key
                lines[i] = f'{indent}{target_key} = "{replacement}";\n'
                replaced = True

            if started and depth <= 0:
                break

        if replaced:
            f.seek(0)
            f.truncate()
            f.writelines(lines)

    return replaced


def _run_nix_update(project_url: Optional[str], revision: Optional[str], version: Optional[str],
                    force_version_only: bool = False) -> str:
    """Run nix-update using current maintenance update strategy.
    Force_version_only is used for the last fallback attempt when the initial nix-update attempts fail.
    """
    def execute_nix_update(version_arg: Optional[str]) -> subprocess.CompletedProcess[str]:
        cmd = ['nix-update', 'default', '--flake', '--src-only'] + \
            ([f'--version={version_arg}'] if version_arg else []) + \
            ([f'--url={project_url}'] if project_url else [])

        coordinator_message(f"Running nix-update with command: {' '.join(cmd)}")
        return subprocess.run(
            cmd,
            cwd=config.flake_dir,
            capture_output=True,
            text=True,
            check=True
        )

    try:
        is_hash = bool(re.fullmatch(r'[0-9a-f]{7,40}', revision, re.IGNORECASE)) if revision else False
        if is_hash and not force_version_only:
            from vibenix.flake import get_attr_pos
            src_pos = get_attr_pos("src")
            if src_pos is None:
                raise RuntimeError("Could not locate src attribute in package.nix")
            _rewrite_src_ref_attributes(
                Path(get_package_path()),
                src_pos,
                "dummy",
                ("rev", "tag", "branch", "release"),
                force_rev_key=True
            )

        version_args: list[Optional[str]] = []
        if force_version_only:
            version_args.append(version)
        elif not is_hash:
            if version:
                version_args.append(f"{version}")
            if revision:
                fallback_version_arg = f"{revision}"
                if fallback_version_arg not in version_args:
                    version_args.append(fallback_version_arg)
        elif revision:
            version_args.append(f"branch={revision}")

        if not version_args:
            version_args.append(None)

        from vibenix.flake import stash_flake_lock
        stash_flake_lock()
        coordinator_message("Stashed flake.lock file to prevent nix-update from modifying it.")

        last_error: Optional[subprocess.CalledProcessError] = None
        for version_arg in version_args:
            try:
                res = execute_nix_update(version_arg)
                break
            except subprocess.CalledProcessError as e:
                last_error = e
                error_details = e.stderr if hasattr(e, 'stderr') else str(e)
                coordinator_message(f"nix-update attempt failed with version arg {version_arg}: {error_details}")
        else:
            if last_error:
                raise last_error
            raise RuntimeError("nix-update failed without an explicit subprocess error.")

        if res.returncode != 0:
            coordinator_error(f"nix-update failed: {res.stderr}")
            raise RuntimeError(f"nix-update failed: {res.stderr}")
    except subprocess.CalledProcessError as e:
        coordinator_error(f"nix-update execution error: {e.stderr if hasattr(e, 'stderr') else str(e)}")
        raise RuntimeError(f"nix-update execution error: {e.stderr if hasattr(e, 'stderr') else str(e)}")
    finally:
        from vibenix.flake import unstash_flake_lock
        coordinator_message("Restoring flake.lock file after nix-update.")
        unstash_flake_lock()

    return res.stdout.strip()


def update_fetcher(project_url: Optional[str], revision: Optional[str],
                   version: Optional[str], update_lock: Optional[bool] = False) -> str:
    """Update the fetcher in package.nix to reflect project updates (default: latest rev) and replaces it in package.nix."""
    coordinator_progress("Updating fetcher in package.nix")
    try:
        _run_nix_update(project_url, revision, version) # This updates the fetcher in package.nix directly
    except RuntimeError:
        if revision:
            coordinator_message("Initial nix-update failed. Rewriting src refs to revision and retrying with version.")
            from vibenix.flake import get_attr_pos
            src_pos = get_attr_pos("src")
            if src_pos is None:
                raise RuntimeError("Could not locate src attribute in package.nix")
            _rewrite_src_ref_attributes(
                Path(get_package_path()),
                src_pos,
                revision,
                ("rev", "tag", "branch", "release")
            )
            _run_nix_update(project_url, revision, version, force_version_only=True)
        else:
            raise

    from vibenix.flake import update_flake
    package_contents = get_package_contents()
    # TODO unused project_url currently 
    update_flake(package_contents, commit_msg="init: nix-update")
    return package_contents


def update_lock_file() -> None:
    """Update the flake.lock file using `nix flake update`."""
    coordinator_progress("Updating flake.lock file using `nix flake update`")
    # TODO only update nixpkgs input
    try:
        result = subprocess.run(
            [ "nix", "flake", "update" ],
            cwd=config.flake_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        if result.returncode != 0:
            ccl_logger = get_logger()
            ccl_logger.write_kv("nix_flake_update_error", result.stderr)
            ccl_logger.leave_attribute(log_end=True)
            raise RuntimeError(f"{result.stderr}")

        coordinator_message("Successfully updated flake.lock file.")
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
        raise RuntimeError(f"Failed to update flake.lock file: {e}")

def fetch_project_src() -> str:
    """Fetch and unpack the project source to the Nix store.
    
    Returns:
        The store path to the unpacked source directory.
    """
    coordinator_progress("Fetching and unpacking project source to Nix store")
    ccl_logger = get_logger()
    ccl_logger.enter_attribute("load_fetcher", log_start=True)

    # Build a minimal derivation that fetches and unpacks the source
    # Similar to evaluate_fetcher_content but uses the src from the local flake directly
    nix_expr = f"""
let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}};
    localFlake = builtins.getFlake (toString ./.);
    packageDrv = localFlake.packages.${{builtins.currentSystem}}.default;
in with pkgs; stdenv.mkDerivation {{
  # Inherit pname, version, and src from the local package
  pname = packageDrv.pname or "package";
  version = packageDrv.version or "unknown";
  src = packageDrv.src;
  
  sourceRoot = ".";
  dontBuild = true;
  dontConfigure = true;
  dontFixup = true;
  installPhase = ''
    shopt -s dotglob nullglob
    entries=(*)
    # Filter out env-vars file
    filtered=()
    for entry in "''${{entries[@]}}"; do
      if [[ "''$entry" != "env-vars" ]]; then
        filtered+=("''$entry")
      fi
    done
    # If there's exactly one directory, copy its contents
    if [[ ''${{#filtered[@]}} -eq 1 && -d "''${{filtered[0]}}" ]]; then
      cp -r "''${{filtered[0]}}/." $out
    else
      # Multiple items or not a single directory, copy everything except env-vars
      for entry in "''${{filtered[@]}}"; do
        cp -r "''$entry" $out/
      done
    fi
  '';
}}
"""
    cmd = [
        'nix',
        'build',
        '--impure',
        '--print-out-paths',
        '--expr',
        nix_expr
    ]
    try:
        result = subprocess.run(cmd, cwd=config.flake_dir, capture_output=True, text=True, check=True)
        # Extract the store path from the output
        store_path = result.stdout.strip()
        ccl_logger.write_kv("fetcher_store_path", store_path)

        # If the store path contains only a single directory (ignoring hidden files),
        # navigate into it automatically so README detection and file tools work correctly
        from pathlib import Path as PathLib
        store_path_obj = PathLib(store_path)
        entries = list(store_path_obj.iterdir())
        non_hidden_dirs = [e for e in entries if e.is_dir() and not e.name.startswith('.')]

        if len(non_hidden_dirs) == 1 and len([e for e in entries if not e.name.startswith('.')]) == 1:
            # Only one non-hidden directory exists - use it as the source root
            store_path = str(non_hidden_dirs[0])
            ccl_logger.write_kv("auto_navigated_to", store_path)
        
        ccl_logger.leave_attribute(log_end=True)
        return store_path
    except subprocess.CalledProcessError as e:
        error_details = e.stderr if hasattr(e, 'stderr') else str(e)
        ccl_logger.write_kv("nix_eval_error", error_details)
        ccl_logger.leave_attribute(log_end=True)
        raise RuntimeError(f"Failed to evaluate project fetcher:\n{error_details}")
    except subprocess.TimeoutExpired as e:
        ccl_logger.write_kv("nix_eval_timeout", str(e))
        ccl_logger.leave_attribute(log_end=True)
        raise RuntimeError(f"Timeout evaluating project fetcher: {e}")

def save_package_output(package_directory: Path, output_dir: str) -> None:
    """Save the fixed package.nix and accompanying files to the output directory.
       Only copies essential files and excludes build artifacts.
    """
    import shutil
    output_path = Path(output_dir).resolve()
    exclude = {'result', 'vm-task', 'packages.nix', '.git', '.gitignore'}

    def ignore(path, names):
        # Ignore if name is in set or starts with .git, or if the actual path is a symlink
        return [n for n in names if n in exclude or n.startswith(".git") 
                or (Path(path) / n).is_symlink()]

    shutil.copytree(package_directory, output_path, dirs_exist_ok=True, ignore=ignore)
    coordinator_message(f"Saved updated package.nix to: {output_path / 'package.nix'}")

def create_nixpkgs_function_calls(initial_path: str):
    """
    Create all the file tools, but wrap them with a method that updates
    the root_dir variable of the class to the current nixpkgs outPath.
    """
    from vibenix.tools.file_tools import create_source_function_calls
    funcs = create_source_function_calls(
        store_path=initial_path,
        prefix="nixpkgs_",
        dynamic_path=True
    )
    funcs, update_path = funcs[:-1], funcs[-1]
    
    import functools
    def decorate_dynamic_path_update(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            from vibenix.packaging_flow.run import get_nixpkgs_source_path
            update_path(get_nixpkgs_source_path())
            result = func(*args, **kwargs)
            return result
        return wrapper

    decorated_funcs = [decorate_dynamic_path_update(f) for f in funcs]
    return decorated_funcs


def run_maintenance(maintenance_dir: str, output_dir: Optional[str] = None,
                    revision: Optional[str] = None, version: Optional[str] = None,
                    upgrade_lock: bool = False, update_lock: bool = True) -> Optional[str]:
    """Run maintenance mode on an existing Nix package directory.

    This mode is for analyzing and fixing existing package directories that
    contain flake.nix and package.nix (flake.lock optional), rather than
    packaging new projects from scratch.

    Args:
        maintenance_dir: Directory containing the Nix files to analyze/fix
        output_dir: Directory to save the fixed package.nix file
        revision: Optional project revision to upgrade to
        version: Optional project version string to set in package.nix (requires revision) (default: revision value)
        upgrade_lock: Whether to upgrade/bump the nixpkgs release used
        update_lock: Whether to update the flake.lock file if present
    """
    maintenance_path = Path(maintenance_dir).resolve()

    if not maintenance_path.exists():
        logger.error(f"Maintenance directory not found: {maintenance_path}")
        raise FileNotFoundError(f"Directory not found: {maintenance_path}")

    if not maintenance_path.is_dir():
        logger.error(f"Maintenance path must be a directory, not a file: {maintenance_path}")
        raise ValueError(f"Expected a directory, got a file: {maintenance_path}")

    def _validate_required_file(filename: str) -> Path:
        file_path = maintenance_path / filename

        if not file_path.exists():
            logger.error(f"Required maintenance file missing: {file_path}")
            raise FileNotFoundError(f"File not found: {file_path}")

        if not file_path.is_file():
            logger.error(f"Expected a file but found a directory at: {file_path}")
            raise ValueError(f"Expected a file, got a directory: {file_path}")

        return file_path

    # Initialize CCL logger
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        log_file = output_path / "run.ccl"
    else:
        log_file = Path("run.ccl")
    ccl_logger = init_logger(log_file)

    # Log vibenix version info
    git_info = get_git_info()
    if git_info["commit_hash"]:
        ccl_logger.enter_attribute("vibenix_version")
        ccl_logger.write_kv("git_hash", git_info["commit_hash"])
        ccl_logger.write_kv("dirty", "true" if git_info["is_dirty"] else "false")
        ccl_logger.leave_attribute()

    coordinator_message("Welcome to vibenix!") ###

    package_nix_path = _validate_required_file("package.nix")
    try:
        flake_lock_path = _validate_required_file("flake.lock")
    except FileNotFoundError:
        flake_lock_path = None
    try:
        flake_nix_path = _validate_required_file("flake.nix")
    except FileNotFoundError:
        flake_nix_path = None

    coordinator_message(f"Validated required files: flake.nix at {flake_nix_path}, package.nix at {package_nix_path}")
    if flake_lock_path is None:
        coordinator_message("flake.lock not provided; continuing without it") # TODO make
    if flake_nix_path is None:
        coordinator_message("flake.nix not provided; continuing without it") # TODO make

    coordinator_message(f"Starting maintenance mode for: {maintenance_path}")

    # Log model configuration
    from vibenix.model_config import get_model_config
    ccl_logger.log_model_config(get_model_config())
    # Log vibenix settings
    ccl_logger.log_vibenix_settings()

    # Initialize flake early (needed for fetcher evaluation in CSV mode)
    coordinator_progress("Setting up a temporary Nix flake for packaging")
    init_flake(reference_dir=maintenance_path)
    coordinator_message(f"Working on temporary flake at {config.flake_dir}")

    if revision:
        update_fetcher(None, revision, version, update_lock=update_lock) # Update package src in the package.nix
    store_path = fetch_project_src() # Fetch and unpack project source to nix store
    if update_lock:
        coordinator_message("Updating flake.lock.")
        update_lock_file()
        # upgrade_lock_file() # match closest nixpkgs release # Not doing this anymore

    # Create additional (runtime-initialized) tools for model
    from vibenix.packaging_flow.run import get_nixpkgs_source_path, create_source_function_calls
    nixpkgs_path = get_nixpkgs_source_path()
    project_functions = create_source_function_calls(store_path, "project_")
    nixpkgs_functions = create_nixpkgs_function_calls(nixpkgs_path) # Dynamic nixpkgs path update
    from vibenix.tools.search_related_packages import get_builder_functions, \
     _create_find_similar_builder_patterns
    find_similar_builder_patterns = _create_find_similar_builder_patterns(use_cache=True)
    additional_functions = project_functions + nixpkgs_functions + [get_builder_functions, find_similar_builder_patterns]
    # Initialize said tools via settings manager
    from vibenix.ui.conversation_templated import get_settings_manager
    get_settings_manager().initialize_additional_tools(additional_functions)

    # Analyze project to obtain summary used for model prompts
    # Use project source root's README.md + root directory file list as information sources
    coordinator_message("Using project source files to analyze the project.")
    try:
        from vibenix.tools.file_tools import get_project_source_info
        source_info = get_project_source_info(store_path)
    except Exception as e:
        coordinator_error(f"Failed to acquire project summary from source: {e}")
        return

    ccl_logger.log_project_summary_begin()
    from vibenix.packaging_flow.run import analyze_project
    summary = analyze_project(source_info=source_info) # TODO add current packaging code as context?
    ccl_logger.log_project_summary_end(summary)

    if not summary:
        coordinator_error("Model failed to produce a project summary.")

    # Agentic loop
    coordinator_progress("Testing the initial build...")
    from vibenix.nix import execute_build_and_add_to_stack, revert_packaging_to_solution
    best = execute_build_and_add_to_stack(get_package_contents())

    from vibenix.tools.view import _view as view_package_contents 
    ccl_logger.log_initial_build(view_package_contents(), best.result)
    
    from vibenix.packaging_flow.packaging_loop import packaging_loop 
    candidate, best, status = packaging_loop(
        best,
        summary,
        maintenance_mode=True
    )

    # Log the raw package code before refinement or analysis
    ccl_logger.write_kv("raw_package", candidate.code)
    packaging_usage = get_model_prompt_manager().get_session_usage()
    ccl_logger.log_packaging_loop_cost(
        packaging_usage.calculate_cost(),
        packaging_usage.prompt_tokens,
        packaging_usage.completion_tokens,
        packaging_usage.cache_read_tokens
    )
    
    if candidate.result.success:
        coordinator_message("Build succeeded!")
        if get_settings_manager().get_setting_enabled("refinement.enabled"):
            candidate = refine_package(candidate, summary, output_dir, maintenance_mode=True)
            ccl_logger.write_kv("refined_package", candidate.code)

            refinement_usage = get_model_prompt_manager().get_session_usage() - packaging_usage
            ccl_logger.log_refinement_cost(
                packaging_usage.calculate_cost(),
                refinement_usage.calculate_cost(),
                refinement_usage.prompt_tokens,
                refinement_usage.completion_tokens,
                refinement_usage.cache_read_tokens
            )
        
        ccl_logger.log_total_tool_cost()
        # Always log success and return, regardless of refinement outcome
        session_usage = get_model_prompt_manager().get_session_usage()
        ccl_logger.log_session_end(signal=None, total_cost=get_model_prompt_manager().get_session_cost(),
                total_input_tokens=session_usage.prompt_tokens,
                total_output_tokens=session_usage.completion_tokens,
                total_cache_read_tokens=session_usage.cache_read_tokens
        )
        if output_dir:
            from vibenix.flake import get_package_path
            package_directory = Path(get_package_path()).parent
            save_package_output(package_directory, output_dir)
        close_logger()
        return candidate.code  
    else:
        max_iterations = get_settings_manager().get_setting_value("packaging_loop.max_iterations")
        max_consecutive_non_build_errors = get_settings_manager().get_setting_value("packaging_loop.max_consecutive_non_build_errors")
        if status['consecutive_non_build_errors'] >= max_consecutive_non_build_errors:
            coordinator_error(f"Aborted: {status['consecutive_rebuilds_without_progress']} consecutive rebuilds without progress.")
        else:
            coordinator_error(f"Reached MAX_ITERATIONS build iteration limit of {max_iterations}.")
    
    ccl_logger.enter_attribute("analyze_failure")
    revert_packaging_to_solution(best) # TODO do this differently later, not just best
    details = analyze_package_failure(best.code, best.result.error.truncated(), summary) # TODO best.code
    ccl_logger.write_kv("description", str(details))
    packaging_failure = classify_packaging_failure(details)
    ccl_logger.write_kv("failure_type", enum_str(packaging_failure))
    ccl_logger.leave_attribute()
    if isinstance(packaging_failure, PackagingFailure):
        coordinator_message(f"Packaging failure type: {packaging_failure}\nDetails:\n{details}\n")

    ccl_logger.log_total_tool_cost()
    session_usage = get_model_prompt_manager().get_session_usage()
    ccl_logger.log_session_end(signal=None, total_cost=get_model_prompt_manager().get_session_cost(),
            total_input_tokens=session_usage.prompt_tokens,
            total_output_tokens=session_usage.completion_tokens,
            total_cache_read_tokens=session_usage.cache_read_tokens
    )
    close_logger()
    return None

