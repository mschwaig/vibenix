from typing import List, Callable, Any
from pathlib import Path
import subprocess
import shlex
import os
from magika import Magika
from itertools import islice
from vibenix.ccl_log import get_logger, log_function_call

MAX_LINES_TO_READ = 200
MAX_LINE_LENGTH = 300

def create_source_function_calls(store_path: str, prefix: str = "") -> List[Callable]:
    """
    Create a list of source analysis related function calls.
    
    Args:
        store_path: The root directory path
        prefix: Optional prefix to add to function names (e.g., "nixpkgs_" or "project_")
    """
    root_dir = Path(store_path).resolve()
    
    if not root_dir.exists():
        raise ValueError(f"Root directory '{root_dir}' does not exist")
    
    def _validate_path(path: str) -> Path:
        """Helper function to validate that a path is within the root directory."""
        target_path = (root_dir / path).resolve()
        
        # Check if the resolved path is within the root directory
        try:
            target_path.relative_to(root_dir)
        except ValueError:
            raise ValueError(f"Path '{path}' is outside the allowed root directory '{root_dir}'")
        
        return target_path
    
    def _is_text_file(path: Path) -> bool:
        """Helper function to check if a file is text using magika."""
        if not path.exists():
            raise FileNotFoundError(f"File '{path}' does not exist")
        if not path.is_file():
            # Directories are not text files, return False
            return False
        
        magika = Magika()
        result = magika.identify_path(path)
        return result.output.is_text

    # Create the function names with prefix
    source_description = f"{prefix}source" if prefix else "project source"
    
    def list_directory_contents(relative_path: str) -> str:
        """List contents of a relative directory within the {source_description} given its relative path to the root directory."""
        print(f"📞 Function called: {prefix}list_directory_contents with path: ", relative_path)
        try:
            _validate_path(relative_path)
            # Use command ls -lha to list directory contents
            cmd = f'ls -lha {store_path}/{relative_path}'
            result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
            if result.returncode == 0 and result.stdout.strip():
                # Limit to MAX_LINES_TO_READ lines
                lines = result.stdout.strip().split('\n')
                if len(lines) > MAX_LINES_TO_READ:
                    return '\n'.join(lines[:MAX_LINES_TO_READ]) + f"\n... (showing first {MAX_LINES_TO_READ} of {len(lines)} lines)"
                return str(result.stdout)
            else:
                return f"Failed to list contents of directory '{relative_path}' in {source_description}."
        except Exception as e:
            return f"Error listing directory contents: {str(e)}"

    def read_file_content(relative_path: str, line_offset: int = 0, number_lines_to_read: int = MAX_LINES_TO_READ) -> str:
        """Read the content of a file within the {source_description} given its relative path to the root directory.
        This DOES NOT include viewing `package.nix` file, since it is outside the project source. Use the `view` tool instead. """
        print(f"📞 Function called: {prefix}read_file_content with path: ", relative_path)
        try:
            path = _validate_path(relative_path)
            if not _is_text_file(path):
                return f"File '{relative_path}' is not a text file. {detect_file_type_and_size(relative_path)}."

            number_lines_to_read = min(max(0, number_lines_to_read), MAX_LINES_TO_READ)
            with open(path, 'r', encoding='utf-8') as file:
                content = file.read()
                sliced_lines = islice(content.splitlines(keepends=True), line_offset, line_offset + number_lines_to_read)
                # limit individual line length
                sliced_lines = [line if len(line) <= MAX_LINE_LENGTH else line[:MAX_LINE_LENGTH] + " (... truncated)\n" for line in sliced_lines]
                total_lines = len(content.splitlines())
                if line_offset >= total_lines:
                    return f"Line offset '{line_offset}' is beyond the end of the file which has {total_lines} lines."
                return "".join(sliced_lines) + f"\n(showing lines {line_offset} to {min(line_offset + number_lines_to_read, total_lines)}, out of {total_lines} total lines)"
        except Exception as e:
            return f"Error reading file content: {str(e)}"
    
    def detect_file_type_and_size(relative_path: str) -> str:
        """Detect the type and size of a file within the {source_description} using `magika`, given its relative path to the root directory."""
        print(f"📞 Function called: {prefix}detect_file_type_and_size with path: ", relative_path)
        try:
            path = _validate_path(relative_path)
            if not path.exists():
                return f"File '{relative_path}' does not exist"
            
            magika = Magika()
            result = magika.identify_path(path)
            
            if path.is_file():
                # Get file size
                size_bytes = path.stat().st_size
                
                if result.output.is_text:
                    # Count lines for text files
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            line_count = sum(1 for _ in f)
                        size_info = f"{line_count} lines"
                    except Exception:
                        # Fallback to file size if we can't read as text
                        size_info = _format_file_size(size_bytes)
                else:
                    # Human-readable size for non-text files
                    size_info = _format_file_size(size_bytes)
            else:
                # For directories, show item count
                try:
                    item_count = len(list(path.iterdir()))
                    size_info = f"{item_count} items"
                except Exception:
                    size_info = "directory"
            
            return f"File type: {result.output.ct_label} (confidence: {result.score:.2%}, is_text: {result.output.is_text}, size: {size_info})"
        except Exception as e:
            return f"Error detecting file type: {str(e)}"
    
    def _format_file_size(size_bytes: int) -> str:
        """Format file size in human-readable format."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                if unit == 'B':
                    return f"{size_bytes} {unit}"
                else:
                    return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"

    def search_inside_files(pattern: str, relative_path: str = ".") -> str: # custom_args: str = None
        """Search for a pattern inside files within the {source_description} using `ripgrep`.
        
        Args:
            pattern: The search pattern (regex or literal string)
            relative_path: The relative path to search in (default: current directory)
        """
        print(f"📞 Function called: {prefix}search_inside_files with pattern: '{pattern}', path: '{relative_path}'")
        try:
            path = _validate_path(relative_path)
            custom_args = None
            
            if custom_args:
                # Use custom arguments provided by the user, need to parse them
                import shlex as shlex_parse
                args = shlex_parse.split(custom_args)
                cmd = ["rg"] + args + ["--", pattern, path]
            else:
                # -n: Show line numbers
                # -H: Show filenames
                # -m 5: Max 5 matches per file
                cmd = ["rg", "-n", "-H", "--color=never", "-m", "5", "--max-filesize=10M", "--", pattern, path]
            
            result = subprocess.run(cmd, text=True, capture_output=True, cwd=root_dir)
            
            if result.returncode == 0 and result.stdout.strip():
                # Limit total output to 50 lines
                lines = result.stdout.strip().split('\n')
                # replace all instances of the store path with "source"
                lines = [line.split("-source/")[-1] for line in lines]
                # limit individual line length
                lines = [line if len(line) <= MAX_LINE_LENGTH else line[:MAX_LINE_LENGTH] + "... (truncated)" for line in lines]
                if len(lines) > 50:
                    return '\n'.join(lines[:50]) + f"\n... (showing first 50 of {len(lines)} matches)"
                return '\n'.join(lines)
            elif result.returncode == 1:
                return f"No matches found for pattern '{pattern}' in {relative_path}"
            else:
                error_msg = result.stderr.strip() if result.stderr else "Unknown error"
                return f"Error searching files: {error_msg}"
                
        except Exception as e:
            return f"Error in search_inside_files: {str(e)}"
    
    funcs = [list_directory_contents, read_file_content, search_inside_files] # detect_file_type_and_size
    for i in range(len(funcs)):
        func = funcs[i]
        # Update name and docstring with prefix
        func.__name__ = f"{prefix}{func.__name__}"
        func.__doc__ = func.__doc__.replace("{source_description}", source_description)
        # Apply logging decorator
        func = log_function_call(func.__name__)(func) 
        funcs[i] = func
    
    return funcs
