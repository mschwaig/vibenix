{% include 'snippets/prompt_intro.md' %}

Taking into account the project description below, choose the most specific package builder(s) for this project.

{% include 'snippets/project_info_section.md' %}

{% if build_summary %}
{{ build_summary }}

{% endif %}
**Builder Selection Rules:**
- Choose the **most specific builder** that matches the project's primary language/framework
- Only use `mkDerivation` as a fallback when no specialized builder exists (or to combine multiple builders)
- Choose all required builders, especially for mixed-language projects
- "Application vs. Package" Rule: If the project is a standalone tool the user runs (e.g., a CLI tool or a GUI), prioritize `Application`. If its primarily modules/libraries, use `Package`.

Available builders:
{% for builder in available_builders %}
- {{ builder }}
{% endfor %}

**Response Format**: You must return a list of builder names only. Do not include explanations or reasoning.

Use the tools at your disposal to construct your answer.
