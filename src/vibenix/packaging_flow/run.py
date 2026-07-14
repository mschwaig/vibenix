"""Business logic for vibenix using the coordinator pattern."""

import re
import subprocess
from pathlib import Path
from typing import Optional

from vibenix.ui.conversation import coordinator_message, coordinator_error, coordinator_progress
from vibenix.parsing import fetch_github_release_data, scrape_and_process, fill_src_attributes, get_store_path
from vibenix.flake import init_flake, get_package_contents
from vibenix.nix import execute_build_and_add_to_stack, revert_packaging_to_solution
from vibenix.packaging_flow.model_prompts import (
    pick_template, summarize_project_source,
    analyze_package_failure, classify_packaging_failure, PackagingFailure,
    choose_builders, compare_template_builders, get_model_prompt_manager
)
from vibenix.packaging_flow.refinement import refine_package
from vibenix.packaging_flow.user_prompts import get_project_url
from vibenix import config
from vibenix.tools.file_tools import create_source_function_calls
from vibenix.ccl_log import init_logger, get_logger, close_logger, enum_str
from vibenix.git_info import get_git_info
from vibenix.tools.view import _view as view_package_contents
from vibenix.defaults.vibenix_settings import get_settings_manager
from vibenix.template.template_types import TemplateType


def _get_nixpkgs_source_path() -> str:
    """Internal function to get the nixpkgs source path from the initialized flake.

    This is the implementation without decorators, used internally and by tool wrappers.
    """
    try:
        result = subprocess.run(
            ["nix", "build", ".#nixpkgs-src", "--no-link", "--print-out-paths"],
            cwd=config.flake_dir,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        coordinator_error(f"Failed to get nixpkgs source path: {e}")
        raise


def get_nixpkgs_source_path() -> str:
    """Get the nixpkgs source path from the initialized flake."""
    return _get_nixpkgs_source_path()



def analyze_project(source_info: Optional[tuple[str, str]]=None) -> str:
    """Analyze the project using the model."""
    if source_info:
        return summarize_project_source(*source_info)
    raise ValueError("No project information provided for analysis.")

def get_release_data_and_version(url, rev=None):
    """Fetch release data and compute version string with proper logging."""
    from vibenix.ccl_log import get_logger
    ccl_logger = get_logger()
    
    ccl_logger.enter_attribute("get_release_data_from_forge", log_start=True)
    ccl_logger.write_kv("forge", "github")
    ccl_logger.write_kv("url", url)
    
    # Fetch latest release if rev not provided
    if not rev:
        rev = fetch_github_release_data(url)
        ccl_logger.write_kv("latest_gh_release_tag", rev)
    
    # Compute version based on whether we have a rev
    if rev:
        version = rev[1:] if rev.startswith('v') else rev
        ccl_logger.write_kv("extracted_version", version)
    else:
        version = None
    ccl_logger.leave_attribute(log_end=True)
    return rev, version

def run_nurl(url, rev=None, finalAttrs: bool=False, extra_args: str=None):
    """Run nurl command and return the version and fetcher."""
    backoff_time = 5  # seconds
    try:
        from vibenix.ccl_log import get_logger
        ccl_logger = get_logger()
        ccl_logger.enter_attribute("pin_fetcher", log_start=True)
        
        # Get release data and version
        rev, version = get_release_data_and_version(url, rev)

        cmd = ['nurl', url, rev] if rev else ['nurl', url]
        if extra_args:
            cmd += extra_args.split()
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        fetcher = result.stdout.strip()

        # Format fetcher with version
        ccl_logger.write_kv("fetcher", fetcher)
        if version:
            fetcher = fetcher.replace(version, "${"+ f"{'finalAttrs.' if finalAttrs else ''}" + "version}") 
        else:
            version = "unstable-${src.rev}"
        ccl_logger.write_kv("version", version)
        
        ccl_logger.leave_attribute(log_end=True)
        return version, fetcher
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.lower()
        # Check for various rate limit indicators
        if any(indicator in error_output for indicator in ["rate limit", "429", "403", "forbidden"]):
            print(f"Rate limit/forbidden error for {url}")
            import time
            time.sleep(backoff_time)
            backoff_time *= 2  # Exponential backoff
            if backoff_time >= 360:
                coordinator_error("Exceeded maximum backoff time due to repeated rate limiting.")
                return None, "RATE_LIMITED"
        print(f"Error running nurl for {url}: {e.stderr}")
        return None, None
    except FileNotFoundError:
        print("Error: nurl command not found. Please ensure nurl is installed.")
        return None, None


def read_fetcher_file(fetcher: str) -> tuple[str, str]:
    """Read the fetcher file content and extract version (URL-based mode).

    This function is used in URL-based mode where version needs to be extracted
    from the fetcher content.
    """
    from pathlib import Path
    try:
        ccl_logger = get_logger()
        ccl_logger.enter_attribute("load_fetcher", log_start=True)
        ccl_logger.write_kv("path", fetcher)

        path = Path(fetcher)
        with open(path, 'r') as f:
            # Ignore comments and empty lines
            content = "".join(line for line in f if line.strip() and not line.startswith("#")).rstrip()
        ccl_logger.write_kv("fetcher", content)

        # Instantiate fetcher to pull contents to nix store TODO theres probably definetly a better way
        cmd = [
            'nix',
            'build',
            '--impure',
            '--expr',
            f"let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}}; in\nwith pkgs; {content}"
        ]
        try:
            result = subprocess.run(cmd, cwd=config.flake_dir, capture_output=True, text=True, check=True)
            if result.returncode != 0:
                ccl_logger.write_kv("nix_eval_error", result.stderr)
                ccl_logger.leave_attribute(log_end=True)
                raise RuntimeError(f"{result.stderr}")
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            raise RuntimeError(f"Failed to evaluate project fetcher: {e}")

        # Extract version from the fetcher if present
        version_match = re.search(r'version\s*=\s*"([^"]+)"', content)
        version = version_match.group(1) if version_match else "unstable-${src.rev}"
        ccl_logger.write_kv("version", version)
        ccl_logger.leave_attribute(log_end=True)
        return version, content
    except FileNotFoundError:
        coordinator_error(f"Fetcher file '{fetcher}' not found.")
        raise
    except Exception as e:
        coordinator_error(f"Error reading fetcher file: {e}")
        raise


def read_fetcher_file_csv_mode(fetcher_path: str) -> str:
    """Read the fetcher file content (CSV-based mode).

    This function is used in CSV-based mode where pname and version are provided
    explicitly, so we only need to read and evaluate the fetcher content.
    """
    from pathlib import Path
    try:
        ccl_logger = get_logger()
        ccl_logger.enter_attribute("load_fetcher_csv", log_start=True)
        ccl_logger.write_kv("path", fetcher_path)

        path = Path(fetcher_path)
        with open(path, 'r') as f:
            # Ignore comments and empty lines
            content = "".join(line for line in f if line.strip() and not line.startswith("#")).rstrip()
        ccl_logger.write_kv("fetcher", content)

        # Instantiate fetcher to pull contents to nix store
        cmd = [
            'nix',
            'build',
            '--impure',
            '--expr',
            f"let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}}; in\nwith pkgs; {content}"
        ]
        try:
            result = subprocess.run(cmd, cwd=config.flake_dir, capture_output=True, text=True, check=True)
            if result.returncode != 0:
                ccl_logger.write_kv("nix_eval_error", result.stderr)
                ccl_logger.leave_attribute(log_end=True)
                raise RuntimeError(f"{result.stderr}")
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError) as e:
            raise RuntimeError(f"Failed to evaluate project fetcher: {e}")

        ccl_logger.leave_attribute(log_end=True)
        return content
    except FileNotFoundError:
        coordinator_error(f"Fetcher file '{fetcher_path}' not found.")
        raise
    except Exception as e:
        coordinator_error(f"Error reading fetcher file: {e}")
        raise


