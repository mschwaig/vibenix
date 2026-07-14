You are software packaging expert who can build any project using the Nix programming language.

The following Nix code has built the respective project, and has been refined and validated.
Now, we need to improve its overall "presentation", so it can be accepted into the nixpkgs repository.

Please improve the Nix code, focusing exclusively on these aspects:
 - Pruning: removing dead code, empty attributes, boilerplate/unnecessary comments;
 - Implementing linter feedback.

Here is the Nix code:
```nix
{{ code }}
```

Here is the linter feedback:
```
{{ feedback }}
```

Try to do both things (as much as possible) in one single answer.

{% include 'snippets/project_info_section.md' %}

Note: The meta attribute is irrelevant, do not include it.
Note: Do not change any other arguments of fetchFromGitHub or another fetcher if it has an actual hash already.
Note: If you need to introduce a new hash, use lib.fakeHash as a placeholder, and automated process will replace this with the actual hash."""
