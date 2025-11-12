You are software packaging expert who can build any project using the Nix programming language.

Your task is to fix the following error in the following Nix code, making only the necessary changes, avoiding other modifications or additions.

```nix
{{ code }}
```

Error:
```
{{ error }}
```

{% if is_broken_log_output %}
**IMPORTANT: The build log output appears to be garbled. This is typically because the build system is outputting interactive elements (progress bars, ANSI codes, etc.) that don't work well in non-interactive environments.** However, it can also have other reasons.

**You MUST prioritize fixing the log output and use your search tools to find a solution:**
1. Search nixpkgs for packages using the same build system to see how they handle this
2. Search the project source files for build configuration options or documentation about non-interactive output
3. Look at the project's CI configuration files (e.g., .github/workflows, .gitlab-ci.yml) to see how they handle non-interactive builds
4. Look for command-line flags or environment variables that control output formatting

Do NOT guess at solutions - use your tools to find real examples and patterns from nixpkgs, the project's CI, or the project itself.

{% endif %}
{% if is_dependency_build_error %}
**IMPORTANT: This appears to be a dependency build failure, not an error in your derivation.**

The error shows that a dependency from nixpkgs failed to build. **DO NOT try to fix the dependency itself.**

Common causes for dependency build failures:
1. Missing lock files (package-lock.json, Cargo.lock, etc.) - Check if the project needs these files
2. Wrong nixpkgs version or channel - But you cannot change this
3. Platform-specific issues - But you cannot fix these

**Focus only on:**
- Checking if the project requires lock files that are missing and finding them
- Ensuring your derivation correctly specifies its dependencies
- Looking for alternative ways to express the dependency requirements

**DO NOT:**
- Try to write derivations for the failing dependencies
- Attempt to patch or fix nixpkgs packages
- Get distracted from building the main project

{% endif %}
{% if is_syntax_error %}
**IMPORTANT: This appears to be an evaluation error, derived from syntactical mistakes.**

Remember that:
- **The causes for syntax errors are very often made before the location where the error is reported.**
- Strings are ONLY defined with quotation marks (")
- Multi-line strings are ONLY defined with double apostrophes ('')
- Double apostrophes ('') in multi-line strings are escaped with a single apostrophe ('), e.g. `var = ''test ''' end'';` is a single multi-line string with one escaped apostrophe.
- In multi-line strings, dollar signs ($), dollar-curly (${), line-feed (\n), and carriage-return (\n) must be escaped by prepending them with double apostrophes (''). For example, `var = ''test ''$var end'';` is a single multi-line string with one escaped dollar sign.
- For the remaining cases, special characters are usually escaped with a backslash (\).
- If you need packages or attributes from a package set like `python3Packages` or `qt6`, only add the package set at the top of the file and not the full path to the attribute. Then, use `python3Packages.package_name` or `with python3Packages; [ package_name ]` to add the package.

{% endif %}
{% if attempted_tool_calls %}
**The following tool calls have already been attempted without making progress. Consider trying different approaches:**
{% for call in attempted_tool_calls %}
- {{ call.function }}({% for key, value in call.arguments.items() %}{{ key }}="{{ value }}"{% if not loop.last %}, {% endif %}{% endfor %})
{% endfor %}

**Suggestions for alternative approaches:**
- Try different search queries or terms
- Look in different parts of nixpkgs or the project
- Use a different tool that might provide better information
- Consider if the issue requires a fundamentally different solution approach

{% endif %}
{% include 'snippets/project_info_section.md' %}

{% include 'snippets/template_note_section.md' %}

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
- Do not the arguments of fetchFromGitHub or without good reason.
- If you need to introduce a new hash, use lib.fakeHash as a placeholder, and automated process will replace this with the actual hash.
- ONLY replace existing hashes with `lib.fakeHash`, if you need to add an argument to a fetcher, like `leaveDotGit` keep the git directory as part of the source code or `fetchSubmodule` to fetch submodules. In those cases you MUST change the hash to `lib.fakeHash` at the same time as well, or the fetched contents will not be updated.
- 'lib.customisation.callPackageWith: Function called without required argument X usually means the name of package X in the function arguments is not correct, or package X does not exist, or is part of a package set which should be listed as a function argument at the top of the file instead of the package.
- If you search for a package using your tools, and you don't have a match, try again with another query or try a different tool.
- Many build functions, like `mkDerivation` provide a C compiler and a matching libc. If you're missing libc anyways, the GNU libc package is called `glibc` in nixpkgs.
- Do not produce a flatpak, or docker container and do not use tools related to theres technologies to produce your output. Use tools to find other more direct ways to build the project.
- If you need packages from a package set like `python3Packages` or `qt6`, only add the package set at the top of the file and use `python3Packages.package_name` or `with python3Packages; [ package_name ]` to add the package.
- You will not find the package that I am asking you to build in nixpkgs already.
