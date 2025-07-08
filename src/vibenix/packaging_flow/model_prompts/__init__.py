"""All model prompts for vibenix.

This module contains all functions that interact with the AI model using template-based prompts.
"""

from enum import Enum
from typing import List, Optional

from magentic import StreamedStr
from vibenix.template.template_types import TemplateType
from vibenix.ui.conversation_templated import ask_model_prompt
from vibenix.tools.function_calls import (
    search_nixpkgs_for_package_semantic,
    search_nixpkgs_for_package_literal,
    search_nix_functions,
    search_nixpkgs_for_file
)
from vibenix.errors import NixBuildErrorDiff, LogDiff, FullLogDiff, ProcessedLogDiff

# Re-export enums
from vibenix.packaging_flow.model_prompts.enums import RefinementExit, PackagingFailure

# Standard search functions for all prompts that need them
SEARCH_FUNCTIONS = [
    search_nixpkgs_for_package_semantic,
    search_nixpkgs_for_package_literal,
    search_nix_functions,
    search_nixpkgs_for_file
]


@ask_model_prompt('pick_template.md')
def pick_template(project_page: str) -> TemplateType:
    """Select the appropriate template for a project."""
    ...


@ask_model_prompt('summarize_project.md')
def summarize_github(project_page: str) -> StreamedStr:
    """Summarize a GitHub project page."""
    ...


@ask_model_prompt('refinement/evaluate_code.md')
def evaluate_code(code: str, previous_code: str, feedback: str) -> RefinementExit:
    """Evaluate whether refinement feedback has been successfully implemented."""
    ...


@ask_model_prompt('refinement/get_feedback.md', functions=SEARCH_FUNCTIONS)
def get_feedback(
    code: str,
    log: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    additional_functions: List = []
) -> StreamedStr:
    """Get feedback on a successfully built package."""
    ...


@ask_model_prompt('refinement/refine_code.md', functions=SEARCH_FUNCTIONS)
def refine_code(
    code: str,
    feedback: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    additional_functions: List = []
) -> StreamedStr:
    """Refine a nix package based on feedback."""
    ...


@ask_model_prompt('error_fixing/fix_build_error.md', functions=SEARCH_FUNCTIONS)
def fix_build_error(
    code: str,
    error: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    additional_functions: List = []
) -> StreamedStr:
    """Fix a build error in Nix code."""
    ...


@ask_model_prompt('error_fixing/fix_hash_mismatch.md')
def fix_hash_mismatch(code: str, error: str) -> StreamedStr:
    """Fix hash mismatch errors in Nix code."""
    ...


def evaluate_progress(log_diff: LogDiff) -> NixBuildErrorDiff:
    """Evaluate if the build made progress by comparing logs."""
    if isinstance(log_diff, FullLogDiff):
        # Use the full log template for complete logs
        @ask_model_prompt('progress_evaluation/evaluate_full_logs.md')
        def _evaluate_full(previous_log: str, new_log: str) -> NixBuildErrorDiff:
            ...
        return _evaluate_full(log_diff.previous_log, log_diff.new_log)
    else:  # ProcessedLogDiff
        # Use the truncated log template for processed logs
        @ask_model_prompt('progress_evaluation/evaluate_truncated_logs.md')
        def _evaluate_truncated(
            previous_truncated_log: str,
            new_truncated_log: str,
            previous_log_hash: str,
            new_log_hash: str
        ) -> NixBuildErrorDiff:
            ...
        return _evaluate_truncated(
            log_diff.previous_truncated_log,
            log_diff.new_truncated_log,
            log_diff.previous_log_hash,
            log_diff.new_log_hash
        )


@ask_model_prompt('failure_analysis/classify_packaging_failure.md')
def classify_packaging_failure(details: str) -> PackagingFailure:
    """Classify a packaging failure based on the provided details."""
    ...


@ask_model_prompt('failure_analysis/analyze_packaging_failure.md', functions=SEARCH_FUNCTIONS)
def analyze_package_failure(
    code: str,
    error: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    additional_functions: List = []
) -> StreamedStr:
    """Analyze why packaging failed."""
    ...


# Import set_up_project from separate module since it's not yet migrated
from vibenix.packaging_flow.model_prompts.set_up_project import set_up_project

# Import logger callbacks
from vibenix.packaging_flow.litellm_callbacks import EndStreamLogger
end_stream_logger = EndStreamLogger()