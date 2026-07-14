"""Error types for the vibenix build system."""

from enum import Enum
from pydantic import BaseModel
from typing import Optional

class NixBuildErrorDiff(Enum):
    REGRESS = "REGRESS"
    PROGRESS = "PROGRESS"
    STAGNATION = "STAGNATION"
    BROKEN_LOG_OUTPUT = "BROKEN_LOG_OUTPUT"

class NixErrorKind(Enum):
    EVAL_ERROR = "EVAL_ERROR"
    BUILD_ERROR = "BUILD_ERROR"
    INVALID_HASH = "INVALID_HASH"
    HASH_MISMATCH = "HASH_MISMATCH"
    DEPENDENCY_BUILD_ERROR = "DEPENDENCY_BUILD_ERROR"

class NixError(BaseModel):
    type: NixErrorKind
    error_message: str
    
    def truncated(self, max_lines: int = 256, page: int = None, max_line_length: int = 512) -> str:
        """Return truncated version of error message, keeping the tail end and limiting line width.
        
        Args:
            max_lines: Maximum number of lines to include from the end.
            page: Specific page to fetch (defaults to the last page).
            max_line_length: Maximum character length per line to prevent token exhaustion.
            
        Returns:
            Truncated error message showing the target page with excessively long lines truncated.
        """
        lines = self.error_message.split('\n')
        # Ensure total_pages is at least 1, even if error_message is empty
        total_pages = max(1, (len(lines) + max_lines - 1) // max_lines)
        
        if page is None:
            page = total_pages
        elif page < 1 or page > total_pages:
            raise ValueError(f"Page number {page} is out of range. Must be between 1 and {total_pages}.")
            
        end_index = len(lines) - (total_pages - page) * max_lines - 1
        start_index = max(0, end_index - (max_lines - 1))
        
        truncated_lines = []
        for line in lines[start_index:end_index + 1]:
            if len(line) > max_line_length:
                # Truncate and add a visual indicator for the LLM/user
                truncated_lines.append(line[:max_line_length] + "... [LINE TRUNCATED]")
            else:
                truncated_lines.append(line)

        # Only add the pagination header if there's actually more than one page
        if total_pages > 1:
            truncated = f"(Showing error page {page} out of {total_pages})\n"
        else:
            truncated = ""
            
        truncated += '\n'.join(truncated_lines)
        
        return truncated


class NixBuildResult(BaseModel):
    """Result of a Nix build operation."""
    success: bool
    is_src_attr_only: bool
    error: Optional[NixError] = None


class FullLogDiff(BaseModel):
    """Log comparison showing full logs (when both are under 100 lines)."""
    previous_log: str
    new_log: str
    initial_lines: int
    improvement_lines: int


class ProcessedLogDiff(BaseModel):
    """Log comparison showing truncated logs with divergence analysis."""
    previous_log_truncated: str
    new_log_truncated: str
    initial_lines: int
    improvement_lines: int
    divergence_line: int


LogDiff = FullLogDiff | ProcessedLogDiff
