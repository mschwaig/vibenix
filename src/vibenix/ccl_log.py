"""
Structured logging in CCL (Categorical Configuration Language) format for Vibenix.

Based on: https://chshersh.com/blog/2025-01-06-the-most-elegant-configuration-language.html
"""

from enum import Enum
import hashlib
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, TextIO
from dataclasses import dataclass, field
from contextlib import contextmanager
import functools
from magentic import FunctionCall

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
    _start_time: float = field(init=False)
    _dedup_dict : Dict[bytes,str] = field(init=False)
    _current_attr_path: List[str|int] = field(init=False)

    def _indent(self):
        return "  " * len(self._current_attr_path)
    
    def __post_init__(self):
        self._file_handle = open(self.log_file, 'w', buffering=1)
        self._start_time = time.time()
        self._dedup_dict = {}
        self._current_attr_path = []
        self.write_kv("start_time", datetime.now().isoformat())
    
    def enter_attribute(self, name: str, log_start=False):
        self._write(self._indent() + name + " =\n")
        self._current_attr_path.append(name)
        if log_start:
            self.write_time("start_at")

    def leave_attribute(self, log_end=False):
        attr_str = self._current_attr_path.pop()
        if log_end:
            self.write_time("end_at")
        attr_str + ""

    def enter_list(self):
        self._write(self._indent() + "= 1 =\n")
        self._current_attr_path.append(1)

    def next_list_item(self):
        num = self._current_attr_path.pop()
        self._write(self._indent() + f"= {num + 1} =\n")
        self._current_attr_path.append(num + 1)

    def leave_list(self):
        num = self._current_attr_path.pop()
        num + 1

    def write_kv(self, key: str, value: str):
        value_str = str(value).strip()
        hash = hashlib.sha256(value_str.encode('utf-8')).digest()
        prev_key_path = self._dedup_dict.get(hash)
        if prev_key_path:
            self._write(self._indent() + key + " = " + prev_key_path + "\n")
        else:
            self._write(self._indent() + key + " = " + self._format_value(value_str) + "\n")
            self._dedup_dict[hash] = "@" + "/".join(str(x) for x in self._current_attr_path + [ key ])

    def write_dict(self, name: str, dict: Dict[str,str]):
        self.enter_attribute(name)
        for k, v in dict.items():
            self.write_kv(k, v)
        self.leave_attribute()

    def write_time(self, key):
        self.write_kv(key, self._elapsed_time())

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
            line_start = ('\n' + self._indent() + "  ")
            return line_start + line_start.join(line for line in lines)
        else:
            return value_str
    
    def close(self):
        """Close the log file."""
        self._file_handle.close()
    
    def _write(self, string: str, indent=0):
        self._file_handle.write(string)
        self._file_handle.flush()

    def log_model_config(self, model: str):
        """Log model configuration including pricing if available."""
        self.enter_attribute("model_config")
        self.write_kv("model", model)
        pricing = get_model_pricing(model)
        if pricing:
            input_cost, output_cost = pricing
            self.write_kv(f"input_cost_per_token = {input_cost:.6f}")
            self.write_kv(f"output_cost_per_token = {output_cost:.6f}")
        self.leave_attribute()

    def log_session_end(self, success: bool, total_iterations: int, total_cost: float = None):
        """Log the end of a packaging session."""
        self.enter_attribute("session_end")
        self.write_time("elapsed")
        self.write_kv("success", "true" if success else "false")
        self.write_kv("total_iterations", str(total_iterations))
        if total_cost is not None:
            self.write_kv("total_cost", f"{total_cost:.6f}")
        self.leave_attribute()
    
    def log_template_selected_begin(self, indent_level: int = 0):
        """Log template selection."""
        self.enter_attribute("select_template")


    def log_template_selected_end(self, template_type: str, template_content: str, notes: str | None, indent_level: int = 0):
        """Log template selection."""
        self.write_kv("template_type", template_type)
        self.write_kv("template", template_content)
        self.write_kv("notes", notes if notes else "")
        self.leave_attribute()

    def log_initial_build(self, code: str, result: NixBuildResult, indent_level: int = 0):
        """Log the initial build result."""
        self.enter_attribute("initial")
        self.write_kv("code", code)
        self.write_kv("error", result.error.truncated() if not result.success else "")
        self.leave_attribute()
    
    def log_iteration_start(self, iteration: int):
        """Log the start of a build iteration."""
        self.enter_list() if iteration == 1 else self.next_list_item()

    def log_iteration_end(self, iteration: int, output : NixBuildResult):
        """Log the end of a build iteration."""
        if output.success:
            self.write_kv("type", "success")
        elif output.error:
            self.write_kv("type", output.error.type.value)
        self.write_time("elapsed")

    def log_progress_eval(self, iteration: int, diff : NixBuildErrorDiff):
        """Log progress evaluation."""
        self.enter_attribute("progress_eval")
        self.write_kv("result", diff.value)
        self.write_time("elapsed")
        self.leave_attribute()
    
    
    
    def log_model_response(self, input_tokens: int, output_tokens: int, 
                          cost: Optional[float] = None, response_type: str = "model_response"):
        """Log a model response with token usage and cost."""
        self.enter_attribute(response_type)
        self.write_time("elapsed")
        self.write_kv("input_tokens", str(input_tokens))
        self.write_kv("output_tokens", str(output_tokens))
        self.write_kv("total_tokens", str(input_tokens + output_tokens))
        if cost is not None:
            self.write_kv("cost", f"{cost:.6f}")
        self.leave_attribute()
    
    def log_iteration_cost(self, iteration: int, iteration_cost: float, 
                          input_tokens: int, output_tokens: int):
        """Log the total cost for an iteration."""
        self.enter_attribute("iteration_cost")
        self.write_time("elapsed")
        self.write_kv("iteration", str(iteration))
        self.write_kv("input_tokens", str(input_tokens))
        self.write_kv("output_tokens", str(output_tokens))
        self.write_kv("total_tokens", str(input_tokens + output_tokens))
        self.write_kv("cost", f"{iteration_cost:.6f}")
        self.leave_attribute()

    def prompt_begin(self, prompt_name: str, prompt_template: str, indent_level: int, prompt_args : Dict):
        """Log the beginning of a model prompt."""
        self.enter_attribute("model_prompt", log_start=True)
        self.write_kv("name", prompt_name)
        self.write_dict("args", prompt_args)
        self.write_kv("template", prompt_template)
        self.enter_attribute("reply_chunks")

    def reply_chunk_text(self, num: int, content : str, indent_level: int):
        """Log one response chunk."""
        if num == 1:
            self.enter_list()
        else:
            self.next_list_item()
        self.write_kv("text", content)
    
    def reply_chunk_function_call(self, num: int, indent_level: int):
        """Log one response chunk."""
        if num == 1:
            self.enter_list()
        else:
            self.next_list_item()

    def reply_chunk_enum(self, num: int, _type : str, value : str, indent_level: int):
        """Log one response chunk."""
        if num == 1:
            self.enter_list()
        else:
            self.next_list_item()
        self.write_kv("enum", f"{_type}.{value}")

    def prompt_end(self, indent_level: int):
        """Log the end of a model prompt."""
        self.leave_list()  # Close reply_chunks list
        self.leave_attribute()  # Close reply_chunks attribute
        self.leave_attribute(log_end=True)  # Close model_prompt attribute

    def _function_begin(self, function_name: str, indent_level: int, **kwargs):
        """Log the beginning of a function call."""
        self.enter_attribute("function_call", log_start=True)
        self.write_kv("name", function_name)
        self.write_dict("args", kwargs)
    
    def _function_end(self, function_name: str, result: Any, indent_level: int):
        """Log the end of a function call with result."""
        self.write_kv("result", result)
        self.leave_attribute(log_end=True)

    def log_project_summary_begin(self, indent_level: int = 0):
        """Log the summary of the project."""
        self.enter_attribute("summarize_project")

    def log_project_summary_end(self, summary_str: str, indent_level: int = 0):
        """Log the summary of the project."""
        self.write_kv("summary", summary_str)
        self.leave_attribute()


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
            logger = get_logger()
            logger._function_begin(function_name, indent_level, **kwargs)
            
            result = func(*args, **kwargs)
            
            logger._function_end(function_name, result, indent_level)
            
            return result
        return wrapper
    return decorator
