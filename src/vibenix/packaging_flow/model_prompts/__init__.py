"""All model prompts for vibenix.

This module contains all functions decorated with @ask_model that interact with the AI model.
"""

from magentic import StreamedStr
from vibenix.template.template_types import TemplateType
from vibenix.ui.conversation import _retry_with_rate_limit, ask_model, ask_model_enum, handle_model_chat
from vibenix.errors import NixBuildErrorDiff, LogDiff, FullLogDiff, ProcessedLogDiff
from magentic import Chat, UserMessage, StreamedResponse, FunctionCall
from vibenix.function_calls import search_nixpkgs_for_package_semantic, search_nixpkgs_for_package_literal, search_nix_functions, search_nixpkgs_for_file

from litellm.integrations.custom_logger import CustomLogger
from litellm.files.main import ModelResponse, BaseModel
import litellm
from enum import Enum
import sys

@ask_model_enum("""
""")
def pick_template(project_page: str) -> TemplateType:
    ...


class RefinementExit(Enum):
    """Enum to represent exit conditions for refinement/evaluation."""
    ERROR = "error"
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"

# 2. Identify unused dependencies. These can be further verified to be unnecessary by checking against build instructions in the project page, local files, etc.;
def get_feedback(code: str, log: str, project_page: str = None, release_data: dict = None, template_notes: str = None, additional_functions: list = []) -> StreamedStr:
    """Refine a nix package to remove unnecessary snippets, add missing code, and improve style."""
    prompt = """
"""

    # Include project information if available
    project_info_section = ""
    if project_page:
        project_info_section = f"""
"""
        if release_data:
            project_info_section += f"""
And some relevant metadata of the latest release:
```
{release_data}
```
"""

    # Include template notes if available
    template_notes_section = ""
    if template_notes:
        template_notes_section = f"""
"""

    chat = Chat(
        messages=[UserMessage(prompt.format(
            code=code, 
            log=log,
            project_info_section=project_info_section,
            template_notes_section=template_notes_section
        ))],
        functions=[search_nixpkgs_for_package_semantic, search_nixpkgs_for_package_literal, search_nix_functions, search_nixpkgs_for_file]+additional_functions,
        output_types=[StreamedResponse],
    )
    chat = _retry_with_rate_limit(chat.submit)

    return handle_model_chat(chat)


def refine_code(code: str, feedback: str, project_page: str = None, release_data: dict = None, template_notes: str = None, additional_functions: list = []) -> StreamedStr:
    """Refine a nix package to remove unnecessary snippets, add missing code, and improve style."""
    prompt = """ """
    # Include project information if available
    project_info_section = ""
    if project_page:
        project_info_section = f"""Here is the information from the project's GitHub page:
```text
{project_page}
```
"""
    if release_data:
        project_info_section += f"""
And some relevant metadata of the latest release:
```
{release_data}
```
"""

    # Include template notes if available
    template_notes_section = ""
    if template_notes:
        template_notes_section = f"""Here are some notes about this template to help you package this type of project:
```
{template_notes}
```
"""

    chat = Chat(
        messages=[UserMessage(prompt.format(
            code=code,
            feedback=feedback,
            project_info_section=project_info_section,
            template_notes_section=template_notes_section
        ))],
        functions=[search_nixpkgs_for_package_semantic, search_nixpkgs_for_package_literal, search_nix_functions, search_nixpkgs_for_file]+additional_functions,
        output_types=[StreamedResponse],
    )

    chat = _retry_with_rate_limit(chat.submit)
    return handle_model_chat(chat)


