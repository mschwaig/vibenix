"""Migrated functions using the unified template decorator."""

from magentic import StreamedStr
from vibenix.ui.conversation_templated import ask_model_prompt
from vibenix.template.template_types import TemplateType
from vibenix.packaging_flow.model_prompts import RefinementExit
from vibenix.tools.function_calls import (
    search_nixpkgs_for_package_semantic,
    search_nixpkgs_for_package_literal,
    search_nix_functions,
    search_nixpkgs_for_file
)
from typing import Optional, List


# Example 1: Function returning an Enum (no function calling needed)
@ask_model_prompt('pick_template.md')
def pick_template(project_page: str) -> TemplateType:
    """Select the appropriate template for a project."""
    ...


# Example 2: Function with streaming response and function calling
@ask_model_prompt(
    'error_fixing/fix_build_error.md',
    functions=[
        search_nixpkgs_for_package_semantic,
        search_nixpkgs_for_package_literal,
        search_nix_functions,
        search_nixpkgs_for_file
    ]
)
def fix_build_error(
    code: str,
    error: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    additional_functions: List = []
) -> StreamedStr:
    """Fix a build error in Nix code."""
    ...


# Example 3: Another streaming function with tools
@ask_model_prompt(
    'refinement/get_feedback.md',
    functions=[
        search_nixpkgs_for_package_semantic,
        search_nixpkgs_for_package_literal,
        search_nix_functions,
        search_nixpkgs_for_file
    ]
)
def get_feedback(
    code: str,
    log: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    additional_functions: List = []
) -> StreamedStr:
    """Get feedback on a successfully built package."""
    ...


# Example 4: Simple prompt without function calling
@ask_model_prompt('summarize_project.md')
def summarize_github(project_page: str) -> StreamedStr:
    """Summarize a GitHub project page."""
    ...


@ask_model_prompt(
    'refinement/refine_code.md',
    functions=[
        search_nixpkgs_for_package_semantic,
        search_nixpkgs_for_package_literal,
        search_nix_functions,
        search_nixpkgs_for_file
    ]
)
def refine_code(
    code: str,
    feedback: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    additional_functions: List = []
) -> StreamedStr:
    """Refine a nix package based on feedback."""
    ...


@ask_model_prompt(
    'failure_analysis/analyze_packaging_failure.md',
    functions=[
        search_nixpkgs_for_package_semantic,
        search_nixpkgs_for_package_literal,
        search_nix_functions,
        search_nixpkgs_for_file
    ]
)
def analyze_package_failure(
    code: str,
    error: str,
    project_page: Optional[str] = None,
    template_notes: Optional[str] = None,
    additional_functions: List = []
) -> StreamedStr:
    """Analyze why packaging failed."""
    ...


@ask_model_prompt('refinement/evaluate_code.md')
def evaluate_code(code: str, previous_code: str, feedback: str) -> RefinementExit:
    """Evaluate whether refinement feedback has been successfully implemented."""
    ...