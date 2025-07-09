"""
Structured logging in CCL (Categorical Configuration Language) format for Vibenix.

Based on: https://chshersh.com/blog/2025-01-06-the-most-elegant-configuration-language.html
"""

import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, TextIO
from dataclasses import dataclass, field
from contextlib import contextmanager
from jinja2 import Environment
import functools

from .errors import NixBuildErrorDiff, NixBuildResult


def get_model_pricing(model: str) -> Optional[tuple[float, float]]:
    """Get model pricing per token from litellm."""
    try:
        import litellm
        if model in litellm.model_cost:
            pricing = litellm.model_cost[model]
            input_cost = pricing.get('input_cost_per_token', 0.0)
            output_cost = pricing.get('output_cost_per_token', 0.0)
            if input_cost > 0 or output_cost > 0:
                return (input_cost, output_cost)
    except Exception:
        pass
    return None


@dataclass
class CCLLogger:
    """
       Logs events in CCL format.
       See: https://chshersh.com/blog/2025-01-06-the-most-elegant-configuration-language.html
    """
    
    log_file: Path
    print_to_console: bool = True
    _file_handle: TextIO = field(init=False)
    _current_indent: int = field(default=0, init=False)
    _start_time: float = field(init=False)
    _jinja_env: Environment = field(init=False)
    
    def __post_init__(self):
        self._file_handle = open(self.log_file, 'w', buffering=1)
        self._start_time = time.time()
        self._setup_jinja_env()
        self._write_header()
    
    def _setup_jinja_env(self):
        """Set up Jinja2 environment for string templates."""
        self._jinja_env = Environment(
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=True,
        )
    
    def _write_header(self):
        """Write header with metadata."""
        # TODO: add things like model version and a commit hash
        self._write("start_time = " + datetime.now().isoformat(), 0)
        self._write("")
    
    def _elapsed_time(self) -> str:
        """Get elapsed time since start formatted as hh:mm:ss.mmm."""
        elapsed = time.time() - self._start_time
        hours = int(elapsed // 3600)
        minutes = int((elapsed % 3600) // 60)
        seconds = elapsed % 60
        return f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"
   
    def _format_value(self, value: Any) -> str:
        """Format a value for Jinja2 template, handling multi-line strings.
        
        For multi-line values, returns the value with a newline prefix
        and proper indentation for each line.
        """
        value_str = str(value)
        if '\n' in value_str:
            lines = value_str.splitlines()
            # Return newline, then each line indented by 8 spaces
            return '\n' + '\n'.join('        ' + line for line in lines)
        else:
            return value_str
    
    def close(self):
        """Close the log file."""
        self._file_handle.close()
    
    def _write(self, line: str, indent_level=None):
        """Write a line with current indentation."""
        if not indent_level:
            indent_level = self._current_indent
        self._file_handle.write("  " * indent_level + line + "\n")
        self._file_handle.flush()  # Ensure immediate write to disk

    @contextmanager
    def _section_begin(self, section_head, indent_level):
        """Context manager for writing indented sections."""
        self._write(section_head, indent_level)
        self._current_indent = indent_level + 1
        yield
        self._current_indent = indent_level

    def _section_content(self, indent_level):
        """Context manager for writing indented sections."""
        prev_indent = self._current_indent
        self._current_indent = indent_level + 1
        yield
        self._current_indent = prev_indent + 1

    def log_model_config(self, model: str):
        """Log model configuration including pricing if available."""
        with self._section_begin("model-config =", 0):
            self._write("model = " + model)
            pricing = get_model_pricing(model)
            if pricing:
                input_cost, output_cost = pricing
                self._write(f"input_cost_per_token = {input_cost:.10f}")
                self._write(f"output_cost_per_token = {output_cost:.10f}")
    
    def log_session_start(self, project_url: str):
        """Log the start of a packaging session."""
        with self._section_begin("session-start =", 0):
            self._write("elapsed = " + self._elapsed_time())
            self._write("project_url = " + project_url)
    
    def log_session_end(self, success: bool, total_iterations: int, total_cost: float = None):
        """Log the end of a packaging session."""
        with self._section_begin("session-end =", 0):
            self._write("elapsed = " + self._elapsed_time())
            self._write("success = " + ("true" if success else "false"))
            self._write("total_iterations = " + str(total_iterations))
            if total_cost is not None:
                self._write(f"total_cost = {total_cost:.6f}")
    
    def log_template_selected(self, template: str):
        """Log template selection."""
        with self._section_begin("template-selected =", 0):
            self._write("event = selected")
            self._write("elapsed = " + self._elapsed_time())
            self._write("name = " + template)

    def log_initial_build(self, result: NixBuildResult):
        """Log the initial build result."""
        with self._section_begin("initial-build =", 0):
            if result.success:
                self._write("result = success")
            elif result.error:
                self._write("result = " + result.error.type.value)
            self._write("elapsed = " + self._elapsed_time())

    def log_before_iterations(self):
        """Log template selection."""
        self._write("iteration =", 0)
    
    def log_iteration_start(self, iteration: int):
        """Log the start of a build iteration."""
        self._write(f"= {iteration} =", 1)

    def log_iteration_end(self, iteration: int, output : NixBuildResult):
        """Log the end of a build iteration."""
        if output.success:
            self._write("type = success", 2)
        elif output.error:
            self._write("type = " + output.error.type.value, 2)
        self._write("elapsed = " + self._elapsed_time(), 2)

    def log_progress_eval(self, iteration: int, diff : NixBuildErrorDiff):
        """Log progress evaluation."""
        with self._section_begin("progress_eval =", 2):
            self._write("result = " + diff.value)
            self._write("elapsed = " + self._elapsed_time())
    
    
    
    def log_model_response(self, input_tokens: int, output_tokens: int, 
                          cost: Optional[float] = None, response_type: str = "model_response"):
        """Log a model response with token usage and cost."""
        with self._section_begin(f"{response_type} =", 2):
            self._write("elapsed = " + self._elapsed_time())
            self._write("input_tokens = " + str(input_tokens))
            self._write("output_tokens = " + str(output_tokens))
            self._write("total_tokens = " + str(input_tokens + output_tokens))
            if cost is not None:
                self._write(f"cost = {cost:.6f}")
    
    def log_iteration_cost(self, iteration: int, iteration_cost: float, 
                          input_tokens: int, output_tokens: int):
        """Log the total cost for an iteration."""
        with self._section_begin("iteration_cost =", 2):
            self._write("elapsed = " + self._elapsed_time())
            self._write("iteration = " + str(iteration))
            self._write("input_tokens = " + str(input_tokens))
            self._write("output_tokens = " + str(output_tokens))
            self._write("total_tokens = " + str(input_tokens + output_tokens))
            self._write(f"cost = {iteration_cost:.6f}")
    
    def log_error(self, error_type: str, message: str, context: Optional[Dict[str, Any]] = None):
        """Log an error with context."""
        with self._section_begin("error =", 0):
            self._write("elapsed = " + self._elapsed_time())
            self._write("type = " + error_type)
            self._write("message = " + message)
            if context:
                with self._section_begin("context =", 1):
                    for key, value in context.items():
                        self._write(f"{key} = {value}")
    
    def function_begin(self, function_name: str, indent_level: int, **kwargs):
        """Log the beginning of a function call."""
        template_str = textwrap.indent(textwrap.dedent("""\
          function_call =
            start_at = {{ elapsed_time }}
            name = {{ function_name }}
            args =
              {% for key, value in args.items() %}
              {{ key }} = {{ format_value(value) }}
              {% endfor %}"""), "  " * indent_level)
        
        # Register the format_value function as a Jinja2 function
        template = self._jinja_env.from_string(template_str)
        output = template.render(
            elapsed_time=self._elapsed_time(),
            function_name=function_name,
            args=kwargs,
            format_value=self._format_value,
            spaces=indent_level * 2
        )

        self._file_handle.write(output)
        if self.print_to_console:
            print(output)
    
    def function_end(self, function_name: str, result: Any, indent_level: int):
        """Log the end of a function call with result."""
        template_str = textwrap.indent(textwrap.dedent("""\
          result = {{ format_value(result) }}
          end_at = {{ elapsed_time }}"""), "  " * (indent_level + 1))
        
        template = self._jinja_env.from_string(template_str)
        output = template.render(
            result=result,
            elapsed_time=self._elapsed_time(),
            format_value=self._format_value
        )
        
        self._file_handle.write(output)
        if self.print_to_console:
            print(output)

# Global logger instance
_logger: CCLLogger = None


def init_logger(log_file: Path, print_to_console: bool = True) -> CCLLogger:
    """Initialize the global CCL logger."""
    global _logger
    _logger = CCLLogger(log_file=log_file, print_to_console=print_to_console)
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


def log_function_call(function_name: str, indent_level: int = 2):
    """Decorator to log function calls with their arguments and results."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # Log function beginning
            logger = get_logger()
            logger.function_begin(function_name, indent_level, **kwargs)
            
            # Execute function
            result = func(*args, **kwargs)
            
            # Log function end
            logger.function_end(function_name, result, indent_level)
            
            return result
        return wrapper
    return decorator
