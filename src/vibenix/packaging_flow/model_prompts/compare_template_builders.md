{% include 'snippets/prompt_intro.md' %}

{% include 'snippets/project_info_section.md' %}

Your primary goal is to modify the structure of the Nix packaging template provided below. Your modifications should be strictly limited to the following actions:
*   **Modify:** Change the existing builder function, if the main package requires a different builder.
*   **Add:** Add more builder functions and respective derivations.
*   **No Change:** Leave the template as is if the existing structure is already appropriate.

Focus exclusively on these structural changes.

Here is the chosen Nix packaging template:
```nix
{{ initial_code }}
```

Here is information regarding builder combinations and packages using them:
```text
{{ builder_combinations_info }}
```

Strictly adhere to the following constraints:
*   Do NOT add, remove, or modify any package dependencies (e.g., `buildInputs`, `nativeBuildInputs`). The model should not add any new packages to the build.
*   Do NOT alter the `src`, `pname`, `version`, or any other metadata attributes.
*   Do NOT make any stylistic changes, add comments, or refactor the code unnecessarily. Your changes must be purely functional and directly related to the builder logic.