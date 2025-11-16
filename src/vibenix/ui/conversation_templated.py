"""Template-based model conversation helpers for vibenix.

This module provides utilities for loading prompt templates and asking models questions.
"""

import os
import inspect
from pathlib import Path
from enum import Enum
from typing import TypeVar, Union, get_origin, get_args, Any, Optional, List, Callable, get_type_hints
from functools import wraps
from vibenix.ccl_log import get_logger
from vibenix.ui.conversation import (
    Usage,
    Message,
    Actor,
    get_ui_adapter
)
from vibenix.defaults import get_settings_manager
from pydantic_ai.usage import RunUsage

from vibenix.packaging_flow.model_prompts.prompt_loader import get_prompt_loader
from vibenix.agent import VibenixAgent
from abc import ABC

T = TypeVar('T')

class ModelPromptManager:
    """Manages model prompt interactions and tracks usage across a session."""
    ### Cost tracking
    def __init__(self, model: str):
        self._session_usage = Usage(model=model)
        self._iteration_usage = Usage(model=model)
        self._session_tool_usage = {} # total across retries
        self._previous_total_usage = Usage(model=model) # total usage until X attempt
        self._model = model
    
    def get_session_cost(self):
        """Get the accumulated cost for this session."""
        self.reset_iteration_usage()
        return self._session_usage.calculate_cost()

    def get_session_usage(self) -> Usage:
        """Get the accumulated usage for this session."""
        self.reset_iteration_usage()
        from copy import deepcopy
        return deepcopy(self._session_usage)

    def get_iteration_usage(self) -> Usage:
        """Get usage for the last model interactions."""
        usage = self._iteration_usage
        self.reset_iteration_usage()
        return usage

    def add_iteration_usage(self, usage: Usage):
        """Add usage from a model interaction to the iteration usage."""
        self._iteration_usage.prompt_tokens += usage.prompt_tokens
        self._iteration_usage.completion_tokens += usage.completion_tokens
        self._iteration_usage.cache_read_tokens += usage.cache_read_tokens

    def reset_iteration_usage(self):
        """Reset the iteration usage counters."""
        self._session_usage.prompt_tokens += self._iteration_usage.prompt_tokens
        self._session_usage.completion_tokens += self._iteration_usage.completion_tokens
        self._session_usage.cache_read_tokens += self._iteration_usage.cache_read_tokens
        self._iteration_usage = Usage(model=self._model)

    def get_session_tool_usage(self) -> dict:
        """Get usage per tool call."""
        from copy import copy
        return copy(self._session_tool_usage)

    def add_session_tool_usage(self, tool_name: str, completion: int=0, prompt: int=0, cache_read: int=0):
        """Add usage from a tool call to the tool call usage."""
        if tool_name not in self._session_tool_usage:
            self._session_tool_usage[tool_name] = Usage(model=self._model)
        self._session_tool_usage[tool_name].completion_tokens += completion
        self._session_tool_usage[tool_name].prompt_tokens += prompt
        self._session_tool_usage[tool_name].cache_read_tokens += cache_read

    def set_previous_total_usage(self, completion: int=0, prompt: int=0, cache_read: int=0):
        """Set the previous step total usage counters."""
        self._previous_total_usage.completion_tokens = completion
        self._previous_total_usage.prompt_tokens = prompt
        self._previous_total_usage.cache_read_tokens = cache_read

    def get_previous_total_usage(self) -> Usage:
        """Get usage for the previous step (total usage up to that point)."""
        from copy import deepcopy
        usage = deepcopy(self._previous_total_usage)
        return usage

    def reset_previous_total_usage(self):
        """Reset the previous step total usage counters."""
        self._previous_total_usage = Usage(model=self._model)
    #####

    def ask_model_prompt(self, template_path: str):
        """Decorator for model interactions using prompt templates.
    
        This decorator uses pydantic-ai to interact with the model.
        
        Args:
            template_path: Path to template file relative to model_prompts directory
            functions: Optional list of functions to make available to the model
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            # Get return type hint
            type_hints = get_type_hints(func)
            return_type = type_hints.get('return', type(None))

            prompt_key = template_path.split('/')[-1].replace('.md', '')
            if get_settings_manager().is_edit_tools_prompt(prompt_key) and get_settings_manager().get_setting_enabled("edit_tools"):
                return_type = type(None)

            # Determine output type and whether to use streaming
            is_streaming = return_type == str
            is_enum = inspect.isclass(return_type) and issubclass(return_type, Enum)
            is_structured = return_type not in [str, type(None)]
            
            @wraps(func)
            def wrapper(*args, **kwargs) -> T:
                adapter = get_ui_adapter()
                
                # Get function signature to map args to param names
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                
                # Filter out None values and empty lists from arguments
                template_context = {
                    k: v for k, v in bound_args.arguments.items() 
                    if v is not None and v != []
                }
                
                # Special handling for additional_functions parameter
                prompt_key = template_path.split('/')[-1].replace('.md', '')
                try:
                    additional_functions = get_settings_manager().get_prompt_additional_tools(prompt_key)
                except Exception as e:
                    raise ValueError(f"Failed to get additional functions for prompt '{prompt_key}', error: {e}")
                tool_call_collector = template_context.pop('tool_call_collector', None) # TODO

                # Filter out disabled additional functions TODO theres a method for this already
                disabled_tools = get_settings_manager().get_disabled_tools()
                additional_functions = [func for func in additional_functions if func.__name__ not in disabled_tools]
                
                get_logger().prompt_begin(func.__name__, template_path, 2, template_context)
                
                # Load and render the template
                prompt_loader = get_prompt_loader()
                rendered_prompt = prompt_loader.load(template_path, **template_context)

                # Add additional snippets TODO move to prompt itself
                if get_settings_manager().is_edit_tools_prompt(prompt_key):
                    #if get_settings_manager().get_setting_enabled("2_agents"):
                    #    edit_tools_snippet = get_settings_manager().get_snippet(snippet="feedback")
                    #else:
                    edit_tools_snippet = get_settings_manager().get_snippet(prompt_key)
                    rendered_prompt += "\n\n" + edit_tools_snippet
                
                # Show coordinator message (first line or whole prompt if short)
                first_line = f"({template_path}): " + rendered_prompt.split('\n')[0]
                coordinator_msg = f"@model {first_line}" if len(rendered_prompt) > 100 else f"@model {rendered_prompt}"
                adapter.show_message(Message(Actor.COORDINATOR, coordinator_msg))
                
                # Create agent with appropriate output type
                if is_structured:
                    # For structured outputs (enums, lists, etc.), use output_type
                    agent = VibenixAgent(output_type=return_type)
                else:
                    # For strings, we don't need structured output
                    agent = VibenixAgent()
                
                functions = get_settings_manager().get_prompt_tools(prompt_key)
                # Add tools to the agent
                all_functions = (functions or []) + additional_functions
                for tool_func in all_functions:
                    agent.add_tool(tool_func)
                
                try:
                    # Run the agent
                    if is_streaming:
                        # For string returns, use streaming
                        response, usage = agent.run_stream(rendered_prompt)
                        result = response
                        get_logger().reply_chunk_text(0, result, 4)
                    else:
                        # For non-streaming (structured outputs), just run normally
                        response, usage = agent.run(rendered_prompt)
                        
                        # Pydantic-ai should return the structured type directly
                        result = response
                        adapter.show_message(Message(Actor.MODEL, str(result)))
                        if is_structured:
                            if is_enum:
                                get_logger().reply_chunk_typed(0, result, 'enum', 4)
                            else:
                                get_logger().reply_chunk_typed(0, result, str(type(result)), 4)
                        else:
                            get_logger().reply_chunk_text(0, result, 4)
                    
                    get_logger().prompt_end(2)
                    # Track usage for cost calculations
                    self.add_iteration_usage(usage)

                    self.reset_previous_total_usage()
                    # If not using edit_tools, need to extract code and updated flake
                    from vibenix.tools import EDIT_FUNCTIONS
                    if get_settings_manager().is_edit_tools_prompt(prompt_key) and not (get_settings_manager().get_setting_enabled("edit_tools") or any(func in get_settings_manager().get_prompt_tools(prompt_key) for func in EDIT_FUNCTIONS)):
                        from vibenix.parsing import extract_updated_code
                        from vibenix.flake import update_flake
                        try:
                            code = extract_updated_code(result)
                            update_flake(code)
                        except ValueError as e:
                            print("Failed to extract updated code from model response.")
                            pass

                    return result
                    
                except Exception as e:
                    adapter.show_message(Message(Actor.MODEL, f"Error: {str(e)}"))
                    get_logger().reply_chunk_text(0, f"Error: {str(e)}", 4)
                    get_logger().prompt_end(2)

                    # Proceed to next iteration, if problem persists, the loop will stop anyway
                    # If we NEED a result, then no can do. Re-raise
                    if return_type != type(None):
                        raise e
                    return None
            
            return wrapper
        return decorator

from vibenix.model_config import get_model_name
model_prompt_manager = ModelPromptManager(get_model_name())

def get_model_prompt_manager() -> ModelPromptManager:
    """Get the global model prompt manager instance."""
    return model_prompt_manager
