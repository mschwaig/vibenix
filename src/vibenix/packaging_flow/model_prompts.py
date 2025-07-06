"""All model prompts for vibenix.

This module contains all functions decorated with @ask_model that interact with the AI model.
"""

from magentic import StreamedStr
from vibenix.template.template_types import TemplateType
from vibenix.ui.conversation import _retry_with_rate_limit, ask_model, ask_model_enum, handle_model_chat
from vibenix.errors import NixBuildErrorDiff
from magentic import Chat, UserMessage, StreamedResponse
from vibenix.function_calls import search_nixpkgs_for_package, search_nix_functions

from litellm.integrations.custom_logger import CustomLogger
from litellm.files.main import ModelResponse
import litellm
from enum import Enum
import os
import sys


class EndStreamLogger(CustomLogger):
    """A custom callback handler to log usage and cost at the end of a successful call."""
    def __init__(self):
        super().__init__()
        self.total_cost = 0.0
        
    def log_success_event(self, kwargs, response_obj: ModelResponse, start_time, end_time):
        print("\n--- STREAM COMPLETE (Callback Triggered) ---")
        try:
            # response_obj is the final, aggregated response.
            # For streaming, the usage is available in the final chunk's response_obj.
            if response_obj and hasattr(response_obj, 'usage'):
                usage = response_obj.usage
                print(f"Final Prompt Tokens: {usage.prompt_tokens}")
                print(f"Final Completion Tokens: {usage.completion_tokens}")
                print(f"Final Total Tokens: {usage.total_tokens}")

                # Calculate cost from the final aggregated response
                cost = litellm.completion_cost(completion_response=response_obj)
                print(f"Total Stream Cost: ${cost:.6f}")
                self.total_cost += cost
            else:
                if kwargs.get("response_cost") is not None:
                     cost = kwargs['response_cost']
                     print(f"Total Stream Cost (from kwargs): ${cost:.6f}")
                     self.total_cost += cost

        except Exception as e:
            print(f"Error in success_callback: {e}")
        finally:
            print("------------------------------------------\n")


class TokenLimitEnforcer(CustomLogger):
    """Enforce token limit."""

    def __init__(self, limit=32000):
        super().__init__()
        self.limit = limit

    def log_pre_api_call(self, model, messages, kwargs):
        """Check estimated tokens and crash if over limit."""
        estimated_tokens = litellm.token_counter(model=model, messages=messages)

        if estimated_tokens > self.limit:
            print(f"\n\nFATAL ERROR: Token limit exceeded!")
            print(f"Estimated input tokens: {estimated_tokens}")
            print(f"Token limit: {self.limit}")
            print("Terminating application.\n")
            sys.exit(1)

        # Set max_tokens to remaining space
        remaining = self.limit - estimated_tokens
        kwargs["max_tokens"] = min(kwargs.get("max_tokens", remaining), remaining)

    def log_success_event(
        self, kwargs, response_obj: ModelResponse, start_time, end_time
    ):
        """Verify actual usage didn't exceed limit."""
        if not response_obj or not hasattr(response_obj, "usage"):
            print(
                "\n\nFATAL ERROR: Cannot verify token usage - response missing usage data"
            )
            print("Terminating application.\n")
            sys.exit(1)

        total_tokens = response_obj.usage.total_tokens
        if total_tokens > self.limit:
            print(f"\n\nFATAL ERROR: Token limit exceeded!")
            print(f"Total tokens used: {total_tokens}")
            print(f"Token limit: {self.limit}")
            print("Terminating application.\n")
            sys.exit(1)


token_limit_enforcer = TokenLimitEnforcer(limit=32000)
end_stream_logger = EndStreamLogger()
litellm.callbacks = [token_limit_enforcer, end_stream_logger]


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
        functions=[search_nixpkgs_for_package, search_nix_functions],
        output_types=[StreamedResponse],
    )
    chat = _retry_with_rate_limit(chat.submit)

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


class RefinementExit(Enum):
    """Enum to represent exit conditions for refinement/evaluation."""
    ERROR = "error"
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"

@ask_model_enum("""@model You are software packaging expert who can build any project using the Nix programming language.

The following Nix code successfuly builds the respective project.

Your task is to evaluate whether or not the feedback a professional evaluator provided regarding the packaging has been successfully implemented, and the packaging completed.
Return: 
    - ERROR if there has been a regression in the packaging code, and should revert to the previous code;
    - INCOMPLETE if the feedback has not yet been mostly implemented, meaning the packaging improvement is not yet complete;
    - COMPLETE if the packaging is largely complete, that is, the improvements have been mostly implemented.

Here is the current Nix code for you to evaluate:
```nix
{code}
```

Here is the feedback provided by the evaluator:
```text
{feedback}
```

Here is the previous Nix code:
```nix
{previous_code}
```
""")
def evaluate_code(code: str, previous_code: str, feedback: str = None) -> RefinementExit:
    ...

