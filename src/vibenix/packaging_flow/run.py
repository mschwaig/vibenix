"""Business logic for vibenix using the coordinator pattern."""

import re
import subprocess
from pathlib import Path

from vibenix.ui.conversation import coordinator_message, coordinator_error, coordinator_progress
from vibenix.parsing import fetch_github_release_data, scrape_and_process, fetch_combined_project_data, fill_src_attributes
from vibenix.flake import init_flake, get_package_contents
from vibenix.nix import eval_progress, execute_build_and_add_to_stack, revert_packaging_to_solution
from vibenix.packaging_flow.model_prompts import (
    pick_template, summarize_github, fix_build_error, fix_hash_mismatch,
    analyze_package_failure, classify_packaging_failure, PackagingFailure,
    summarize_build, choose_builders, compare_template_builders, get_model_prompt_manager
)
from vibenix.packaging_flow.refinement import refine_package
from vibenix.packaging_flow.user_prompts import get_project_url
from vibenix.packaging_flow.Solution import Solution
from vibenix import config
from vibenix.errors import NixBuildErrorDiff, NixErrorKind
from vibenix.tools.file_tools import create_source_function_calls
from vibenix.ccl_log import init_logger, get_logger, close_logger, enum_str
from vibenix.git_info import get_git_info
from vibenix.tools.view import _view as view_package_contents
from vibenix.tools.error_pagination import error_pagination
from vibenix.defaults.vibenix_settings import get_settings_manager


def get_nixpkgs_source_path() -> str:
    """Get the nixpkgs source path from the template flake."""
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
        coordinator_error(f"Failed to get nixpkgs source path: {e}")
        raise



def analyze_project(project_page: str) -> str:
    """Analyze the project using the model."""
    # summarize_github already has the @ask_model decorator
    return summarize_github(project_page)

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

def run_nurl(url, rev=None):
    """Run nurl command and return the version and fetcher."""
    backoff_time = 5  # seconds
    try:
        from vibenix.ccl_log import get_logger
        ccl_logger = get_logger()
        ccl_logger.enter_attribute("pin_fetcher", log_start=True)
        
        # Get release data and version
        rev, version = get_release_data_and_version(url, rev)

        cmd = ['nurl', url, rev] if rev else ['nurl', url]
        ccl_logger.write_kv("nurl_args", " ".join(cmd[1:]))
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
            fetcher = fetcher.replace(version, "${version}") 
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
    """Read the fetcher file content."""
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

        # Instantiate fetcher to pull contents to nix store
        cmd = [
            'nix',
            'build',
            '--impure',
            '--expr',
            f"let pkgs = (builtins.getFlake (toString ./.)).inputs.nixpkgs.legacyPackages.${{builtins.currentSystem}}; in\nwith pkgs; {content}"
        ]
        try:
            result = subprocess.run(cmd, cwd=config.template_dir, capture_output=True, text=True, check=True)
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


def compare_template(available_builders, initial_code,
                     find_similar_builder_patterns, summary) -> str:
    """Prompt LLM to compare template with set of builders it thinks are relevant,
    present builder combinations to model, and let it make changes if needed."""
    from vibenix.tools.search_related_packages import _extract_builders

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