def fix_build_error(code: str, error: str, project_page: str = None, release_data: dict = None, template_notes: str = None, additional_functions: list = []) -> StreamedStr:
    """Fix a build error in Nix code."""
    prompt = """ """
    # Include project information if available
    project_info_section = ""
    if project_page:
        project_info_section = f"""Here is the information from the project's GitHub page:
```text
{project_page}
```
"""
        if release_data:
            project_info_section += f"""
And some relevant metadata of the latest release:
```
{release_data}
```
"""

    # Include template notes if available
    template_notes_section = ""
    if template_notes:
        template_notes_section = f"""Here are some notes about this template to help you package this type of project:
```
{template_notes}
```
"""

    chat = Chat(
        messages=[UserMessage(prompt.format(
            code=code,
            error=error,
            project_info_section=project_info_section,
            template_notes_section=template_notes_section
        ))],
        functions=[search_nixpkgs_for_package_semantic, search_nixpkgs_for_package_literal, search_nix_functions, search_nixpkgs_for_file]+additional_functions,
        output_types=[StreamedResponse],
    )
    chat = _retry_with_rate_limit(chat.submit)

    return handle_model_chat(chat)


def evaluate_progress(log_diff: LogDiff) -> NixBuildErrorDiff:
    """Evaluate if the build made progress by comparing logs."""
    
    if isinstance(log_diff, FullLogDiff):
        # Create a dynamic function with the full log prompt
        @ask_model_enum(""" """)
        def _evaluate_full_logs(log_diff: FullLogDiff) -> NixBuildErrorDiff:
            ...
        
        return _evaluate_full_logs(log_diff)
    else:  # ProcessedLogDiff
        # Create a dynamic function with the truncated log prompt
        @ask_model_enum("""@model 
""")
        def _evaluate_truncated_logs(log_diff: ProcessedLogDiff) -> NixBuildErrorDiff:
            ...
        
        return _evaluate_truncated_logs(log_diff)

@ask_model("""@model """)
def fix_hash_mismatch(code: str, error: str) -> StreamedStr:
    ...


class PackagingFailure(Enum):
    """Represents a packaging failure with specific details."""
    BUILD_TOOL_NOT_IN_NIXPKGS = "BUILD_TOOL_NOT_IN_NIXPKGS"
    BUILD_TOOL_VERSION_NOT_IN_NIXPKGS = "BUILD_TOOL_VERSION_NOT_IN_NIXPKGS"
    DEPENDENCY_NOT_IN_NIXPKGS = "DEPENDENCY_NOT_IN_NIXPKGS"
    PACKAGING_REQUIRES_PATCHING_OF_SOURCE = "PACKAGING_REQUIRES_PATCHING_OF_SOURCE"
    BUILD_DOWNLOADS_FROM_NETWORK = "BUILD_DOWNLOADS_FROM_NETWORK"
    REQUIRES_SPECIAL_HARDWARE = "REQUIRES_SPECIAL_HARDWARE"
    REQUIRES_PORT_OR_DOES_NOT_TARGET_LINUX = "REQUIRES_PORT_OR_DOES_NOT_TARGET_LINUX"
    OTHER = "OTHER"

@ask_model_enum("""@model 
""")
def classify_packaging_failure(details: str) -> PackagingFailure:
    """Classify a packaging failure based on the provided details."""
    ...

def analyze_package_failure(code: str, error: str, project_page: str = None, release_data: dict = None, template_notes: str = None, additional_functions: list = []) -> StreamedStr:
    """Analyze a package failure to determine the type of failure and describe it."""
    prompt = """ """
    # Include project information if available
    project_info_section = ""
    if project_page:
        project_info_section = f"""Here is the information from the project's GitHub page:
```text
{project_page}
```
"""
        if release_data:
            project_info_section += f"""
And some relevant metadata of the latest release:
```
{release_data}
```
"""

    # Include template notes if available
    template_notes_section = ""
    if template_notes:
        template_notes_section = f"""Here are some notes about this template to help you package this type of project:
```
{template_notes}
```
"""

    chat = Chat(
        messages=[UserMessage(prompt.format(
            code=code,
            error=error,
            project_info_section=project_info_section,
            template_notes_section=template_notes_section
        ))],
        functions=[search_nixpkgs_for_package_semantic, search_nixpkgs_for_package_literal, search_nix_functions, search_nixpkgs_for_file]+additional_functions,
        output_types=[StreamedResponse],
    )
    chat = _retry_with_rate_limit(chat.submit)

    return handle_model_chat(chat)
