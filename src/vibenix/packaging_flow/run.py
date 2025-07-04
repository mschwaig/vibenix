"""Business logic for vibenix using the coordinator pattern."""

import subprocess
from pathlib import Path
from pydantic import BaseModel

from vibenix.ui.conversation import ask_user,  coordinator_message, coordinator_error, coordinator_progress
from vibenix.parsing import scrape_and_process, extract_updated_code, fetch_combined_project_data, fill_src_attributes
from vibenix.flake import init_flake
from vibenix.nix import eval_progress, execute_build_and_add_to_stack
from vibenix.packaging_flow.model_prompts import pick_template, set_up_project, summarize_github, fix_build_error, fix_hash_mismatch, evaluate_code, refine_code, get_feedback, RefinementExit
from vibenix.packaging_flow.user_prompts import get_project_url
from vibenix import config
from vibenix.errors import NixBuildErrorDiff, NixErrorKind, NixBuildResult
from vibenix.function_calls_source import create_source_function_calls
from vibenix.ccl_log import init_logger, get_logger, close_logger

class Solution(BaseModel):
    """Represents a solution candidate with its code and build result."""
    code: str
    result: NixBuildResult


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



def analyze_project(project_page: str, release_data: dict = None) -> str:
    """Analyze the project using the model."""
    # summarize_github already has the @ask_model decorator
    return summarize_github(project_page, release_data)


def create_initial_package(template: str, project_page: str, release_data: dict = None, template_notes: str = None) -> str:
    """Create initial package configuration."""
    # set_up_project now has the @ask_model decorator that handles UI and streaming
    result = set_up_project(template, project_page, release_data, template_notes)
    return extract_updated_code(result)


def run_nurl(url, rev=None):
    """Run nurl command and return the output."""
    try:
        cmd = ['nurl', url, rev] if rev else ['nurl', url]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        error_output = e.stderr.lower()
        # Check for various rate limit indicators
        if any(indicator in error_output for indicator in ["rate limit", "429", "403", "forbidden"]):
            print(f"Rate limit/forbidden error for {url}")
            return "RATE_LIMITED"
        print(f"Error running nurl for {url}: {e.stderr}")
        return None
    except FileNotFoundError:
        print("Error: nurl command not found. Please ensure nurl is installed.")
        return None


def read_fetcher_file(fetcher: str) -> str:
    """Read the fetcher file content."""
    from pathlib import Path
    try:
        path = Path(fetcher)
        with open(path, 'r') as f:
            # Ignore comments and empty lines
            return "".join(line for line in f if line.strip() and not line.startswith("#"))
    except FileNotFoundError:
        coordinator_error(f"Fetcher file '{fetcher}' not found.")
        raise
    except Exception as e:
        coordinator_error(f"Error reading fetcher file: {e}")
        raise


def refine_package(curr: Solution, project_page: str):
    """Refinement cycle to improve the packaging."""
    prev = curr
    max_iterations = 3

    for iteration in range(max_iterations):
        # Get feedback
        # TODO BUILD LOG IS NOT BEING PASSED!
        feedback = get_feedback(curr.code, "", project_page)
        coordinator_message(f"Refining package (iteration {iteration}/{max_iterations})...")
        coordinator_message(f"Received feedback: {feedback}")

        # Pass the feedback to the generator (refine_code)
        response = refine_code(curr.code, feedback, project_page)
        updated_code = extract_updated_code(response)
        updated_res = execute_build_and_add_to_stack(updated_code)
        attempt = Solution(code=updated_code, result=updated_res)
        
        # Verify the updated code still builds
        if not attempt.result.success:
            coordinator_message(f"Refinement caused a regression: {attempt.result.error.type}")
            return attempt, RefinementExit.ERROR
        else:
            coordinator_message("Refined packaging code successfuly builds...")
            prev = curr
            curr = attempt

        # Verify if the state of the refinement process
        evaluation = evaluate_code(curr.code, prev.code, feedback)
        if evaluation == RefinementExit.COMPLETE:
            coordinator_message("Evaluator deems the improvements complete.")
            return curr, RefinementExit.COMPLETE
        elif evaluation == RefinementExit.INCOMPLETE:
            coordinator_message("Evaluator suggests further improvements are needed.")
        else:
            coordinator_message("Evaluator deems there has been a regression in the packaging code. Reverting to previous state.")
            curr = prev
    return curr, RefinementExit.INCOMPLETE