def package_project(output_dir=None, project_url=None, revision=None, fetcher=None):
    """Main coordinator function for packaging a project."""
    # Initialize CCL logger
    log_file = Path(output_dir) / "run.ccl" if output_dir else Path("run.ccl")
    ccl_logger = init_logger(log_file)
    
    # Log vibenix version info
    git_info = get_git_info()
    if git_info["commit_hash"]:
        ccl_logger.enter_attribute("vibenix_version")
        ccl_logger.write_kv("git_hash", git_info["commit_hash"])
        ccl_logger.write_kv("dirty", "true" if git_info["is_dirty"] else "false")
        ccl_logger.leave_attribute()
    
    # Step 1: Get project URL (includes welcome message)
    if project_url is None:
        project_url = get_project_url()
    else:
        # When URL is provided via CLI, still show welcome but skip prompt
        coordinator_message("Welcome to vibenix!")
    
    # Log model configuration
    from vibenix.model_config import get_model_config
    ccl_logger.log_model_config(get_model_config())
    # Log vibenix settings
    ccl_logger.log_vibenix_settings()

    ccl_logger.write_kv("project_url", project_url)

    # Obtain the project fetcher
    if fetcher: 
        coordinator_progress(f"Using provided fetcher: {fetcher}")
        version, fetcher = read_fetcher_file(fetcher)
        if revision:
            coordinator_error("Ignoring revision parameter in favor of provided fetcher.")
    else:
        coordinator_progress("Obtaining project fetcher from the provided URL")
        version, fetcher = run_nurl(project_url, revision)

    coordinator_progress(f"Fetching project information from {project_url}")
    
    # Step 2: Scrape project page
    try:
        project_page = scrape_and_process(project_url)
    except Exception as e:
        coordinator_error(f"Failed to fetch project page: {e}")
        return
    
    # Step 3: Analyze project
    coordinator_message("I found the project information. Let me analyze it.")
    ccl_logger.log_project_summary_begin()
    summary = analyze_project(project_page)
    ccl_logger.log_project_summary_end(summary)
    
    if not summary:
        coordinator_message("Failed to analyze project. Proceeding with empty project summary.")
        summary = ""

    # Step 4: Initialize flake
    coordinator_progress("Setting up a temporary Nix flake for packaging")
    init_flake()
    coordinator_message(f"Working on temporary flake at {config.flake_dir}")
    
    ccl_logger.log_template_selected_begin()
    template_type = pick_template(summary)
    if not template_type:
        raise RuntimeError("Model failed to pick a template type.")
    coordinator_message(f"Selected template: {template_type.value}")
    template_filename = f"{template_type.value}.nix"
    template_path = config.template_dir / template_filename
    starting_template = template_path.read_text()
    
    # Load optional notes file
    notes_filename = f"{template_type.value}.notes"
    notes_path = config.template_dir / notes_filename
    template_notes = notes_path.read_text() if notes_path.exists() else None
    ccl_logger.log_template_selected_end(template_type, starting_template, template_notes)

    # Step 6.a: Manual src setup
    coordinator_message("Setting up the src attribute in the template...")
    # Extract pname (repo name) from the fetcher
    repo_match = re.search(r'repo\s*=\s*"(.*?)"', fetcher)
    if not repo_match:
        raise ValueError("Could not extract repo name from fetcher")
    pname = repo_match.group(1)
    
    initial_code, store_path = fill_src_attributes(starting_template, pname, version, fetcher)
    nixpkgs_path = get_nixpkgs_source_path()
    # Create functions for both the project source and nixpkgs
    project_functions = create_source_function_calls(store_path, "project_")
    nixpkgs_functions = create_source_function_calls(nixpkgs_path, "nixpkgs_")

    # Compare chosen template with builders from model response
    from vibenix.tools.search_related_packages import get_builder_functions, _create_find_similar_builder_patterns
    available_builders = get_builder_functions()
    find_similar_builder_patterns = _create_find_similar_builder_patterns(available_builders)
    additional_functions = project_functions + nixpkgs_functions + [get_builder_functions, find_similar_builder_patterns]
    get_settings_manager().initialize_additional_tools(additional_functions)

    if get_settings_manager().get_setting_enabled("build_summary"):
        build_summary = summarize_build(summary)
        if not build_summary:
            coordinator_error("Model failed to produce a build summary.")
            raise RuntimeError("Model failed to produce a build summary.")
        summary += f"\n\nBuild Summary:\n{build_summary}"

    if get_settings_manager().get_setting_enabled("compare_template_builders"):
        initial_code = compare_template(available_builders, initial_code, find_similar_builder_patterns, summary)
    coordinator_message(f"Initial package code:\n```nix\n{initial_code}\n```")

    # Step 7: Agentic loop
    coordinator_progress("Testing the initial build...")
    best = execute_build_and_add_to_stack(initial_code)

    ccl_logger.log_initial_build(view_package_contents(), best.result)
    ccl_logger.enter_attribute("iterate")
    
    iteration = 0
    MAX_ITERATIONS = 40
    candidate = best
    MAX_CONSECUTIVE_REBUILDS_WITHOUT_PROGRESS = 10
    consecutive_rebuilds_without_progress = 0
    # we have to evaluate if a small limit here helps or hurts
    # or just get rid of it
    MAX_CONSECUTIVE_NON_BUILD_ERRORS = 99
    consecutive_non_build_errors = 0

    first_build_error = True
    has_broken_log_output = False
    
    # Track attempted tool calls to avoid repetition
    attempted_tool_calls = []
    
    while (
        (not candidate.result.success) and
        (iteration < MAX_ITERATIONS) and
        (consecutive_rebuilds_without_progress < MAX_CONSECUTIVE_REBUILDS_WITHOUT_PROGRESS)):
      
        coordinator_message(f"Iteration {iteration + 1}:")
        coordinator_message(f"```\n{candidate.result.error.truncated()}\n```")
        ccl_logger.log_iteration_start(iteration, candidate.result.error.type if candidate.result.error else None)
        
        if candidate.result.error.type == NixErrorKind.HASH_MISMATCH:
            coordinator_message("Hash mismatch detected, fixing...")
            coordinator_message(f"code:\n{candidate.code}\n")
            coordinator_message(f"error:\n{candidate.result.error.truncated()}\n")
            fix_hash_mismatch(view_package_contents(prompt="fix_hash_mismatch"), candidate.result.error.truncated())
        elif candidate.result.error.type == NixErrorKind.INVALID_HASH:
            coordinator_message("Invalid SRI hash detected, fixing...")
            hash_match = re.search(r'hash \'([a-zA-Z0-9+/=]+)\'', candidate.result.error.truncated())
            if hash_match:
                invalid_hash = hash_match.group(1)
                coordinator_message(f"Invalid hash from error: {invalid_hash}")
                escaped_hash = re.escape(invalid_hash)
                match = re.search(rf'"[^"]*?{escaped_hash}[^"]*?"', candidate.code)
                if match:
                    coordinator_message(f"Found invalid hash in code: {match.group(0)}")
                    fixed_code = re.sub(rf'"[^"]*?{escaped_hash}[^"]*?"', 'lib.fakeHash', candidate.code)
                    from vibenix.flake import update_flake
                    update_flake(fixed_code)
                else: # fallback if regex fails
                    fix_hash_mismatch(view_package_contents(prompt="fix_hash_mismatch"), candidate.result.error.truncated())
            else: # fallback
                fix_hash_mismatch(view_package_contents(prompt="fix_hash_mismatch"), candidate.result.error.truncated())
        else:
            coordinator_message("Other error detected, fixing...")
            coordinator_message(f"code:\n{candidate.code}\n")
            coordinator_message(f"error:\n{candidate.result.error.truncated()}\n")
            
            # Create a collector for this iteration's tool calls
            iteration_tool_calls = []
            error_truncated = candidate.result.error.truncated()
            is_dependency_error = candidate.result.error.type == NixErrorKind.DEPENDENCY_BUILD_ERROR
            is_syntax_error = candidate.result.error.type == NixErrorKind.EVAL_ERROR and "error: syntax error" in error_truncated
            if is_syntax_error:
                syntax_error_index = error_truncated.index("error: syntax error")
                error_truncated = error_truncated[syntax_error_index:]

            fix_build_error(
                view_package_contents(prompt="fix_build_error"),
                error_truncated, 
                summary, 
                template_notes, 
                has_broken_log_output,
                is_dependency_error,
                is_syntax_error,
                attempted_tool_calls,
                iteration_tool_calls
            )
            
            # Add this iteration's tool calls to the attempted list
            attempted_tool_calls.extend(iteration_tool_calls)
        
        updated_code = get_package_contents()
        # Log the updated code
        ccl_logger.write_kv("updated_code", updated_code)

        if updated_code == candidate.code:
            coordinator_message("No changes made by the model, skipping iteration.")
            usage = get_model_prompt_manager().get_iteration_usage()
            ccl_logger.log_iteration_cost(
                iteration=iteration,
                iteration_cost=usage.calculate_cost(),
                input_tokens=usage.prompt_tokens,
                output_tokens=usage.completion_tokens,
                cache_read_tokens=usage.cache_read_tokens
            )
            iteration += 1
            continue
            
        # Test the fix
        coordinator_progress(f"Iteration {iteration + 1}: Testing fix attempt {iteration + 1} of {MAX_ITERATIONS}...")
        candidate = execute_build_and_add_to_stack(updated_code)
        new_result = candidate.result
        
        # Log the build result
        ccl_logger.enter_attribute("build", log_start=True)
        if new_result.success:
            ccl_logger.write_kv("error", None)
            ccl_logger.write_kv("log", None)
        else:
            ccl_logger.write_kv("error", enum_str(new_result.error.type))
            ccl_logger.write_kv("log", new_result.error.error_message)
        ccl_logger.leave_attribute(log_end=True)

        if not new_result.success:
            if new_result.error.type == NixErrorKind.BUILD_ERROR:
                coordinator_message(f"Nix build result: {candidate.result.error.type}")
                if first_build_error:
                    eval_result = NixBuildErrorDiff.PROGRESS
                    first_build_error = False
                    ccl_logger.write_kv("is_first_build_error", None)
                elif best.result.error == candidate.result.error:
                    eval_result = NixBuildErrorDiff.REGRESS
                else:
                    if get_settings_manager().get_setting_enabled("progress_evaluation"):
                        ccl_logger.log_progress_eval_start()
                        eval_result = eval_progress(best.result, candidate.result, iteration)
                        ccl_logger.log_progress_eval_end(eval_result)
                    else:
                        eval_result = NixBuildErrorDiff.PROGRESS
                if eval_result == NixBuildErrorDiff.PROGRESS:
                    coordinator_message(f"Iteration {iteration + 1} made progress...")
                    best = candidate
                    consecutive_rebuilds_without_progress = 0
                    has_broken_log_output = False  # Reset since we made progress
                    attempted_tool_calls = []  # Reset tool calls since we made progress
                elif eval_result == NixBuildErrorDiff.BROKEN_LOG_OUTPUT:
                    coordinator_message(f"Iteration {iteration + 1} produced broken log output - continuing without rollback...")
                    has_broken_log_output = True
                    # Don't update best, but also don't rollback candidate
                    # Don't increment consecutive_rebuilds_without_progress since this is a special case
                elif eval_result == NixBuildErrorDiff.STAGNATION:
                    if has_broken_log_output:
                        # Stagnation after broken log output means we fixed the log output!
                        coordinator_message(f"Iteration {iteration + 1} fixed broken log output (now showing clear error)...")
                        best = candidate
                        has_broken_log_output = False
                        consecutive_rebuilds_without_progress = 0
                        attempted_tool_calls = []  # Reset tool calls since we fixed log output
                    else:
                        coordinator_message(f"Iteration {iteration + 1} stagnated...")
                        candidate = best
                        revert_packaging_to_solution(best)
                        consecutive_rebuilds_without_progress += 1
                else:  # REGRESS
                    coordinator_message(f"Iteration {iteration + 1} regressed...")
                    candidate = best
                    revert_packaging_to_solution(best)
                    consecutive_rebuilds_without_progress += 1
                consecutive_non_build_errors = 0
            else:
                # Non-build errors (EVAL_ERROR, INVALID_HASH, HASH_MISMATCH, DEPENDENCY_BUILD_ERROR)
                coordinator_message(f"Non-build error: {new_result.error.type}")
                consecutive_non_build_errors += 1          
                if consecutive_non_build_errors >= MAX_CONSECUTIVE_NON_BUILD_ERRORS:
                    candidate = best
                    revert_packaging_to_solution(best)
                    consecutive_non_build_errors = 0
        
        usage = get_model_prompt_manager().get_iteration_usage()
        ccl_logger.log_iteration_cost(
            iteration=iteration,
            iteration_cost=usage.calculate_cost(),
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens,
            cache_read_tokens=usage.cache_read_tokens
        )
        iteration += 1

    # Close the iteration list and iterate attribute
    if iteration > 0:
        ccl_logger.leave_list()
    ccl_logger.leave_attribute()
    
    # Log the raw package code before refinement or analysis
    ccl_logger.write_kv("raw_package", candidate.code)
    
    if candidate.result.success:
        if get_settings_manager().get_setting_enabled("refinement"):
            packaging_usage = get_model_prompt_manager().get_session_usage()
            coordinator_message("Build succeeded! Refining package...")
            candidate = refine_package(candidate, summary)
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
        ccl_logger.log_session_end(signal=None, total_cost=get_model_prompt_manager().get_session_cost())
        close_logger()
        if output_dir:
            save_package_output(candidate.code, project_url, output_dir)
        return candidate.code  
    else:
        if consecutive_non_build_errors >= MAX_CONSECUTIVE_NON_BUILD_ERRORS:
            coordinator_error(f"Aborted: {consecutive_rebuilds_without_progress} consecutive rebuilds without progress.")

        else:
            coordinator_error(f"Reached MAX_ITERATIONS build iteration limit of {MAX_ITERATIONS}.")
    
    ccl_logger.enter_attribute("analyze_failure")
    revert_packaging_to_solution(best) # TODO do this differently later, not just best
    details = analyze_package_failure(best.code, best.result.error.truncated(), summary, template_notes) # TODO best.code
    ccl_logger.write_kv("description", str(details))
    packaging_failure = classify_packaging_failure(details)
    ccl_logger.write_kv("failure_type", enum_str(packaging_failure))
    ccl_logger.leave_attribute()
    if isinstance(packaging_failure, PackagingFailure):
        coordinator_message(f"Packaging failure type: {packaging_failure}\nDetails:\n{details}\n")

    ccl_logger.log_total_tool_cost()
    ccl_logger.log_session_end(signal=None, total_cost=get_model_prompt_manager().get_session_cost())
    close_logger()
    return None


def save_package_output(code: str, project_url: str, output_dir: str):
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
    
    coordinator_message(f"Saved package to: {package_file}")


def run_packaging_flow(output_dir=None, project_url=None, revision=None, fetcher=None):
    """Run the complete packaging flow."""
    try:
        result = package_project(output_dir=output_dir, project_url=project_url,
                                revision=revision, fetcher=fetcher)
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
        get_logger().log_session_end(signal=None, total_cost=get_model_prompt_manager().get_session_cost())
        close_logger()
        raise