def evaluate_fetcher_content(content: str, version: str = None, pname: str = None) -> str:
    """Evaluate fetcher content directly (CSV dataset mode).

    This function is used when fetcher content is provided directly from CSV,
    not from a file. Pname and version are provided explicitly.

    Args:
        content: The fetcher expression
        version: Package version to use in the evaluation context
        pname: Package name to use in the evaluation context
    """
    try:
        ccl_logger = get_logger()
        ccl_logger.enter_attribute("evaluate_fetcher_csv", log_start=True)
        ccl_logger.write_kv("fetcher", content)

        # Wrap fetcher in an attrset with finalAttrs as an attribute to make finalAttrs.version and finalAttrs.pname available
        wrapped_expr = f'rec {{ pname = "{pname}"; finalAttrs.pname = pname; version = "{version}"; finalAttrs.version = version; src = {content}; }}'

        # Build a minimal derivation that unpacks the source
        # This ensures we get the actual unpacked source directory, even if the fetcher returns a tarball
        nix_expr = f"""
let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}};
in with pkgs; stdenv.mkDerivation {{
  inherit (({wrapped_expr})) pname version src;
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
            from pathlib import Path
            store_path_obj = Path(store_path)
            entries = list(store_path_obj.iterdir())
            non_hidden_dirs = [e for e in entries if e.is_dir() and not e.name.startswith('.')]

            if len(non_hidden_dirs) == 1 and len([e for e in entries if not e.name.startswith('.')]) == 1:
                # Only one non-hidden directory exists - use it as the source root
                store_path = str(non_hidden_dirs[0])
                ccl_logger.write_kv("auto_navigated_to", store_path)
        except subprocess.CalledProcessError as e:
            ccl_logger.write_kv("nix_eval_error", e.stderr)
            ccl_logger.leave_attribute(log_end=True)
            raise RuntimeError(f"Failed to evaluate project fetcher: {e.stderr}")
        except subprocess.TimeoutExpired as e:
            ccl_logger.write_kv("nix_eval_timeout", str(e))
            ccl_logger.leave_attribute(log_end=True)
            raise RuntimeError(f"Timeout evaluating project fetcher: {e}")

        ccl_logger.leave_attribute(log_end=True)
        return store_path
    except Exception as e:
        coordinator_error(f"Error evaluating fetcher content: {e}")
        raise


def compare_template(initial_code, find_similar_builder_patterns, summary) -> str:
    """Prompt LLM to compare template with set of builders it thinks are relevant,
    present builder combinations to model, and let it make changes if needed."""
    from vibenix.tools.search_related_packages import _extract_builders
    from vibenix.tools.search_related_packages import _get_builder_functions

    available_builders = _get_builder_functions()
    builders = choose_builders(available_builders, summary)
    if not choose_builders:
        raise RuntimeError("Model failed to choose builders for comparison.")
    builders_set = set(builder.split(".")[-1].strip("'\"") for builder in builders) # has happened it reply with '"pkgs.(...)"'
    template_builders = _extract_builders(initial_code, available_builders) or []
    template_builders = set(builder.split(".")[-1] for builder in template_builders)
    if len(builders) > 0 and builders_set != template_builders:
        # Get builder combinations and random set of packages for each
        builder_combinations = find_similar_builder_patterns(builders)
        coordinator_message(builder_combinations)
        # Let model analyse and make changes
        compare_template_builders(view_package_contents(prompt="compare_template_builders"), builder_combinations, summary)
        initial_code = get_package_contents()
        coordinator_message(f"Finished comparing builders to template.")
    else:
        coordinator_message("Builders chosen by model match template. Skipping comparison step.")
    return initial_code


def package_project(output_dir=None, project_url=None, revision=None, fetcher=None,
                    csv_pname=None, csv_version=None, fetcher_content=None):
    """Main coordinator function for packaging a project.

    Supports two modes:
    - URL-based mode: project_url is provided, fetcher/version are derived
    - CSV-based mode: csv_pname and csv_version are provided with fetcher or fetcher_content

    Args:
        fetcher_content: Direct fetcher content string (alternative to fetcher file path)
    """
    # Initialize CCL logger
    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        log_file = output_path / "run.ccl"
    else:
        log_file = Path("run.ccl")
    ccl_logger = init_logger(log_file)

    # Determine if we're in CSV-based mode
    csv_mode = csv_pname is not None and csv_version is not None

    # Log vibenix version info
    git_info = get_git_info()
    if git_info["commit_hash"]:
        ccl_logger.enter_attribute("vibenix_version")
        ccl_logger.write_kv("git_hash", git_info["commit_hash"])
        ccl_logger.write_kv("dirty", "true" if git_info["is_dirty"] else "false")
        ccl_logger.leave_attribute()

    # Step 1: Get project URL (includes welcome message)
    if not (project_url or fetcher or csv_mode):
        project_url = get_project_url() # Interactive mode (none)
    else: # (purl or fetcher or csv_mode)
        # When URL/fetcher/CSV is provided via CLI, still show welcome but skip prompt
        coordinator_message("Welcome to vibenix!")

    # Log model configuration
    from vibenix.model_config import get_model_config
    ccl_logger.log_model_config(get_model_config())
    # Log vibenix settings
    ccl_logger.log_vibenix_settings()

    if project_url:
        ccl_logger.write_kv("project_url", project_url)

    # Initialize flake early (needed for fetcher evaluation in CSV mode)
    coordinator_progress("Setting up a temporary Nix flake for packaging")
    init_flake()
    coordinator_message(f"Working on temporary flake at {config.flake_dir}")

    # Obtain the project fetcher and version based on mode
    if csv_mode:
        # CSV dataset mode: pname, version, and fetcher_content provided from CSV
        coordinator_progress(f"Using CSV dataset: pname={csv_pname}, version={csv_version}")
        ccl_logger.write_kv("csv_mode", "true")
        ccl_logger.write_kv("csv_pname", csv_pname)
        ccl_logger.write_kv("csv_version", csv_version)
        # fetcher_content is already set from CSV parsing in main.py
        store_path = evaluate_fetcher_content(fetcher_content, csv_version, csv_pname)
        pname = csv_pname
        version = csv_version
    elif fetcher:
        # Legacy fetcher file mode (URL-based): version extracted from file
        coordinator_progress(f"Using provided fetcher file: {fetcher}")
        version, fetcher_content = read_fetcher_file(fetcher)
        pname = None  # Will be extracted from fetcher later
        if revision:
            coordinator_error("Ignoring revision parameter in favor of provided fetcher.")
        store_path = get_store_path(fetcher_content)
    else:
        # URL-based mode: derive fetcher from URL
        if not project_url:
            coordinator_error("No project URL nor fetcher provided, either is required for Vibenix to proceed.")
            return
        coordinator_progress("Obtaining project fetcher from the provided URL")
        version, fetcher_content = run_nurl(project_url, revision)
        pname = None  # Will be extracted from fetcher later
        store_path = get_store_path(fetcher_content)

    # Step 2: Create additional (runtime-initialized) tools for model
    nixpkgs_path = get_nixpkgs_source_path()
    project_functions = create_source_function_calls(store_path, "project_")
    nixpkgs_functions = create_source_function_calls(nixpkgs_path, "nixpkgs_")
    from vibenix.tools.search_related_packages import get_builder_functions, \
     _create_find_similar_builder_patterns
    find_similar_builder_patterns = _create_find_similar_builder_patterns(use_cache=True)
    additional_functions = project_functions + nixpkgs_functions + [get_builder_functions, find_similar_builder_patterns]
    # Initialize said tools via settings manager
    get_settings_manager().initialize_additional_tools(additional_functions)

    # Step 3: Analyze project to obtain summary used for model prompts
    # Use project source root's README.md + root directory file list as information sources
    coordinator_message("Using project source files to analyze the project.")
    try:
        from vibenix.tools.file_tools import get_project_source_info
        source_info = get_project_source_info(store_path)
    except Exception as e:
        coordinator_error(f"Failed to acquire project summary from source: {e}")
        return

    ccl_logger.log_project_summary_begin()
    if get_settings_manager().get_setting_enabled("analyze_project"):
        summary = analyze_project(source_info=source_info)
        if not summary:
            coordinator_error("Model failed to produce a project summary.")
    else:
        summary = ""
        coordinator_message("Project analysis is disabled, proceeding with empty summary.")
    ccl_logger.log_project_summary_end(summary)

    # Step 4: Pick template
    ccl_logger.log_template_selected_begin()
    if get_settings_manager().get_setting_enabled("pick_template"):
        template_type = pick_template(get_settings_manager().get_enabled_templates(), summary)
        if not template_type:
            coordinator_error("Model failed to pick a template type.")
            return
    else:
        template_type = TemplateType.GENERIC
        coordinator_message("Template selection is disabled, defaulting to GENERIC template.")
    coordinator_message(f"Selected template: {template_type.value}")
    template_filename = f"{template_type.value}.nix"
    template_path = config.template_dir / template_filename
    starting_template = template_path.read_text()
    
    ccl_logger.log_template_selected_end(template_type, starting_template)

    # Step 6.a: Manual src setup
    coordinator_message("Setting up the src attribute in the template...")
    # Extract pname from fetcher if not already set (URL-based mode)
    if pname is None:
        repo_match = re.search(r'repo\s*=\s*"(.*?)"', fetcher_content)
        if not repo_match:
            coordinator_error("Could not extract repo name from fetcher")
            return
        pname = repo_match.group(1)
    
    initial_code = fill_src_attributes(starting_template, pname, version, fetcher_content)

    if get_settings_manager().get_setting_enabled("compare_template_builders"):
        initial_code = compare_template(initial_code, find_similar_builder_patterns, summary)
    coordinator_message(f"Initial package code:\n```nix\n{initial_code}\n```")

    # Step 7: Agentic loop
    coordinator_progress("Testing the initial build...")
    best = execute_build_and_add_to_stack(initial_code)

    ccl_logger.log_initial_build(view_package_contents(), best.result)
    
    from vibenix.packaging_flow.packaging_loop import packaging_loop 
    candidate, best, status = packaging_loop(
        best,
        summary,
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
            candidate = refine_package(candidate, summary, output_dir)
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
            save_package_output(candidate.code, output_dir)
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


def save_package_output(code: str, output_dir: str, auxiliary_files: bool=False):
    """Save the package.nix file to the output directory."""
    import os
    import re
    from pathlib import Path
    
    # Extract package name from the code
    pname_match = re.search(r'pname\s*=\s*"([^"]+)"', code)
    if not pname_match:
        coordinator_error("Could not extract package name from code")
        return
    
    package_name = pname_match.group(1)
    
    # Create output directory structure
    output_path = Path(output_dir) / package_name
    output_path.mkdir(parents=True, exist_ok=True)
    
    # Save package.nix
    package_file = output_path / "package.nix"
    package_file.write_text(code)

    # Save flake.nix and flake.lock
    for filename in ["flake.nix", "flake.lock"]:
        src_file = Path(config.flake_dir) / filename
        if src_file.exists():
            dest_file = output_path / filename
            dest_file.write_text(src_file.read_text())
    
    coordinator_message(f"Saved package to: {package_file}")
    from vibenix.ccl_log import get_logger
    get_logger().log_save_package(str(package_file))


def run_packaging_flow(output_dir=None, project_url=None, revision=None, fetcher=None,
                       csv_pname=None, csv_version=None, fetcher_content=None):
    """Run the complete packaging flow."""
    try:
        result = package_project(output_dir=output_dir, project_url=project_url,
                                revision=revision, fetcher=fetcher,
                                csv_pname=csv_pname, csv_version=csv_version,
                                fetcher_content=fetcher_content)
        if result:
            coordinator_message("Packaging completed successfully!")
            coordinator_message(f"Final package code:\n```nix\n{result}\n```")
        else:
            coordinator_message("Packaging failed. Please check the errors above.")
        print(f"Total API cost: ${get_model_prompt_manager().get_session_cost():.4f}")
    except Exception as e:
        coordinator_error(f"Unexpected error: {e}")
        from vibenix.ccl_log import get_logger
        get_logger().log_total_tool_cost()
        session_usage = get_model_prompt_manager().get_session_usage()
        get_logger().log_session_end(signal=None, total_cost=get_model_prompt_manager().get_session_cost(),
                total_input_tokens=session_usage.prompt_tokens,
                total_output_tokens=session_usage.completion_tokens,
                total_cache_read_tokens=session_usage.cache_read_tokens
        )
        close_logger()
        raise
