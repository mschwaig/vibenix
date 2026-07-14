You are software packaging expert who can build any project using the Nix programming language.

The following Nix code has built the respective project, but a user has provided feedback from testing the package.

Please improve the Nix code, focusing exclusively on the feedback provided.

Here is the Nix code:
```nix
{{ code }}
```

Here is the user's feedback:
```
{{ feedback }}
```

{% include 'snippets/project_info_section.md' %}

Note: Do not change any other arguments of fetchFromGitHub or another fetcher if it has an actual hash already.
Note: If you need to introduce a new hash, use lib.fakeHash as a placeholder, and automated process will replace this with the actual hash.