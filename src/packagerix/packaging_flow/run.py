"""Business logic for packagerix using the coordinator pattern."""

import subprocess
from pydantic import BaseModel

from packagerix.ui.conversation import ask_user,  coordinator_message, coordinator_error, coordinator_progress
from packagerix.parsing import scrape_and_process, extract_updated_code, fetch_combined_project_data, fill_src_attribute
from packagerix.flake import init_flake
from packagerix.nix import eval_progress, execute_build_and_add_to_stack
from packagerix.packaging_flow.model_prompts import pick_template, summarize_github, fix_build_error, fix_hash_mismatch
from packagerix.packaging_flow.user_prompts import get_project_url
from packagerix import config
from packagerix.errors import NixBuildErrorDiff, NixErrorKind, NixBuildResult
from packagerix.function_calls_source import create_source_function_calls


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

def package_project(output_dir=None, project_url=None):
    """Main coordinator function for packaging a project."""
    # Step 1: Get project URL (includes welcome message)
    if project_url is None:
        project_url = get_project_url()
    else:
        # When URL is provided via CLI, still show welcome but skip prompt
        coordinator_message("Welcome to packagerix!")
    
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
        from packagerix.parsing import fetch_github_release_data
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
    flake = init_flake()
    coordinator_message(f"Working on temporary flake at {config.flake_dir}")
    
    # Step 5: Load template
    template_type = pick_template(project_page)
    coordinator_message(f"Selected template: {template_type.value}")
    template_filename = f"{template_type.value}.nix"
    template_path = config.template_dir / template_filename
    starting_template = template_path.read_text()
    
    # Load optional notes file
    notes_filename = f"{template_type.value}.notes"
    notes_path = config.template_dir / notes_filename
    template_notes = notes_path.read_text() if notes_path.exists() else None

    # Step 6.a: Manual src setup
    coordinator_message("Setting up the src attribute in the template...")
    initial_code, store_path = fill_src_attribute(starting_template, project_url,
                                                  release_data.get('tag_name'))

    # Create functions for both the project source and nixpkgs
    project_functions = create_source_function_calls(store_path, "project_")
    nixpkgs_path = get_nixpkgs_source_path()
    nixpkgs_functions = create_source_function_calls(nixpkgs_path, "nixpkgs_")
    additional_functions = project_functions + nixpkgs_functions
    
    coordinator_progress("Testing the initial build...")
    initial_result = execute_build_and_add_to_stack(initial_code)
    best = Solution(code=initial_code, result=initial_result)
    
    # Check if initial build succeeded
    if best.result.success:
        coordinator_message("✅ Build succeeded on first try!")
        if output_dir:
            save_package_output(best.code, project_url, output_dir)
        return best.code

    build_iteration = 1
    eval_iteration = 1
    max_inner_attempts = 10
    candidate = best
    
    while True:
        coordinator_message(f"Build iteration {build_iteration} - attempting to fix error:")
        coordinator_message(f"```\n{candidate.result.error.error_message}\n```")
        
        # Call fix_build_error to get a generator of NixBuildResult objects
        build_results_generator = fix_build_error(candidate.code, candidate.result.error.error_message, 
                                                  project_page, release_data, template_notes, additional_functions)
        
        # Inner loop: Fix evaluation errors with limited attempts
        hash_fix_to_send = None
        
        while True:
            # Get next build result from generator (or send hash fix if we have one)
            if hash_fix_to_send:
                build_result = build_results_generator.send(hash_fix_to_send)
                hash_fix_to_send = None
            else:
                build_result = next(build_results_generator)
            
            eval_iteration += 1
            
            # Always update candidate with latest result
            candidate = Solution(code=build_result.code, result=build_result)
            
            if candidate.result.success:
                coordinator_message(f"✅ Build succeeded after {build_iteration} iterations!")
                if output_dir:
                    save_package_output(candidate.code, project_url, output_dir)
                return candidate.code
            coordinator_message(f"Nix build result: {candidate.result.error.type}")
            
            # Build still failed - check if we made progress or hit eval error
            if candidate.result.error.type == NixErrorKind.EVAL_ERROR:
                # Evaluation error - continue inner loop
                coordinator_message(f"{candidate.result.error.type} (attempt {eval_iteration}/{max_inner_attempts}), retrying...")
            elif candidate.result.error.type == NixErrorKind.HASH_MISMATCH:
                # Hash mismatch - fix and send back to generator
                coordinator_message("Hash mismatch detected, fixing...")
                coordinator_message(f"{candidate.result.error.type} (attempt {eval_iteration}/{max_inner_attempts}), retrying...")
                fixed_response = fix_hash_mismatch(candidate.code, candidate.result.error.error_message)
                hash_fix_to_send = extract_updated_code(fixed_response)
            elif candidate.result.error.type == NixErrorKind.BUILD_ERROR:
                break
            
            if eval_iteration > max_inner_attempts:
                coordinator_error(f"Failed to make progress within {max_inner_attempts} attempts.")
                return None
        
        # TODO: Check progress using NixBuildErrorDiff and decide whether to continue

        eval_result = eval_progress(best.result, candidate.result, build_iteration)
        if eval_result == NixBuildErrorDiff.PROGRESS:
            coordinator_message(f"Build iteration {build_iteration} made progress...")
            best = candidate
            build_iteration += 1
            eval_iteration = 1
        else:
            coordinator_message(f"Build iteration {build_iteration} did NOT made progress...")
            candidate = best

        if build_iteration > 15:
            coordinator_error("Reached temporary build iteration limit.")
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


def run_packaging_flow(output_dir=None, project_url=None):
    """Run the complete packaging flow."""
    try:
        result = package_project(output_dir=output_dir, project_url=project_url)
        if result:
            coordinator_message("Packaging completed successfully!")
            coordinator_message(f"Final package code:\n```nix\n{result}\n```")
        else:
            coordinator_message("Packaging failed. Please check the errors above.")
    except Exception as e:
        coordinator_error(f"Unexpected error: {e}")
        raise
