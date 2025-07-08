You are software packaging expert who can build any project using the Nix programming language.

Your task is to fix the following error in the following Nix code, making only the necessary changes, avoiding other modifications or additions.

```nix
{{ code }}
```

Error:
```
{{ error }}
```

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
- Do not change any other arguments of fetchFromGitHub or another fetcher if it has an actual hash already.
- Your reply should contain exactly one code block with the updated Nix code.
- If you need to introduce a new hash, use lib.fakeHash as a placeholder, and automated process will replace this with the actual hash.
- Never replace existing hashes with `lib.fakeHash` or otherwise modify existing hashes.
- 'lib.customisation.callPackageWith: Function called without required argument... usually means you've misjudged the package's name, or the package does not exist.
- If you search for a package using your tools, and you don't have a match, try again with another query or try a different tool.
- Many build functions, like `mkDerivation` provide a C compiler and a matching libc. If you're missing libc anyways, the GNU libc package is called `glibc` in nixpkgs.
- Do not produce a flatpak, or docker container and do not use tools related to theres technologies to produce your output. Use tools to find other more direct ways to build the project.
- If you need packages from a package set like `python3Packages` or `qt6`, only add the package set at the top of the file and use `python3Packages.package_name` or `with python3Packages; [ package_name ]` to add the package.