"""View tool to examine the current packaging expression."""

from vibenix.ccl_log import get_logger, log_function_call
from vibenix.flake import get_package_contents


@log_function_call("view")
def view() -> str:
    """Examine the contents of the current packaging expression.

    Args:
        view_range: Optional(list[int]): A list of two integers specifying the start and end lines to view (1-indexed, inclusive).
    """
    print(f"📞 Function called: view")
    return _view()


def _view(view_range: list[int]=None, prompt: str=None, line_numbers: bool=False) -> str:
    """Examine the contents of the current packaging expression."""
    
    try:
        # Get current package contents
        current_content = get_package_contents()
        
        # Check if view_range is valid
        lines = current_content.splitlines()
        # Add `<line_number>: ` prefix to each line
        from vibenix.defaults.vibenix_settings import get_settings_manager

        # Show line numbers if prompt uses edit tools or if specifically requested
        if prompt or line_numbers:
            prompt_tools = (get_settings_manager().is_edit_tools_prompt(prompt) 
             and get_settings_manager().get_setting_enabled("edit_tools"))

            show_line_numbers = line_numbers or prompt_tools
            if show_line_numbers:
                lines = [f"{i+1:>3}: {line}" for i, line in enumerate(lines)]

        if view_range:
            if len(view_range) != 2:
                error_msg = "Invalid `view_range`: must be a list of two integers."
                return error_msg
            start, end = view_range
            if start < 1 or end > len(lines) or start > end:
                error_msg = f"Invalid `view_range`: {view_range}. Packaging code has lines between 1 and {len(lines)} (inclusive), and start < end must hold."
                return error_msg
            if start == end: # if its trying to view a single line, make end one more than start (just to make life easier for the model)
                end += 1
            # Slice the lines to the specified range
            lines = lines[start:end]

        # Join the lines back into a single string
        content_to_view = "\n".join(lines)
        return content_to_view
        
    except Exception as e:
        error_msg = f"Error viewing package contents: {str(e)}"
        return error_msg
