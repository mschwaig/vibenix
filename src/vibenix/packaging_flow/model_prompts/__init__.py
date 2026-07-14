"""All model prompts for vibenix.

This module contains all functions that interact with the AI model using template-based prompts.
"""

from enum import Enum
from typing import List, Optional

from vibenix.template.template_types import TemplateType
from vibenix.ui.conversation_templated import get_model_prompt_manager
from vibenix.ui.conversation import ModelCodeResponse
from vibenix.errors import NixBuildErrorDiff, LogDiff, FullLogDiff, ProcessedLogDiff

# Re-export enums
from vibenix.packaging_flow.model_prompts.enums import RefinementExit, PackagingFailure


model_prompt_manager = get_model_prompt_manager()
ask_model_prompt = model_prompt_manager.ask_model_prompt

def run_formatter_after(func):
    """Decorator to automatically run Nix formatter after prompts that modify code."""
    import sys
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        try:
            from vibenix.nix import run_formatter
            run_formatter()
        except Exception as e:
            print(f"⚠️  Warning: Failed to format code: {e}", file=sys.stderr, flush=True)
        return result
    return wrapper

def pick_template(templates: List[str], project_page: str) -> TemplateType:
    """Select the appropriate template for a project.

    The underlying model is constrained to an Enum built from the
    provided templates argument (intersected with TemplateType). The
    result is then mapped back to TemplateType for callers.
    """
    enabled_members = {}
    for item in templates:
        for name, member in TemplateType.__members__.items():
            if member.value == item:
                enabled_members[name] = member.value
                break

    if enabled_members:
        EnabledTemplateType = Enum("EnabledTemplateType", enabled_members)
    else:
        print("⚠️  Warning: No valid templates provided for model prompt, falling back to full TemplateType.")
        # If nothing valid was provided, fall back to full TemplateType
        # so the prompt still has a usable schema.
        EnabledTemplateType = TemplateType

    # func name itself not relevant, prompt key is derived from template_path
    @ask_model_prompt('pick_template.md')
    def _pick_template_inner(templates: List[str], project_page: str) -> EnabledTemplateType:
        ...

    result = _pick_template_inner(templates, project_page)

    # If our enabled enum fell back to TemplateType, the result is
    # already the desired type.
    if isinstance(result, TemplateType):
        return result

    # Otherwise, map by enum member name back into TemplateType.
    return TemplateType[result.name]


@ask_model_prompt('summarize_project_source.md')
def summarize_project_source(
    project_readme: str,
    project_root_file_list: str) -> str:
    """Summarize a project based on key source files and directory structure."""
    ...


@ask_model_prompt('refinement/evaluate_code.md')
def evaluate_code(code: str, previous_code: str, feedback: str) -> RefinementExit:
    """Evaluate whether refinement feedback has been successfully implemented."""
    ...


@ask_model_prompt('refinement/get_feedback.md')
def get_feedback(
    code: str,
    chat_history: Optional[List],
    lessons_learned: List[str] = [],
    already_implemented: List[str] = [],
    project_page: Optional[str] = None,
    tree_output: Optional[str] = "",
) -> str:
    """Get feedback on a successfully built package."""
    ...


@ask_model_prompt('refinement/mnt_get_feedback.md')
def mnt_get_feedback(
    code: str,
    chat_history: Optional[List],
    lessons_learned: List[str] = [],
    already_implemented: List[str] = [],
    project_page: Optional[str] = None,
    tree_output: Optional[str] = "",
) -> str:
    """Get feedback on a successfully built package (maintenance mode)."""
    ...


@run_formatter_after
@ask_model_prompt('refinement/refine_code.md')
def refine_code(
    code: str,
    feedback: str,
    chat_history: Optional[List],
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
) -> ModelCodeResponse:
    """Refine a nix package based on feedback."""
    ...


@run_formatter_after
@ask_model_prompt('refinement/improve_code.md')
def improve_code(
    code: str,
    feedback: str,
    chat_history: Optional[List]
) -> ModelCodeResponse:
    """Improve a refined Nix package, with suggestions from linters."""
    ...


@run_formatter_after
@ask_model_prompt('error_fixing/fix_build_error.md')
def fix_build_error(
    code: str,
    error: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    is_broken_log_output: bool = False,
    is_dependency_build_error: bool = False,
    is_syntax_error: bool = False,
    attempted_tool_calls: List = [],
    tool_call_collector: List = None,
    chat_history: Optional[List] = None,
) -> ModelCodeResponse:
    """Fix a build error in Nix code."""
    ...


@run_formatter_after
@ask_model_prompt('error_fixing/mnt_fix_build_error.md')
def fix_build_error_maintenance(
    code: str,
    error: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    is_broken_log_output: bool = False,
    is_dependency_build_error: bool = False,
    is_syntax_error: bool = False,
    attempted_tool_calls: List = [],
    tool_call_collector: List = None,
    chat_history: Optional[List] = None,
) -> ModelCodeResponse:
    """Fix a build error in Nix code during maintenance mode."""
    ...


@ask_model_prompt('error_fixing/fix_hash_mismatch.md')
def fix_hash_mismatch(code: str, error: str) -> ModelCodeResponse:
    """Fix hash mismatch errors in Nix code."""
    ...


# TODO Not managed by settings
def evaluate_progress(log_diff: LogDiff) -> NixBuildErrorDiff:
    """Evaluate if the build made progress by comparing logs."""
    # Use unified template for both full and truncated logs
    @ask_model_prompt('progress_evaluation/evaluate_progress.md')
    def _evaluate(
        previous_log: str,
        new_log: str,
        is_truncated: bool
    ) -> NixBuildErrorDiff:
        ...

    if isinstance(log_diff, FullLogDiff):
        return _evaluate(
            previous_log=log_diff.previous_log,
            new_log=log_diff.new_log,
            is_truncated=False
        )
    else:  # ProcessedLogDiff
        return _evaluate(
            previous_log=log_diff.previous_log_truncated,
            new_log=log_diff.new_log_truncated,
            is_truncated=True
        )


@ask_model_prompt('failure_analysis/classify_packaging_failure.md')
def classify_packaging_failure(details: str) -> PackagingFailure:
    """Classify a packaging failure based on the provided details."""
    ...


@ask_model_prompt('failure_analysis/analyze_packaging_failure.md')
def analyze_package_failure(
    code: str,
    error: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
) -> str:
    """Analyze why packaging failed."""
    ...


@ask_model_prompt('choose_builders.md')
def choose_builders(
    available_builders: List[str],
    project_page: Optional[str] = None) -> List[str]:
    """Identify the Nix builder to use for packaging the project."""
    ...


@run_formatter_after
@ask_model_prompt('compare_template_builders.md')
def compare_template_builders(
    initial_code: str,
    builder_combinations_info: str,
    project_page: Optional[str] = None) -> ModelCodeResponse:
    """Compare the template builders with ones from choose_builders."""
    ...


__all__ = [
    "pick_template",
    "evaluate_code",
    "get_feedback",
    "refine_code",
    "improve_code",
    "fix_build_error",
    "fix_hash_mismatch",
    "evaluate_progress",
    "classify_packaging_failure",
    "analyze_package_failure",
    "choose_builders",
    "compare_template_builders",
    "mnt_get_feedback",
    "fix_build_error_maintenance",
]
