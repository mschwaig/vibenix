from vibenix.flake import get_package_contents
from vibenix.ui.conversation import ask_user,  coordinator_message, coordinator_error, coordinator_progress
from vibenix.nix import eval_progress, execute_build_and_add_to_stack, revert_packaging_to_solution
from vibenix.packaging_flow.model_prompts import (
    refine_code, get_feedback
)
from vibenix.ccl_log import init_logger, get_logger, close_logger, enum_str
from pydantic import BaseModel
from vibenix.errors import NixBuildErrorDiff, NixErrorKind, NixBuildResult
from vibenix.packaging_flow.Solution import Solution
from vibenix.tools.view import _view as view_package_contents, view

from vibenix.ui.conversation_templated import model_prompt_manager

def refine_package(curr: Solution, project_page: str, additional_functions: list = None) -> Solution:
    """Refinement cycle to improve the packaging."""
    from vibenix.defaults import get_settings_manager
    max_iterations = get_settings_manager().get_setting_value("refinement.iterations")

    from vibenix.ccl_log import get_logger, close_logger
    ccl_logger = get_logger()
    ccl_logger.enter_attribute("refine_package", log_start=True)

    for iteration in range(max_iterations):
        ccl_logger.log_iteration_start(iteration)
        ccl_logger.write_kv("code", curr.code)
        # Get feedback for current code
        # TODO BUILD LOG IS NOT BEING PASSED!
        code_lines = view_package_contents()

        feedback = get_feedback(code_lines, project_page, iteration+1, max_iterations)
        coordinator_message(f"Refining package (iteration {iteration+1}/{max_iterations})...")
        coordinator_message(f"Received feedback: {feedback}")
        ccl_logger.write_kv("feedback", str(feedback))
        if not feedback:
            coordinator_message("No feedback received, ending refinement process.")
            continue

        # Pass the feedback to the generator (refine_code)
        refine_code(code_lines, feedback, project_page)
        updated_code = get_package_contents()
        ccl_logger.write_kv("refined_code", updated_code)
        attempt = execute_build_and_add_to_stack(updated_code)
        
        # Verify the updated code still builds
        if not attempt.result.success:
            coordinator_error(f"Refinement caused a regression ({attempt.result.error.type}), reverting to last successful solution.")
            revert_packaging_to_solution(curr)
            ccl_logger.write_kv("type", attempt.result.error.type)
            ccl_logger.write_kv("error", attempt.result.error.truncated())
        else:
            coordinator_message("Refined packaging code successfuly builds, continuing...")
            curr = attempt

        usage = model_prompt_manager.get_iteration_usage()
        ccl_logger.log_iteration_cost(
            iteration=iteration,
            iteration_cost=usage.calculate_cost(),
            input_tokens=usage.prompt_tokens,
            output_tokens=usage.completion_tokens
        )
    # Close the iteration list and refine_package attribute
    ccl_logger.leave_list()
    ccl_logger.leave_attribute(log_end=True)
    coordinator_message("Refinement process reached its conclusion (max iterations).")
    return curr


