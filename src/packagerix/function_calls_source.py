from typing import List, Callable, Any
from pathlib import Path
import subprocess
from magika import Magika
from itertools import islice

MAX_LINES_TO_READ = 500

def create_source_function_calls(store_path: str) -> List[Callable]:
    """
    Create a dictionary of project source analysis related function calls.
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
            raise ValueError(f"Path '{path}' is not a file")
        
        magika = Magika()
        result = magika.identify_path(path)
        return result.output.is_text

    def list_directory_contents(relative_path: str) -> str:
        """List contents of a relative directory within the project source given its relative path to the root directory."""
        print("📞 Function called: list_directory_contents with path: ", relative_path)
        try:
            _validate_path(relative_path)
            # Use command ls -lha to list directory contents
            cmd = f'ls -lha {store_path}/{relative_path}'
            result = subprocess.run(cmd, shell=True, text=True, capture_output=True)
            if result.returncode == 0 and result.stdout.strip():
                return str(result.stdout)
            else:
                return f"Failed to list contents of directory '{relative_path}'\
                        on the project source directory."
        except Exception as e:
            return f"Error listing directory contents: {str(e)}"

    def read_file_content(relative_path: str, line_offset: int = 0, number_lines_to_read: int = MAX_LINES_TO_READ) -> str:
        """Read the content of a file within the project source given its relative path to the root directory."""
        print("📞 Function called: read_file_content with path: ", relative_path)
        try:
            path = _validate_path(relative_path)
            if not _is_text_file(path):
                return f"File '{relative_path}' is not a text file. {detect_file_type_and_size(relative_path)}."

            number_lines_to_read = min(max(1, number_lines_to_read), MAX_LINES_TO_READ)
            with open(path, 'r', encoding='utf-8') as file:
                sliced_lines = islice(file, line_offset, line_offset + number_lines_to_read)
                return "".join(sliced_lines)
        except Exception as e:
            return f"Error reading file content: {str(e)}"
    
    def detect_file_type_and_size(relative_path: str) -> str:
        """Detect the type and size of a file within the project source using magika given its relative path to the root directory."""
        print("📞 Function called: detect_file_type_and_size with path: ", relative_path)
        try:
            path = _validate_path(relative_path)
            if not path.exists():
                return f"File '{relative_path}' does not exist"
            if not path.is_file():
                return f"Path '{relative_path}' is not a file"
            
            magika = Magika()
            result = magika.identify_path(path)
            
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
    
    return [list_directory_contents, read_file_content, detect_file_type_and_size]
