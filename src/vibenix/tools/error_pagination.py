"""Pagination for error messages."""

from vibenix.ccl_log import get_logger, log_function_call
from vibenix.flake import update_flake, get_package_contents


@log_function_call("error_pagination")
def error_pagination(page: int) -> str:
    """Paginate error messages to show a specific page."""
    print(f"📞 Function called: error_pagination with page={page}")
    return _error_pagination(page)


def _error_pagination(page: int) -> str:
    """Paginate error messages to show a specific page."""
    
    try:
        from vibenix.config import config
        if config.error_stack is None or len(config.error_stack) == 0:
            return "No errors available to paginate through."
        error = config.error_stack[-1]
        return error.truncated(page=page)
        
    except Exception as e:
        return f"Error during error pagination: {e}"
