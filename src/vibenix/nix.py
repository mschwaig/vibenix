import subprocess

from vibenix import config
from vibenix.packaging_flow.model_prompts import evaluate_progress
from vibenix.packaging_flow.Solution import Solution
from vibenix.errors import NixBuildResult, NixError, NixErrorKind, NixBuildErrorDiff, FullLogDiff, ProcessedLogDiff, LogDiff

import git

from typing import Optional

from vibenix.flake import update_flake
from vibenix.ui.logging_config import logger


def invoke_build(is_src_attr_only: bool) -> NixBuildResult:
    if is_src_attr_only:
        target_attr = f"{config.flake_dir}#default.src"
    else:
        target_attr = f"{config.flake_dir}#default"
    # First, evaluate the flake to get the derivation path
    # If this fails, it's an evaluation error
    eval_result = subprocess.run(
        ["nix", "path-info", "--derivation", target_attr],
        text=True,
        capture_output=True
    )
    
    if eval_result.returncode != 0:
        import re
        if "invalid SRI hash" in eval_result.stderr or re.search(r"hash '.*' does not include", eval_result.stderr):
            return NixBuildResult(
                success=False,
                is_src_attr_only=is_src_attr_only,
                error=NixError(type=NixErrorKind.INVALID_HASH, error_message=eval_result.stderr)
            )
        if "hash mismatch in fixed-output derivation" in eval_result.stderr:
            return NixBuildResult(
                success=False,
                is_src_attr_only=is_src_attr_only,
                error=NixError(type=NixErrorKind.HASH_MISMATCH, error_message=eval_result.stderr)
            )
        return NixBuildResult(
            success=False,
            is_src_attr_only=is_src_attr_only,
            error=NixError(type=NixErrorKind.EVAL_ERROR, error_message=eval_result.stderr)
        )
    
    derivation_path = eval_result.stdout.strip()
    logger.info(f"Building derivation outputs: {derivation_path}^*")

    # Build the derivation outputs (not just the derivation file)
    build_result = subprocess.run(
        ["nix", "build", "--timeout", config.build_timeout, f"{derivation_path}^*", "--no-link"],
        text=True,
        capture_output=True
    )

    # If build succeeded, return success
    if build_result.returncode == 0:
        return NixBuildResult(success=True, is_src_attr_only=is_src_attr_only)

    # Build failed, check if it's a hash mismatch before getting logs
    if "hash mismatch in fixed-output derivation" in build_result.stderr:
        return NixBuildResult(
            success=False,
            is_src_attr_only=is_src_attr_only,
            error=NixError(type=NixErrorKind.HASH_MISMATCH, error_message=build_result.stderr)
        )

    # Not a hash mismatch, get logs for build error
    log_result = subprocess.run(
        ["nix", "log", f"{derivation_path}^*"],
        text=True,
        capture_output=True
    )
    
    if log_result.returncode != 0:
        # We should always be able to get logs after a build
        # Except if the build of a dependency failed
        # Return the build output which shows the dependency context more clearly
        return NixBuildResult(
            success=False,
            is_src_attr_only=is_src_attr_only,
            error=NixError(type=NixErrorKind.DEPENDENCY_BUILD_ERROR, error_message=build_result.stderr)
        )

    return NixBuildResult(
        success=False,
        is_src_attr_only=is_src_attr_only,
        error=NixError(type=NixErrorKind.BUILD_ERROR, error_message=log_result.stdout)
    )


def prepare_logs_for_comparison(initial_error: str, attempted_improvement: str, max_lines: int = 260) -> LogDiff:
    """Prepare logs for comparison by finding divergence point using sophisticated matching that handles reordered lines."""
    initial_lines_list = initial_error.splitlines()
    improvement_lines_list = attempted_improvement.splitlines()
    
    initial_lines = len(initial_lines_list)
    improvement_lines = len(improvement_lines_list)
    
    # If both logs are under 100 lines, show them in full
    if initial_lines < 100 and improvement_lines < 100:
        # Add line numbers to the full logs
        initial_numbered_lines = []
        for i, line in enumerate(initial_lines_list, start=1):
            initial_numbered_lines.append(f"{i:4d}: {line}")
        
        improvement_numbered_lines = []
        for i, line in enumerate(improvement_lines_list, start=1):
            improvement_numbered_lines.append(f"{i:4d}: {line}")
        
        initial_error_full = '\n'.join(initial_numbered_lines)
        attempted_improvement_full = '\n'.join(improvement_numbered_lines)
        
        return FullLogDiff(
            previous_log=initial_error_full,
            new_log=attempted_improvement_full,
            initial_lines=initial_lines,
            improvement_lines=improvement_lines
        )
    
    # Otherwise, use the existing truncation logic
    # Create one set for O(1) lookup
    improvement_set = set(improvement_lines_list)
    
    # Find the first line from initial log that doesn't exist anywhere in improvement log
    divergence_line = 1
    for i in range(min(len(initial_lines_list), len(improvement_lines_list))):
        initial_line = initial_lines_list[i]
        
        # If this line doesn't exist in the other log, this is where they diverge
        if initial_line not in improvement_set:
            divergence_line = i + 1
            break
    else:
        # If we didn't find such a pair, diverge at the length difference
        divergence_line = min(initial_lines, improvement_lines) + 1
    
    # Calculate how many lines to take from the end, considering divergence point
    # We want at most max_lines, but if divergence is late, we take from divergence point
    initial_start_line = max(0, initial_lines - max_lines, divergence_line - 1)
    improvement_start_line = max(0, improvement_lines - max_lines, divergence_line - 1)
    
    # Add line numbers to the truncated logs
    initial_truncated_lines = []
    for i, line in enumerate(initial_lines_list[initial_start_line:], start=initial_start_line + 1):
        initial_truncated_lines.append(f"{i:4d}: {line}")
    
    improvement_truncated_lines = []
    for i, line in enumerate(improvement_lines_list[improvement_start_line:], start=improvement_start_line + 1):
        improvement_truncated_lines.append(f"{i:4d}: {line}")
    
    initial_error_truncated = '\n'.join(initial_truncated_lines)
    attempted_improvement_truncated = '\n'.join(improvement_truncated_lines)
    
    return ProcessedLogDiff(
        previous_log_truncated=initial_error_truncated,
        new_log_truncated=attempted_improvement_truncated,
        initial_lines=initial_lines,
        improvement_lines=improvement_lines,
        divergence_line=divergence_line
    )


