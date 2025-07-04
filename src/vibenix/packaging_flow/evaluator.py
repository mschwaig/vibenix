"""Refinement cycle for packaging code based on feedback from an evaluator."""

from vibenix.ui.conversation import coordinator_message, coordinator_error, coordinator_progress
from vibenix.parsing import extract_updated_code
from vibenix.nix import execute_build_and_add_to_stack
from vibenix.packaging_flow.model_prompts import evaluate_code, refine_code, get_feedback, RefinementExit
from vibenix.errors import Solution

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
