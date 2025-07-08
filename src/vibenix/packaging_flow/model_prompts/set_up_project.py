from magentic import StreamedStr

def set_up_project(code_template: str, project_page: str, template_notes: str = None) -> StreamedStr:
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
        functions=[search_nixpkgs_for_package_semantic, search_nixpkgs_for_package_literal, search_nix_functions, search_nixpkgs_for_file],
        output_types=[StreamedResponse],
    )
    chat = _retry_with_rate_limit(chat.submit)

    return handle_model_chat(chat)
