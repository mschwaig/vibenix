from vibenix.packaging_flow.Solution import Solution
from vibenix.ui.conversation import coordinator_message, coordinator_error, coordinator_progress
from vibenix.errors import NixErrorKind, NixBuildErrorDiff
from vibenix.tools.view import _view as view_package_contents
from vibenix.packaging_flow.model_prompts import (
    fix_build_error,
    fix_hash_mismatch
)
from vibenix.flake import get_package_contents
from vibenix.nix import eval_progress, execute_build_and_add_to_stack, revert_packaging_to_solution
from vibenix.packaging_flow.model_prompts import get_model_prompt_manager
from vibenix.ccl_log import get_logger, enum_str
from vibenix.defaults import get_settings_manager

from typing import List
from pydantic_ai import ModelMessage

settings_manager = get_settings_manager()
MAX_ITERATIONS = settings_manager.get_setting_value("packaging_loop.max_iterations")
MAX_CONSECUTIVE_REBUILDS_WITHOUT_PROGRESS = settings_manager.get_setting_value("packaging_loop.max_consecutive_rebuilds_without_progress")
# we have to evaluate if a small limit here helps or hurts
# or just get rid of it
MAX_CONSECUTIVE_NON_BUILD_ERRORS = settings_manager.get_setting_value("packaging_loop.max_consecutive_non_build_errors")

def packaging_loop(best: Solution, summary: str,
    max_iterations: int = MAX_ITERATIONS, chat_history: List[ModelMessage] = None, maintenance_mode: bool = False):
    """Loop that receives a Solution (code and build result), and iteratively
    attempts to fix the errors until a successful build is a achieved or a
    limit of (failing, etc.) iterations is reached."""
    ccl_logger = get_logger()

    iteration = 0
    consecutive_rebuilds_without_progress = 0
    consecutive_non_build_errors = 0

    candidate = best
    first_build_error = True
    has_broken_log_output = False
    # Track attempted tool calls to avoid repetition
    attempted_tool_calls = []

    ccl_logger.enter_attribute("iterate")
    while (
        (not candidate.result.success) and
        (iteration < max_iterations) and
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
            import re
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

            if not maintenance_mode:
                fix_build_error(
                    view_package_contents(prompt="fix_build_error"),
                    error_truncated, 
                    summary, 
                    has_broken_log_output,
                    is_dependency_error,
                    is_syntax_error,
                    attempted_tool_calls,
                    iteration_tool_calls,
                    chat_history=chat_history
                )
            else:
                from vibenix.packaging_flow.model_prompts import fix_build_error_maintenance
                fix_build_error_maintenance(
                    view_package_contents(prompt="fix_build_error_maintenance"),
                    error_truncated, 
                    summary, 
                    has_broken_log_output,
                    is_dependency_error,
                    is_syntax_error,
                    attempted_tool_calls,
                    iteration_tool_calls,
                    chat_history=chat_history
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
        coordinator_progress(f"Iteration {iteration + 1}: Testing fix attempt {iteration + 1} of {max_iterations}...")
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

    if candidate.result.success:
        best = candidate

    return candidate, best, {'iterations': iteration, 'consecutive_rebuilds_without_progress': consecutive_rebuilds_without_progress, 'consecutive_non_build_errors': consecutive_non_build_errors}
