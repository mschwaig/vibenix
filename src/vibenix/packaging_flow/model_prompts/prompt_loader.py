"""Jinja2-based prompt loader for managing prompt templates."""

from pathlib import Path
from typing import Any, Dict, Optional
from jinja2 import Environment, FileSystemLoader, TemplateNotFound
from pydantic import BaseModel


class PromptLoader:
    """Loads and renders prompt templates using Jinja2.
    
    This loader supports:
    - Variable substitution: {{ variable_name }}
    - Including snippets: {% include 'snippets/file.md' %}
    - Automatic Pydantic model conversion to dicts
    - Template caching for performance
    """
    
    def __init__(self, base_path: Optional[Path] = None):
        """Initialize the prompt loader.
        
        Args:
            base_path: Base directory for templates. Defaults to this module's directory.
        """
        if base_path is None:
            base_path = Path(__file__).parent
        
        self.base_path = base_path
        self.env = Environment(
            loader=FileSystemLoader(base_path),
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
            variable_start_string='{{',
            variable_end_string='}}',
        )
    
    def load(self, template_path: str, **context: Any) -> str:
        """Load and render a template with the given context.
        
        Args:
            template_path: Path to template relative to base_path (e.g., 'error_fixing/fix_build_error.md')
            **context: Variables to pass to the template
            
        Returns:
            Rendered template string
            
        Raises:
            TemplateNotFound: If the template file doesn't exist
        """
        # Convert Pydantic models to dicts for easier template access
        processed_context = {}
        for key, value in context.items():
            if isinstance(value, BaseModel):
                processed_context[key] = value.model_dump()
            else:
                processed_context[key] = value
        
        try:
            template = self.env.get_template(template_path)
            return template.render(**processed_context)
        except TemplateNotFound as e:
            raise TemplateNotFound(
                f"Template not found: {template_path}. "
                f"Looked in: {self.base_path}"
            ) from e
    
    def load_snippet(self, snippet_name: str) -> str:
        """Load a snippet directly without rendering variables.
        
        Args:
            snippet_name: Name of snippet file (without path)
            
        Returns:
            Raw snippet content
        """
        snippet_path = f"snippets/{snippet_name}"
        try:
            return self.env.get_template(snippet_path).source
        except TemplateNotFound as e:
            raise TemplateNotFound(
                f"Snippet not found: {snippet_path}"
            ) from e


# Global instance for convenience
_prompt_loader: Optional[PromptLoader] = None


def get_prompt_loader() -> PromptLoader:
    """Get the global prompt loader instance."""
    global _prompt_loader
    if _prompt_loader is None:
        _prompt_loader = PromptLoader()
    return _prompt_loader


def load_prompt(template_path: str, **context: Any) -> str:
    """Convenience function to load a prompt using the global loader.
    
    Args:
        template_path: Path to template relative to model_prompts directory
        **context: Variables to pass to the template
        
    Returns:
        Rendered template string
    """
    return get_prompt_loader().load(template_path, **context)