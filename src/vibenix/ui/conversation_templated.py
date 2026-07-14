"""Template-based model conversation helpers for vibenix.

This module provides utilities for loading prompt templates and asking models questions.
"""

import inspect
from enum import Enum
from typing import TypeVar, Optional, List, Callable, get_type_hints
from vibenix.ccl_log import get_logger
from vibenix.ui.conversation import (
    ModelCodeResponse,
    Usage,
    Message,
    Actor,
    get_ui_adapter
)
from vibenix.defaults import get_settings_manager

from vibenix.packaging_flow.model_prompts.prompt_loader import get_prompt_loader
from vibenix.agent import VibenixAgent

T = TypeVar('T')

class ModelPromptManager:
    """Manages model prompt interactions and tracks usage across a session."""
    ### Cost tracking
    def __init__(self, model: str):
        self._session_usage = Usage(model=model)
        self._iteration_usage = Usage(model=model)
        self._session_tool_usage = {} # total across retries
        self._model = model
        self.current_prompt = None
    
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
            self._session_tool_usage[tool_name] = {"usage": Usage(model=self._model), "count": 0}
        self._session_tool_usage[tool_name]["usage"].completion_tokens += completion
        self._session_tool_usage[tool_name]["usage"].prompt_tokens += prompt
        self._session_tool_usage[tool_name]["usage"].cache_read_tokens += cache_read
        self._session_tool_usage[tool_name]["count"] += 1

    def set_current_prompt(self, prompt: str):
        """Set the current prompt being used."""
        self.current_prompt = prompt

    def get_current_prompt(self) -> Optional[str]:
        """Get the current prompt being used."""
        return self.current_prompt

    def ask_model_prompt(self, template_path: str):
        """Decorator for model interactions using prompt templates.
    
        This decorator uses pydantic-ai to interact with the model.
        
        Args:
            template_path: Path to template file relative to model_prompts directory
            functions: Optional list of functions to make available to the model
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args, **kwargs) -> T:
                adapter = get_ui_adapter()

                self.set_current_prompt(func.__name__)

                type_hints = get_type_hints(func)
                return_type = type_hints.get('return', type(None))
                prompt_key = template_path.split('/')[-1].replace('.md', '')
                if get_settings_manager().is_edit_tools_prompt(prompt_key) and \
                 get_settings_manager().get_setting_enabled("edit_tools"):
                      return_type = None
                # Determine output type and whether to use streaming
                is_streaming = return_type == str
                is_enum = inspect.isclass(return_type) and issubclass(return_type, Enum)
                is_structured = return_type not in [str, None]

                # Get function signature to map args to param names
                sig = inspect.signature(func)
                bound_args = sig.bind(*args, **kwargs)
                bound_args.apply_defaults()
                
                # Filter out None values and empty lists from arguments
                template_context = {
                    k: v for k, v in bound_args.arguments.items() 
                    if v is not None# and v != []
                }
                
                tool_call_collector = template_context.pop('tool_call_collector', None) # TODO
                chat_history = template_context.pop('chat_history', None)

                get_logger().prompt_begin(func.__name__, template_path, 2, template_context)
                
                # Load and render the template
                prompt_loader = get_prompt_loader()
                rendered_prompt = prompt_loader.load(template_path, **template_context)

                # Add additional snippets TODO move to prompt itself
                if get_settings_manager().is_edit_tools_prompt(prompt_key):
                    # Add a snippet to the prompt about how to reply with the updated code
                    edit_tools_snippet = get_settings_manager().get_snippet(prompt_key)
                    rendered_prompt += "\n\n" + edit_tools_snippet
                
                # Show coordinator message (first line or whole prompt if short)
                first_line = f"({template_path}): " + rendered_prompt.split('\n')[0]
                coordinator_msg = f"@model {first_line}" if len(rendered_prompt) > 100 else f"@model {rendered_prompt}"
                adapter.show_message(Message(Actor.COORDINATOR, coordinator_msg))
                
                functions = get_settings_manager().get_prompt_tools(prompt_key)
                try:
                    functions = [get_settings_manager().get_tool_callable(func_name) for func_name in functions]
                except Exception as e:
                    raise ValueError(f"Failed to get tool callables for prompt '{prompt_key}': {e}")

                from vibenix.model_config import get_cached_model_config
                model_config = get_cached_model_config()
                if len(functions) == 0 and model_config["provider"] in ["openai"]:
                    # If no functions are provided, add a noop tool to avoid errors
                    from vibenix.tools import noop_tool
                    functions = [noop_tool]

                # Create agent with appropriate output type
                if is_structured:
                    # For structured outputs (enums, lists, etc.), use output_type
                    agent = VibenixAgent(output_type=return_type)
                else:
                    # For strings, we don't need structured output
                    # AS IT IS this means that even with return_type=None, we get a message response back from the model (fix if needed, with perhaps an object)
                    agent = VibenixAgent()
                
                # Add tools to the agent
                for tool_func in functions:
                    agent.add_tool(tool_func)
                
                try:
                    # Run the agent
                    if is_streaming: # TODO remove when possible
                        # For string returns, use streaming
                        result, usage = agent.run_stream(rendered_prompt, message_history=chat_history if chat_history else [])
                        get_logger().reply_chunk_text(0, result, 4)
                    else:
                        # For non-streaming (structured outputs), just run normally
                        result, usage = agent.run(rendered_prompt, message_history=chat_history if chat_history else [])
                        
                        if return_type == ModelCodeResponse:
                            from vibenix.flake import update_flake
                            if isinstance(result, ModelCodeResponse):
                                result = result.code
                                update_flake(result)
                        
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
                    self.set_current_prompt(None)

                    if chat_history is not None:
                        from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart
                        from vibenix.flake import get_package_contents
                        
                        package_contents = get_package_contents()
                        user_message = ModelRequest(parts=[UserPromptPart(content=rendered_prompt)])
                        if return_type is None:
                            response_content = package_contents
                        else:
                            response_content = str(result) if result is not None else "(empty response)"
                        model_message = ModelResponse(parts=[TextPart(content=response_content)])
                        
                        chat_history.append(user_message)
                        chat_history.append(model_message)

                    return result
                    
                except Exception as e:
                    adapter.show_message(Message(Actor.MODEL, f"Error: {str(e)}"))
                    get_logger().reply_chunk_text(0, f"Error: {str(e)}", 4)
                    get_logger().prompt_end(2)
                    self.set_current_prompt(None)

                    raise e
            
            return wrapper
        return decorator

from vibenix.model_config import get_model_name
model_prompt_manager = ModelPromptManager(get_model_name())

def get_model_prompt_manager() -> ModelPromptManager:
    """Get the global model prompt manager instance."""
    return model_prompt_manager