# read build log of previous step and this step
# to evalute if the model made progress towards building the project
# this is done by counting magical phrases in the build output like
# "comiling ..." 
def eval_initial_build() -> NixError:
    """Evaluate the initial build - look for hash mismatch which indicates progress."""
    build_result = config.error_stack[-1]
    error_message = build_result.error.error_message
    
    # Check if this is a hash mismatch error (indicates template was filled correctly)
    if "hash mismatch" in error_message.lower() or "expected sha256" in error_message.lower():
        logger.info("✅ Initial build shows hash mismatch - template was filled correctly!")
        # Return NixError object for LLM to fix the hash
        return NixError(type=NixErrorKind.HASH_MISMATCH, error_message=error_message)
    else:
        logger.info("❌ Initial build failed with non-hash error")
        return NixError(type=NixErrorKind.EVAL_ERROR, error_message=error_message)

# a significantly higher number of magical phrases indicates progress
# an about equal amount goes to an llm to break the tie using same_build_error with the two tails of the two build logs
def eval_progress(previous_result: NixBuildResult, current_result: NixBuildResult, build_iteration: int) -> NixBuildErrorDiff:    
    if build_iteration == 1 or current_result.success:
        return NixBuildErrorDiff.PROGRESS
    
    # Log truncated versions for debugging
    logger.info(f"previous error (last 50 lines): \n```\n{previous_result.error.truncated(50)}\n```\n")
    logger.info(f"new error (last 50 lines): \n```\n{current_result.error.truncated(50)}\n```\n")

    repo = git.Repo(config.flake_dir.as_posix())
    logger.info(repo.commit().diff())

    if not current_result.is_src_attr_only and previous_result.is_src_attr_only:
        return NixBuildErrorDiff.PROGRESS
    if current_result.is_src_attr_only:
        return NixBuildErrorDiff.REGRESS

    # Prepare the logs for comparison with limited lines to avoid token limits
    log_comparison = prepare_logs_for_comparison(
        previous_result.error.error_message,
        current_result.error.error_message,
        max_lines=240 # 260 exceeded token limit on gianni-rosato/aviator
    )
    
    # Log the comparison details
    logger.info(f"Log comparison details:")
    logger.info(f"  Initial build: {log_comparison.initial_lines} total lines")
    logger.info(f"  Attempted improvement: {log_comparison.improvement_lines} total lines")
    if isinstance(log_comparison, FullLogDiff):
        logger.info(f"  Showing full logs (both under 100 lines)")
    else:
        logger.info(f"  Logs diverge at line: {log_comparison.divergence_line}")
        logger.info(f"  Sending to model - truncated logs showing divergence")
    
    return evaluate_progress(log_comparison)

def execute_build_and_add_to_stack(updated_code: str) -> Solution:
    """Update flake with new code, build it, and add result to error stack."""
    commit_hash = update_flake(updated_code, do_commit=True)
    result = invoke_build(True)
    if result.success:
        result = invoke_build(False)
    # Check if full log can be fetched with a nix command
    import re
    match = re.search(r"For full logs, run '(nix log .+?)'", result.error.error_message) if result.error else None
    if match:
        full_log_cmd = match.group(1)
        log_result = subprocess.run(
            full_log_cmd.split(),
            text=True,
            capture_output=True
        )
        if log_result.returncode == 0:
            result.error.error_message = log_result.stdout
    config.error_stack.append(result)
    return Solution(code=updated_code, commit_hash=commit_hash,
        result=result, error_index=len(config.error_stack)-1)

def revert_packaging_to_solution(solution: Solution) -> None:
    """Revert the flake to a known good solution."""
    repo = git.Repo(config.flake_dir.as_posix())
    repo.git.reset('--hard', solution.commit_hash)
    config.error_stack = config.error_stack[:solution.error_index + 1]
    logger.info(f"Reverted to commit {solution.commit_hash}.")

def check_syntax(code: str) -> Optional[str]:
    """Try to parse the Nix code to check for syntax errors."""
    parse_result = subprocess.run(
        ["nix-instantiate", "--parse-only", "-"],
        input=code,
        text=True,
        capture_output=True
    )
    
    if parse_result.returncode != 0:
        return parse_result.stderr.strip()
    
    return None

def run_formatter():
    """Run nixpkgs-fmt on the current package.nix to ensure consistent formatting."""
    from vibenix.flake import get_package_path
    file_path = get_package_path()
    print(f"Running nixfmt on {file_path}")
    with open(file_path, 'r') as f:
        format_result = subprocess.run(
            ["nixfmt"],
            text=True,
            capture_output=True,
            input=f.read()
        )
    if format_result.returncode != 0:
        print("nixpkgs-fmt failed:", format_result.stderr.strip())
        logger.warning(f"nixpkgs-fmt failed: {format_result.stderr.strip()}")
        # if formatter fails, we don't block the flow
        return
    else:
        updated_code = format_result.stdout
        update_flake(updated_code)