def package_project(output_dir=None, project_url=None, revision=None, fetcher=None):
    """Main coordinator function for packaging a project."""
    # Initialize CCL logger
    log_file = Path(output_dir) / "run.ccl" if output_dir else Path("run.ccl")
    ccl_logger = init_logger(log_file)
    
    # Step 1: Get project URL (includes welcome message)
    if project_url is None:
        project_url = get_project_url()
    else:
        # When URL is provided via CLI, still show welcome but skip prompt
        coordinator_message("Welcome to vibenix!")
    
    # Log session start
    ccl_logger.log_session_start(project_url)

    # Obtain the project fetcher
    if fetcher: 
        coordinator_progress(f"Using provided fetcher: {fetcher}")
        fetcher = read_fetcher_file(fetcher)
        if revision:
            coordinator_error("Ignoring revision parameter in favor of provided fetcher.")
    else:
        coordinator_progress("Obtaining project fetcher from the provided URL")
        fetcher = run_nurl(project_url, revision)

    coordinator_progress(f"Fetching project information from {project_url}")
    
    # Step 2: Scrape project page
    try:
        project_page = scrape_and_process(project_url)
    except Exception as e:
        coordinator_error(f"Failed to fetch project page: {e}")
        return
    
    # Step 2b: Fetch release data from GitHub API
    release_data = None
    try:
        from vibenix.parsing import fetch_github_release_data
        release_data = fetch_github_release_data(project_url)
        if release_data:
            coordinator_message("Found GitHub release information via API")
    except Exception as e:
        coordinator_message(f"Could not fetch release data: {e}")
    
    # Step 3: Analyze project
    coordinator_message("I found the project information. Let me analyze it.")
    summary = analyze_project(project_page, release_data)

    # Step 4: Initialize flake
    coordinator_progress("Setting up a temporary Nix flake for packaging")
    init_flake()
    coordinator_message(f"Working on temporary flake at {config.flake_dir}")
    
    # Step 5: Load template
    template_type = pick_template(summary)
    coordinator_message(f"Selected template: {template_type.value}")
    ccl_logger.log_template_selected(template_type.value)
    template_filename = f"{template_type.value}.nix"
    template_path = config.template_dir / template_filename
    starting_template = template_path.read_text()
    
    # Load optional notes file
    notes_filename = f"{template_type.value}.notes"
    notes_path = config.template_dir / notes_filename
    template_notes = notes_path.read_text() if notes_path.exists() else None

    # Step 6.a: Manual src setup
    coordinator_message("Setting up the src attribute in the template...")
    initial_code, store_path = fill_src_attributes(starting_template, fetcher)

    # Create functions for both the project source and nixpkgs
    project_functions = create_source_function_calls(store_path, "project_")
    nixpkgs_path = get_nixpkgs_source_path()
    nixpkgs_functions = create_source_function_calls(nixpkgs_path, "nixpkgs_")
    additional_functions = project_functions + nixpkgs_functions
    
    # Step 7: Agentic loop
    coordinator_progress("Testing the initial build...")
    initial_result = execute_build_and_add_to_stack(initial_code)
    best = Solution(code=initial_code, result=initial_result)
    last_successful = None

    # Log that we're starting iterations
    ccl_logger.log_before_iterations()
    
    iteration = 1
    MAX_ITERATIONS = 40
    candidate = best
    
    while iteration <= MAX_ITERATIONS:
        coordinator_message(f"Iteration {iteration}:")
        coordinator_message(f"```\n{candidate.result.error.error_message}\n```")
        ccl_logger.log_iteration_start(iteration)
        
        # Fix the error based on type
        if candidate.result.success:
            last_successful = candidate
            coordinator_message("Build succeeded! Refining package...")
            refined_candidate, completed = refine_package(candidate, summary)
            
            if completed == RefinementExit.ERROR:
                coordinator_error("Refinement encountered an error. Returning pre-refinement solution.")
            elif completed == RefinementExit.INCOMPLETE:
                coordinator_message("Refinement process reached max iterations.")
            
            # Always log success and return, regardless of refinement outcome
            from vibenix.packaging_flow.model_prompts import end_stream_logger
            ccl_logger.log_session_end(True, iteration, end_stream_logger.total_cost)
            close_logger()
            if output_dir:
                # Use refined version if no error, otherwise use pre-refinement version
                final_code = refined_candidate.code if completed != RefinementExit.ERROR else candidate.code
                save_package_output(final_code, project_url, output_dir)
            return refined_candidate.code if completed != RefinementExit.ERROR else candidate.code
        elif candidate.result.error.type == NixErrorKind.HASH_MISMATCH:
            coordinator_message("Hash mismatch detected, fixing...")
            coordinator_message(f"code:\n{candidate.code}\n")
            coordinator_message(f"error:\n{candidate.result.error.error_message}\n")
            fixed_response = fix_hash_mismatch(candidate.code, candidate.result.error.error_message)
        else:
            coordinator_message("Other error detected, fixing...")
            coordinator_message(f"code:\n{candidate.code}\n")
            coordinator_message(f"error:\n{candidate.result.error.error_message}\n")
            fixed_response = fix_build_error(candidate.code, candidate.result.error.error_message, summary, release_data, template_notes, additional_functions)
        
        updated_code = extract_updated_code(fixed_response)
            
        # Test the fix
        coordinator_progress(f"Iteration {iteration}: Testing fix attempt {iteration} of {MAX_ITERATIONS}...")
        new_result = execute_build_and_add_to_stack(updated_code)
        candidate = Solution(code=updated_code, result=new_result)
        
        coordinator_message(f"Nix build result: {candidate.result.error.type}")
        eval_result = eval_progress(best.result, candidate.result, iteration)
        ccl_logger.log_progress_eval(iteration, eval_result)
        
        if eval_result == NixBuildErrorDiff.PROGRESS:
            coordinator_message(f"Iteration {iteration} made progress...")
            best = candidate
        else:
            coordinator_message(f"Iteration {iteration} did NOT made progress...")
            candidate = best
            
        ccl_logger.log_iteration_end(iteration, new_result)
        iteration += 1

    coordinator_error("Reached temporary build iteration limit.")
    from vibenix.packaging_flow.model_prompts import end_stream_logger
    if last_successful:
        coordinator_message("Returning last successful build.")
        ccl_logger.log_session_end(False, iteration, end_stream_logger.total_cost)
        close_logger()
        return last_successful.code
    ccl_logger.log_session_end(False, iteration, end_stream_logger.total_cost)
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
            # Print total API cost
            from vibenix.packaging_flow.model_prompts import end_stream_logger
            if end_stream_logger.total_cost > 0:
                coordinator_message(f"\n💰 Total API cost: ${end_stream_logger.total_cost:.6f}")
        else:
            coordinator_message("Packaging failed. Please check the errors above.")
            # Print total API cost even on failure
            from vibenix.packaging_flow.model_prompts import end_stream_logger
            if end_stream_logger.total_cost > 0:
                coordinator_message(f"\n💰 Total API cost: ${end_stream_logger.total_cost:.6f}")
    except Exception as e:
        coordinator_error(f"Unexpected error: {e}")
        raise
