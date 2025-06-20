"""All model prompts for packagerix.

This module contains all functions decorated with @ask_model that interact with the AI model.
"""

from magentic import StreamedStr
from packagerix.template.template_types import TemplateType
from packagerix.ui.conversation import ask_model, ask_model_enum, handle_model_chat
from packagerix.errors import NixBuildErrorDiff
from magentic import Chat, UserMessage, StreamedResponse
from packagerix.function_calls import search_nixpkgs_for_package, web_search, fetch_url_content, search_nix_functions


def set_up_project(code_template: str, project_page: str, release_data: dict = None, template_notes: str = None) -> StreamedStr:
    """Initial setup of a Nix package from a GitHub project."""

    prompt = """You are software packaging expert who can build any project using the Nix programming language.
Your task is to read the contents of the project's GitHub page and fill out all of the sections in the code template that are marked with ... .
Do not make any other modifications or additions. Do not modify the included lib.fakeHash.

This is the code template you have to fill out:
```nix
{code_template}
```   

{template_notes_section}

Here is the information form the project's GitHub page:
```text
{project_page}
```

And some relevant metadata of the latest release:
```
{release_data}
```

Note: Nothing in the meta attribute of a derivation has any impact on its build output, so do not provide a meta attribute.
Note: Do not change any other arguments of fetchFromGitHub or another fetcher if it has an actual hash already.
Note: Your reply should always contain exactly one code block with the updated Nix code.
Note: Even though the provided template uses the mkDerivation function, this is not the appropriate way to package software for most software ecosystems (outside of C/C++).
      Make sure you base your code on an appropriate function provided by nixpkgs instead.
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
            code_template=code_template, 
            project_page=project_page, 
            release_data=release_data,
            template_notes_section=template_notes_section
        ))],
        functions=[search_nixpkgs_for_package, web_search, fetch_url_content, search_nix_functions],
        output_types=[StreamedResponse],
    ).submit()

    return handle_model_chat(chat)


@ask_model("""@model You are software packaging expert who can build any project using the Nix programming language.

Read the contents of the project's GitHub page and summarize it.
Include information like
    1. The build tool which should be invoked to build the project.
    2. A list of build tools and project dependencies.
    3. Other information which might be necessary for buiding the project.
    
    Do not include information which is irrelevant for building the project.
Here is the information form the project's GitHub page:

```text
{project_page}
```

{release_data}
""")
def summarize_github(project_page: str, release_data: dict = None) -> StreamedStr:
    """Summarize a GitHub project page for packaging purposes."""
    ...

@ask_model_enum("""@model You are software packaging expert who can build any project using the Nix programming language.

Please pick the most appropriate project template from the following list.
```text
{project_page}
```
""")
def pick_template(project_page: str) -> TemplateType:
    ...


def fix_build_error(code: str, error: str, project_page: str = None, release_data: dict = None, template_notes: str = None, additional_functions: list = []) -> StreamedStr:
    """Fix a build error in Nix code."""
    prompt = """You are software packaging expert who can build any project using the Nix programming language.

Please fix the following error in the following Nix code.      

```nix
{code}
```

Error:
```
{error}
```

{project_info_section}

{template_notes_section}

If the error message does not give you enough information to make progress, and to verify your actions, look at relevant files in the proejct directory,
and try to compare your approach with similar packages in nixpkgs.
You can also search the web or fetch content if required.
Note: Nothing in the meta attribute of a derivation has any impact on its build output, so do not provide a meta attribute.
Note: Do not change any other arguments of fetchFromGitHub or another fetcher if it has an actual hash already.
Note: Your reply should contain exactly one code block with the updated Nix code.
Note: If you need to introduce a new hash, use lib.fakeHash as a placeholder, and automated process will replace this with the actual hash.
Note: Never replace existing hashes with `lib.fakeHash` or otherwise modify existing hashes."""

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
        functions=[search_nixpkgs_for_package, web_search, fetch_url_content, search_nix_functions]+additional_functions,
        output_types=[StreamedResponse],
    ).submit()

    return handle_model_chat(chat)


@ask_model_enum("""@model You are software packaging expert who can build any project using the Nix programming language.

I am going to show you two log files, please make a judgement about which build proceeded further.

Initial build (total lines: {initial_lines}):
```nix
{initial_error_truncated}
```

Attempted improvement (total lines: {improvement_lines}):
```
{attempted_improvement_truncated}
```

The logs diverge at line {divergence_line}. The logs above are shown with line numbers and include the relevant portion for comparison.

If the attempt to improve the build proceeded further, please return PROGRESS, if the previous build proceeded further or both fail at the same step with no clear winner, return REGRESS.

Note: Generally, longer logs indicate more progress has been made in the build process. Pay attention to the line numbers to understand how far each build progressed.
""")
def evaluate_progress(initial_error_truncated: str, attempted_improvement_truncated: str, 
                     initial_lines: int, improvement_lines: int, divergence_line: int) -> NixBuildErrorDiff:
    ...

@ask_model("""@model You are software packaging expert who can build any project using the Nix programming language.

Please fix the following hash mismatch error in the following Nix code.
In the error message lib.fakeHash is represented as `sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=`.

Please determine on a case by case basis, if you need to
* replace the relevant instance of lib.fakeHash with the actual value from the error message, or
* make lib.fakeHash and an actual hash value switch places in the Nix code.    

```nix
{code}
```

Error:
```
{error}
```
           
Note: Your reply should contain exactly one code block with the updated Nix code.
Note: Never replace more than one instance of lib.fakeHash.
Note: Never put sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA= in the code.
Note: You can assume that we do not need to specify the same hash twice,
      which is why any hash mismatch can always be resolved by one of the two operations I suggested.
""")
def fix_hash_mismatch(code: str, error: str) -> StreamedStr:
    ...