def get_feedback(code: str, log: str, project_page: str = None, release_data: dict = None, template_notes: str = None, additional_functions: list = []) -> StreamedStr:
    """Refine a nix package to remove unnecessary snippets, add missing code, and improve style."""
    prompt = """You are software packaging expert who can build any project using the Nix programming language.

The following Nix code successfuly builds the respective project.

Your task is to identify if there exist concrete improvements to the packaging code, namely:
    1. Ensure a reasonable coding style, removing unnecessary comments and unused code, such as dangling template snippets;
    2. Identify unused dependencies. These can be further verified to be unnecessary by checking against build instructions in the project page, local files, etc.;
    3. Identify missing dependencies, mentioned in the project page, local files, etc.;
    4. Ensure the Nix builder function has the "doInstallCheck" set to true and includes the "installCheckPhase" attribute.
    This attribute should be set to, at least, programatically verify basic execution of the program, and that the expected
    binaries and libraries are, respectively, executable and present.

Among the tools at your disposal for the task, you can: 
- compare your approach with similar packages in nixpkgs;
- look at relevant files in the project directory in the Nix store;
- search for nixpkgs package names or functions in Noogle.

Above improving the package, prioritize not breaking the build.
Make extensive use of the available tools to verify your feedback before making assumptions.

You should look at the build output in the Nix store to verify its validity.

Here is the Nix code for you to evaluate:
```nix
{code}
```

Here is the last build output:
```
{log}
```

{project_info_section}

{template_notes_section}

Notes:
- The meta attribute is irrelevant, do not include it.
- Do not attempt to generate the full updated packaging code, only provide feedback on the existing code.
- Do not access the project's online git repository, such as GitHub, and instead browse the local files in the Nix store.
- Only test the execution of GUI programs under a virtual display environment when necessary, that is, when a CLI-only iteraction is not possible.
- Your feedback needs to be concise, concrete to the specific project source, and follow the given format:
```text
# <1. improvement title>
<1st improvement description>

# <2. improvement title>
<2nd improvement description>
(...)
```
"""

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
            log=log,
            project_info_section=project_info_section,
            template_notes_section=template_notes_section
        ))],
        functions=[search_nixpkgs_for_package, search_nix_functions]+additional_functions,
        output_types=[StreamedResponse],
    )
    chat = _retry_with_rate_limit(chat.submit)

    return handle_model_chat(chat)


def refine_code(code: str, feedback: str, project_page: str = None, release_data: dict = None, template_notes: str = None, additional_functions: list = []) -> StreamedStr:
    """Refine a nix package to remove unnecessary snippets, add missing code, and improve style."""
    prompt = """You are software packaging expert who can build any project using the Nix programming language.

The following Nix code has built the respective project, but an expert evaluator identifed possible improvements.

Your task is to improve the Nix package code, following the feedback provided.

Here is the Nix code:
```nix
{code}
```

Here is the evaluator's feedback:
```
{feedback}
```

Only make the necessary changes to implement the feedback. Do not make any other other unrelated or unnecessary modifications or additions.

{project_info_section}

{template_notes_section}

Among the tools at your disposal for the task, you can:
    - compare your approach with similar packages in nixpkgs;
    - look at relevant files in the project directory in the Nix store;
    - search for nixpkgs packages names or functions in Noogle.

Note: The meta attribute is irrelevant, do not include it.
Note: Do not change any other arguments of fetchFromGitHub or another fetcher if it has an actual hash already.
Note: Your reply should contain exactly one code block with the updated Nix code.
Note: If you need to introduce a new hash, use lib.fakeHash as a placeholder, and automated process will replace this with the actual hash."""

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
        functions=[search_nixpkgs_for_package, search_nix_functions]+additional_functions,
        output_types=[StreamedResponse],
    )

    chat = _retry_with_rate_limit(chat.submit)
    return handle_model_chat(chat)


def fix_build_error(code: str, error: str, project_page: str = None, release_data: dict = None, template_notes: str = None, additional_functions: list = []) -> StreamedStr:
    """Fix a build error in Nix code."""
    prompt = """You are software packaging expert who can build any project using the Nix programming language.

Your task is to fix the following error in the following Nix code, making only the necessary changes, avoiding other modifications or additions.

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

Known errors:
- `error: evaluation aborted with the following error message: 'lib.customisation.callPackageWith: Function called without required argument "package_name" at /nix/store/[...]`:
   This error indicates that one of the function arguments you specified at the top of the file was not found and is incorrect.
   The package in question might now be available nixpkgs, might be avilable under a different name, or might be part of a package set only.
   Use tools to find the package or other code that depends on the same package.
Notes:
- Nothing in the meta attribute of a derivation has any impact on its build output, so do not provide a meta attribute.
- Do not access the project's online git repository, such as GitHub, and instead browse the local files in the Nix store.
- Do not change any other arguments of fetchFromGitHub or another fetcher if it has an actual hash already.
- Your reply should contain exactly one code block with the updated Nix code.
- If you need to introduce a new hash, use lib.fakeHash as a placeholder, and automated process will replace this with the actual hash.
- Never replace existing hashes with `lib.fakeHash` or otherwise modify existing hashes.
- 'lib.customisation.callPackageWith: Function called without required argument... usually means you've misjudged the package's name, or the package does not exist.
- If you search for a package using your tools, and you don't have a match, try again with another query or try a different tool.
- Many build functions, like `mkDerivation` provide a C compiler and a matching libc. If you're missing libc anyways, the GNU libc package is called `glibc` in nixpkgs.
- Do not produce a flatpak, or docker container and do not use tools related to theres technologies to produce your output. Use tools to find other more direct ways to build the project.
- If you need packages from a package set like `python3Packages` or `qt6`, only add the package set at the top of the file and use `python3Packages.package_name` or `with python3Packages; [ package_name ]` to add the package."""
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
        functions=[search_nixpkgs_for_package, search_nix_functions]+additional_functions,
        output_types=[StreamedResponse],
    )
    chat = _retry_with_rate_limit(chat.submit)

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
