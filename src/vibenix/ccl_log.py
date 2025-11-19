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

from .errors import NixBuildErrorDiff, NixBuildResult

def enum_str(enum: Enum):
    return f"{type(enum).__name__}.{enum.name}"


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
        if log_end:
            self.write_time("end_at")
        attr_str = self._current_attr_path.pop()
        attr_str + ""

    def enter_list(self):
        self._write(self._indent() + "= 0 =\n")
        self._current_attr_path.append(0)

    def next_list_item(self):
        num = self._current_attr_path.pop()
        self._write(self._indent() + f"= {num + 1} =\n")
        self._current_attr_path.append(num + 1)

    def leave_list(self):
        num = self._current_attr_path.pop()
        num + 1

    def write_kv(self, key: str, value: str):
        # Handle None values by writing just "key ="
        if value is None:
            self._write(self._indent() + key + " =\n")
            return
            
        value_str = str(value).strip()
        is_multiline = '\n' in value_str
        curr_key_path = "@" + "/".join(str(x) for x in self._current_attr_path + [ key ])
        prev_key_path = None
        # reference multi-line values by previous path if possible
        if is_multiline:
            hash = hashlib.sha256(value_str.encode('utf-8')).digest()
            prev_key_path = self._dedup_dict.get(hash)
            # prefer shorter paths
            if prev_key_path and (prev_key_path.count('/') > curr_key_path.count('/')):
                self._dedup_dict[hash] = curr_key_path
        if prev_key_path:
            self._write(self._indent() + key + " = " + prev_key_path + "\n")
        elif  not is_multiline:
            self._write(self._indent() + key + " = " + value_str + "\n")
        else:
            self._write(self._indent() + key + " = " + self._format_multiline_value(value_str) + "\n")
            self._dedup_dict[hash] = curr_key_path

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
   
    def _format_multiline_value(self, value_str: Any) -> str:
        """Format a value for Jinja2 template, handling multi-line strings.
        
        For multi-line values, returns the value with a newline prefix
        and proper indentation for each line.
        """
        lines = value_str.splitlines()
        line_start = ('\n' + self._indent() + "  ")
        return line_start + line_start.join(line for line in lines)
    
    def close(self):
        """Close the log file."""
        self._file_handle.close()
    
    def _write(self, string: str, indent=0):
        self._file_handle.write(string)
        self._file_handle.flush()

    def log_model_config(self, model_config: dict):
        """Log model configuration including pricing if available."""
        self.enter_attribute("model_config")

        from vibenix.model_config import get_model_name
        full_model = get_model_name()
        self.write_kv("full_model", full_model)

        self.enter_attribute("model_settings")
        model_settings = model_config.get("model_settings", {})
        for k, v in model_settings.items():
            self.write_kv(k, str(v))
        self.leave_attribute()

        # genai-prices doesnt provide direct way to get actual pricing
        from vibenix.model_config import calc_model_pricing
        input_cost = calc_model_pricing(full_model, 1, 0, 0)
        output_cost = calc_model_pricing(full_model, 0, 1, 0)
        cache_read_cost = calc_model_pricing(full_model, 1, 0, 1)
        self.write_kv("input_cost_per_token", f"{input_cost:.15f}".rstrip('0'))
        self.write_kv("output_cost_per_token", f"{output_cost:.15f}".rstrip('0'))
        self.write_kv("cache_read_cost_per_token", f"{cache_read_cost:.15f}".rstrip('0'))

        self.leave_attribute()


    def log_vibenix_settings(self):
        """Log Vibenix settings using the global logger."""
        import json
        from vibenix.defaults import get_settings_manager, settings_to_json_format
        compact = json.dumps(settings_to_json_format(get_settings_manager().get_settings()), separators=(',', ':'))
        self.write_kv("vibenix_settings", compact)
    
    
    def log_session_end(self, signal: str = None, total_cost: float = None):
        """Log the end of a packaging session.
        
        Args:
            signal: None for orderly termination, or the signal name (e.g., "SIGTERM", "SIGINT") if terminated by signal
            total_cost: Optional total cost of the session
        """
        # Reset indentation stack to ensure we're at level 0
        self._current_attr_path = []
        
        self.enter_attribute("session_end")
        self.write_time("elapsed")
        self.write_kv("signal", signal)
        if total_cost is not None:
            self.write_kv("total_cost", f"{total_cost:.6f}")
        self.leave_attribute()
    
    def log_exception(self, exception_str: str):
        """Log an exception that terminated the session.
        
        Args:
            exception_str: String representation of the exception
        """
        # Reset indentation stack to ensure we're at level 0
        self._current_attr_path = []
        
        self.write_kv("exception", exception_str)
    
    def log_template_selected_begin(self, indent_level: int = 0):
        """Log template selection."""
        self.enter_attribute("select_template")


    def log_template_selected_end(self, template_type: str, template_content: str, notes: str | None, indent_level: int = 0):
        """Log template selection."""
        self.write_kv("template_type", enum_str(template_type))
        self.write_kv("template", template_content)
        self.write_kv("notes", notes if notes else "")
        self.leave_attribute()

    def log_initial_build(self, code: str, result: NixBuildResult, indent_level: int = 0):
        """Log the initial build result."""
        self.enter_attribute("initial")
        self.write_kv("code", code)
        self.write_kv("error", result.error.truncated() if not result.success else "")
        self.leave_attribute()
    
    def log_iteration_start(self, iteration: int, error_type=None):
        """Log the start of a build iteration."""
        self.enter_list() if iteration == 0 else self.next_list_item()
        if error_type:
            self.write_kv("type", enum_str(error_type))

    def log_progress_eval_start(self):
        """Start logging progress evaluation."""
        self.enter_attribute("evaluate_progress")
    
    def log_progress_eval_end(self, diff : NixBuildErrorDiff):
        """End logging progress evaluation with result."""
        self.write_kv("progress", enum_str(diff))
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
                           input_tokens: int, output_tokens: int, cache_read_tokens: int = 0):
        """Log the total cost for an iteration."""
        self.enter_attribute("iteration_cost")
        self.write_time("elapsed")
        self.write_kv("iteration", str(iteration))
        self.write_kv("input_tokens", str(input_tokens))
        self.write_kv("output_tokens", str(output_tokens))
        self.write_kv("cache_read_tokens", str(cache_read_tokens))
        self.write_kv("total_tokens", str(input_tokens + output_tokens))
        self.write_kv("cost", f"{iteration_cost:.6f}")
        self.leave_attribute()

    def log_refinement_cost(self, packaging_cost: float, refinement_cost: float, 
                           input_tokens: int, output_tokens: int, cache_read_tokens: int = 0):
        """Log the total cost for the refinement loop."""
        self.enter_attribute("refinement_cost")
        self.write_kv("input_tokens", str(input_tokens))
        self.write_kv("output_tokens", str(output_tokens))
        self.write_kv("cache_read_tokens", str(cache_read_tokens))
        self.write_kv("total_tokens", str(input_tokens + output_tokens))
        self.write_kv("cost", f"{refinement_cost:.6f}")
        self.write_kv("packaging_cost", f"{packaging_cost:.6f}")
        self.leave_attribute()

    def log_total_tool_cost(self):
        """Log the total cost for each tool call."""
        self.enter_attribute("tool_cost")
        from vibenix.ui.conversation_templated import get_model_prompt_manager
        session_tool_usage = get_model_prompt_manager().get_session_tool_usage()
        for tool_name, usage in session_tool_usage.items():
            self.enter_attribute(tool_name)
            self.write_kv("input_tokens", str(usage.prompt_tokens))
            self.leave_attribute()
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
        if num == 0:
            self.enter_list()
        else:
            self.next_list_item()
        self.write_kv("text", content)
    
    def reply_chunk_function_call(self, num: int, indent_level: int):
        """Log one response chunk."""
        if num == 0:
            self.enter_list()
        else:
            self.next_list_item()

    def reply_chunk_typed(self, num: int, content: object, typed: str, indent_level: int):
        """Log one response chunk."""
        def handle_typed(content: object, typed: str) -> str:
            match typed:
                case "enum":
                    return enum_str(content)
                case _:
                    return str(content)

        if num == 0:
            self.enter_list()
        else:
            self.next_list_item()
        self.write_kv(typed, handle_typed(content, typed))

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


def log_function_call(function_name: str, indent_level: int = 2, do_print: bool = True):
    """Decorator to log function calls with their arguments and results."""
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            logger = get_logger()
            logger._function_begin(function_name, indent_level, **kwargs)
            
            result = func(*args, **kwargs)
            if result != None and do_print:
                print(result)
            
            logger._function_end(function_name, result, indent_level)
            
            return result
        return wrapper
    return decorator
