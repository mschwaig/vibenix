"""Insert tool to insert text at a specific location in the current packaging expression."""

from vibenix.ccl_log import get_logger, log_function_call
from vibenix.flake import update_flake, get_package_contents
from difflib import unified_diff


@log_function_call("insert_line_after")
def insert_line_after(line_number: int, new_content: str) -> str:
    """Inserts a new line of content after a specified line number.
    Pushes the line already at line_number and all following lines down by one.
    
    Args:
      new_content: The string to be inserted as a new line.
      line_number: The line number after which the new content will be inserted.
    """
    print(f"📞 Function called: insert_line_after")
    return _insert(line_number, new_content)


def _insert(insert_line: int, new_str: str) -> str:
    """Insert text at a specific location in the current packaging expression."""
    
    try:
        # Get current package contents
        current_content = get_package_contents()
        new_str = new_str.rstrip("\n").lstrip("\n")  # Remove leading/trailing newlines
        
        # Check if insert_line is valid
        lines = current_content.splitlines()
        if insert_line < 0 or insert_line > len(lines):
            error_msg = f"Invalid `insert_line`: {insert_line}. Valid range is 0 to {len(lines)}." # TODO
            return error_msg
        
        # Insert the new string at the specified line
        lines.insert(insert_line, new_str)
        updated_content = "\n".join(lines)
        
        # Check if replacement actually changed something
        if updated_content == current_content:
            error_msg = "Replacement resulted in no changes"
            return error_msg

        # Test if it breaks syntax
        from vibenix.nix import check_syntax
        syntax_err = check_syntax(updated_content)
        if syntax_err and "expect" in syntax_err:
            syntax_error_index = syntax_err.index("error: syntax error")
            error_truncated = syntax_err[syntax_error_index:]
            error_msg = f"Error: Insertion aborted, breaks syntax:\n{error_truncated}"
            return error_msg
        
        # Update the flake with new content
        update_flake(updated_content)
        
        # Show all lines starting from first changed line, mark inserted lines with *
        start_line = insert_line-1
        updated_lines = updated_content.splitlines()
        previous_lines = current_content.splitlines()
        
        diff_lines = []
        # Updated lines get * marker, other lines are shown for context (updated line number)
        first_diff_index = next(i for i in range(min(len(previous_lines), len(updated_lines))) if previous_lines[i] != updated_lines[i])
        diff_lines = []
        for i, line in enumerate(updated_lines[first_diff_index:], start=first_diff_index):
            if i < first_diff_index+len(new_str.splitlines()):
                diff_lines += [f"*{i + 1:>3}: {line}"]
            else:
                diff_lines += [f" {i + 1:>3}: {line}"]
        diff = "\n".join(diff_lines)
        return_msg = f"Lines starting from {insert_line}:\n```\n{diff}\n```"

        return f"Successfuly inserted text. {return_msg}"
        
    except Exception as e:
        error_msg = f"Error inserting text: {str(e)}"
        return error_msg
