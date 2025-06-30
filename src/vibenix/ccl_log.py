"""
Structured logging in CCL (Categorical Configuration Language) format for Vibenix.

Based on: https://chshersh.com/blog/2025-01-06-the-most-elegant-configuration-language.html
Implements: https://github.com/mschwaig/vibenix/issues/30
"""

import hashlib
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TextIO
from dataclasses import dataclass, field
from contextlib import contextmanager

from .errors import NixBuildResult


@dataclass
class CCLLogger:
    """Logs events in CCL format with content addressing."""
    
    log_file: Path
    _file_handle: TextIO = field(init=False)
    _content_hashes: Dict[str, str] = field(default_factory=dict, init=False)
    _current_indent: int = field(default=0, init=False)
    _start_time: float = field(init=False)
    
    def __post_init__(self):
        self._file_handle = open(self.log_file, 'w', buffering=1)
        self._start_time = time.time()
        self._write_header()
    
    def _write_header(self):
        """Write header with metadata."""
        self._write("vibenix_version = 0.1.0")
        self._write("log_format = ccl")
        self._write("start_time = " + datetime.now().isoformat())
        self._write("")
    
    def _elapsed_time(self) -> str:
        """Get elapsed time since start formatted as hh:mm:ss.mmm."""
        elapsed = time.time() - self._start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = elapsed % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"
    
    def close(self):
        """Close the log file."""
        self._file_handle.close()
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    def _hash_content(self, content: str) -> str:
        """Generate a content hash for deduplication."""
        return hashlib.sha256(content.encode()).hexdigest()[:12]
    
    def _store_content(self, content: str) -> str:
        """Store content and return its hash reference."""
        if len(content) > 100:  # Only hash longer content
            content_hash = self._hash_content(content)
            if content_hash not in self._content_hashes:
                self._content_hashes[content_hash] = content
                # Write the content definition
                with self._section("content"):
                    self._write(f"hash = {content_hash}")
                    self._write("value =")
                    # Write multiline content with extra indentation
                    for line in content.split('\n'):
                        self._write("  " + line)
            return f"@{content_hash}"
        return content
    
    def _write(self, line: str):
        """Write a line with current indentation."""
        indent = "  " * self._current_indent
        self._file_handle.write(indent + line + "\n")
    
    @contextmanager
    def _section(self, name: str):
        """Context manager for writing indented sections."""
        self._write(f"{name} =")
        self._current_indent += 1
        yield
        self._current_indent -= 1
        if self._current_indent == 0:
            self._write("")  # Empty line after top-level sections
    
    def log_session_start(self, project_url: str, issue_number: Optional[str] = None):
        """Log the start of a packaging session."""
        with self._section("session"):
            self._write("event = start")
            self._write("elapsed = " + self._elapsed_time())
            self._write("project_url = " + project_url)
            if issue_number:
                self._write("issue_number = " + issue_number)
    
    def log_session_end(self, success: bool, total_iterations: int):
        """Log the end of a packaging session."""
        with self._section("session"):
            self._write("event = end")
            self._write("elapsed = " + self._elapsed_time())
            self._write("success = " + ("true" if success else "false"))
            self._write("total_iterations = " + str(total_iterations))
    
    def log_template_selected(self, template: str):
        """Log template selection."""
        with self._section("template"):
            self._write("event = selected")
            self._write("elapsed = " + self._elapsed_time())
            self._write("name = " + template)
    
    def log_build_iteration(self, iteration: int):
        """Log the start of a build iteration."""
        with self._section("build_iteration"):
            self._write("number = " + str(iteration))
            self._write("elapsed = " + self._elapsed_time())
    
    def log_eval_iteration(self, iteration: int, attempt: int):
        """Log the start of an eval fix iteration."""
        with self._section("eval_iteration"):
            self._write("build_iteration = " + str(iteration))
            self._write("attempt = " + str(attempt))
            self._write("elapsed = " + self._elapsed_time())
    
    def log_build_attempt(self, iteration: int, attempt: int, code: Optional[str] = None):
        """Log a build attempt."""
        with self._section("build_attempt"):
            self._write("iteration = " + str(iteration))
            self._write("attempt = " + str(attempt))
            self._write("elapsed = " + self._elapsed_time())
            if code:
                self._write("code = " + self._store_content(code))
    
    def log_build_result(self, 
                        iteration: int, 
                        attempt: int,
                        result: NixBuildResult):
        """Log a build result."""
        with self._section("build_result"):
            self._write("iteration = " + str(iteration))
            self._write("attempt = " + str(attempt))
            self._write("elapsed = " + self._elapsed_time())
            self._write("success = " + ("true" if result.success else "false"))
            
            if not result.success and result.error:
                self._write("error_kind = " + result.error.type.value)
                self._write("error_message = " + self._store_content(result.error.error_message))
    
    def log_function_call(self, function_name: str, **kwargs):
        """Log a function call to the model."""
        with self._section("function_call"):
            self._write("elapsed = " + self._elapsed_time())
            self._write("name = " + function_name)
            for key, value in kwargs.items():
                if isinstance(value, str) and len(value) > 100:
                    self._write(f"{key} = " + self._store_content(value))
                else:
                    self._write(f"{key} = {value}")
    
    def log_model_interaction(self, 
                            prompt: Optional[str] = None,
                            response: Optional[str] = None,
                            model: Optional[str] = None,
                            prompt_tokens: Optional[int] = None,
                            response_tokens: Optional[int] = None):
        """Log a model interaction."""
        with self._section("model"):
            self._write("timestamp = " + datetime.now().isoformat())
            if model:
                self._write("name = " + model)
            if prompt:
                self._write("prompt = " + self._store_content(prompt))
            if response:
                self._write("response = " + self._store_content(response))
            if prompt_tokens:
                self._write("prompt_tokens = " + str(prompt_tokens))
            if response_tokens:
                self._write("response_tokens = " + str(response_tokens))
    
    def log_error(self, error_type: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log an error with context."""
        with self._section("error"):
            self._write("timestamp = " + datetime.now().isoformat())
            self._write("type = " + error_type)
            self._write("message = " + self._store_content(message))
            if context:
                with self._section("context"):
                    for key, value in context.items():
                        self._write(f"{key} = {value}")


# Global logger instance
_logger: CCLLogger = None


def init_logger(log_file: Path) -> CCLLogger:
    """Initialize the global CCL logger."""
    global _logger
    _logger = CCLLogger(log_file=log_file)
    return _logger


def get_logger() -> CCLLogger:
    """Get the global CCL logger instance."""
    if _logger is None:
        raise RuntimeError("CCL logger not initialized. Call init_logger() first.")
    return _logger


def close_logger():
    """Close the global logger."""
    global _logger
    if _logger:
        _logger.close()
        _logger = None