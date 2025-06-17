import subprocess

from packagerix import config
from packagerix.packaging_flow.model_prompts import evaluate_progress
from packagerix.errors import NixBuildResult, NixError, NixErrorKind, NixBuildErrorDiff

import git

from typing import Optional

from packagerix.flake import update_flake
from packagerix.ui.logging_config import logger


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
        ["nix", "build", f"{derivation_path}^*", "--no-link"],
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
        # This is unexpected - we should always be able to get logs after a build
        # Except if the build of a dependency failed
        raise RuntimeError(f"Failed to retrieve build logs for {derivation_path}: {log_result.stderr}")

    return NixBuildResult(
        success=False,
        is_src_attr_only=is_src_attr_only,
        error=NixError(type=NixErrorKind.BUILD_ERROR, error_message=log_result.stdout)
    )


def get_tail_of_log(s : str) -> str:
    lines = s.split('\n')
    return '\n'.join(lines[-50:])


def prepare_logs_for_comparison(initial_error: str, attempted_improvement: str, max_lines: int = 1000) -> dict:
    """Prepare logs for comparison by finding divergence point using sophisticated matching that handles reordered lines."""
    initial_lines_list = initial_error.splitlines()
    improvement_lines_list = attempted_improvement.splitlines()
    
    initial_lines = len(initial_lines_list)
    improvement_lines = len(improvement_lines_list)
    
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
    
    return {
        'initial_lines': initial_lines,
        'improvement_lines': improvement_lines,
        'divergence_line': divergence_line,
        'initial_error_truncated': initial_error_truncated,
        'attempted_improvement_truncated': attempted_improvement_truncated,
        '_initial_start_line': initial_start_line + 1,  # For logging only
        '_improvement_start_line': improvement_start_line + 1  # For logging only
    }


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
    error_message_trunc = f"\n```\n{get_tail_of_log(current_result.error.error_message)}\n```\n"
    prev_error_message_trunc = f"\n```\n{get_tail_of_log(previous_result.error.error_message)}\n```\n"
    logger.info(f"previous error: {prev_error_message_trunc}")

    logger.info(f"new error: {error_message_trunc}")

    repo = git.Repo(config.flake_dir.as_posix())
    logger.info(repo.commit().diff())

    if build_iteration == 1:
        return NixBuildErrorDiff.PROGRESS
    
    if not current_result.is_src_attr_only and previous_result.is_src_attr_only:
        return NixBuildErrorDiff.PROGRESS
    if current_result.is_src_attr_only:
        return NixBuildErrorDiff.REGRESS

    # Prepare the full logs for comparison
    log_comparison = prepare_logs_for_comparison(
        previous_result.error.error_message,
        current_result.error.error_message
    )
    
    # Log the comparison details
    logger.info(f"Log comparison details:")
    logger.info(f"  Initial build: {log_comparison['initial_lines']} total lines")
    logger.info(f"  Attempted improvement: {log_comparison['improvement_lines']} total lines")
    logger.info(f"  Logs diverge at line: {log_comparison['divergence_line']}")
    logger.info(f"  Sending to model - Initial: lines {log_comparison['_initial_start_line']}-{log_comparison['initial_lines']}")
    logger.info(f"  Sending to model - Improvement: lines {log_comparison['_improvement_start_line']}-{log_comparison['improvement_lines']}")
    
    # Remove logging-only keys before passing to model
    model_args = {k: v for k, v in log_comparison.items() if not k.startswith('_')}
    return evaluate_progress(**model_args)

def execute_build_and_add_to_stack(updated_code: str) -> NixBuildResult:
    """Update flake with new code, build it, and add result to error stack."""
    update_flake(updated_code)
    result = invoke_build(True)
    if result.success:
        result = invoke_build(False)
    config.error_stack.append(result)
    return result
