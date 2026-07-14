"""String replacement tool for modifying the current packaging expression."""

from vibenix.ccl_log import get_logger, log_function_call
from vibenix.flake import update_flake, get_package_contents


@log_function_call("str_replace")
def str_replace(old_str: str, new_str: str, occurrence: int = 1) -> str:
    """
    Replace text in the current packaging expression.
    DO NOT include line numbers (`1: `), just the exact text to find and replace.
    
    Args:
        old_str: The exact text to find and replace (must match exactly including whitespace)
        new_str: The replacement text
        occurrence: Which occurrence to replace if multiple matches exist (1-based, defaults to first)
    
    Example:
        str_replace("buildInputs = [];", "buildInputs = [ cmake ];")
    """
    print(f"📞 Function called: str_replace")
    return _str_replace(old_str, new_str, occurrence)


def _str_replace(old_str: str, new_str: str, occurrence: int = None) -> str:
    """Replace text in the current packaging expression."""
    
    try:
        # Get current package contents
        current_content = get_package_contents()
        
        if not old_str:
            return f"Error replacing: `old_str` cannot be empty."

        if old_str == new_str:
            return f"Error replacing: `old_str` and `new_str` are identical; no changes made."

        count = current_content.count(old_str)
        if count == 0:
            import re
            # Use regex instead for flexible whitespace matching
            escaped_old = re.escape(old_str.strip())
                
            # Replace literal spaces in the escaped string with \s+ (one or more whitespace)
            pattern = escaped_old.replace(r'\ ', r'\s+')
            matches = list(re.finditer(pattern, current_content))
            
            if not matches:
                return "Error replacing: Text not found in packaging expression (even with flexible whitespace)."
            
            if len(matches) < occurrence:
                return f"Error replacing: Only found {len(matches)} occurrences."
            
            # Perform the replacement at the specific match location
            match = matches[occurrence - 1]
            new_content = (
                current_content[:match.start()] + 
                new_str + 
                current_content[match.end():]
            )
            
            update_flake(new_content)
            return f"Successfully replaced text."

        # Validate occurrence parameter
        if count > 1 and occurrence:
            if occurrence not in range(1, count + 1):
                error_msg = f"Error: Requested occurrence {occurrence} outside range 1 to {count}.\n"
                error_msg += "All occurrences:\n"
                for i, line in enumerate(current_content.splitlines(), start=1):
                    if old_str in line:
                        error_msg += f"{i:>3}: {line}\n"
                return error_msg

            # Replace the specified occurrence
            parts = current_content.split(old_str)
            updated_content = old_str.join(parts[:occurrence]) + new_str + old_str.join(parts[occurrence:])
        else:
            updated_content = current_content.replace(old_str, new_str)

        # Test if it breaks syntax (commented in favor of prompt with syntax hints)
        #from vibenix.nix import check_syntax
        #syntax_err = check_syntax(updated_content)
        #if syntax_err and "expect" in syntax_err:
        #    syntax_error_index = syntax_err.index("error: syntax error")
        #    error_truncated = syntax_err[syntax_error_index:]
        #    error_msg = f"Error: Insertion aborted, breaks syntax:\n{error_truncated}"
        #    return error_msg

        update_flake(updated_content)

        return f"Successfully replaced text."
        
    except Exception as e:
        error_msg = f"Error during string replacement: {str(e)}"
        return error_msg
