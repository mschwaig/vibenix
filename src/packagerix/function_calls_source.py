from typing import List, Callable, Any
from pathlib import Path
import subprocess

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
    def read_file_content(relative_path: str) -> str:
        """Read the content of a file within the project source given its relative path to the root directory."""
        print("📞 Function called: read_file_content with path: ", relative_path)
        try:
            path = _validate_path(relative_path)
            with open(path, 'r', encoding='utf-8') as file:
                return file.read()
        except Exception as e:
            return f"Error reading file content: {str(e)}"
    return [list_directory_contents, read_file_content]
