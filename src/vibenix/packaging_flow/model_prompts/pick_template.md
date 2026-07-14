{% include 'snippets/prompt_intro.md' %}

Please pick the most appropriate project template from the following list.

Selectable templates:
{% for template in templates %}
- {{ template }}
{% endfor %}

{% include 'snippets/project_info_section.md' %}
