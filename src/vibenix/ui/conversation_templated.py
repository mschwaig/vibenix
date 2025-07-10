"""Unified template-based decorator for model conversations using Chat API."""

from functools import wraps
from typing import Callable, TypeVar, Optional, List, Any, get_type_hints, Tuple
import os
from pathlib import Path
from enum import Enum
import inspect

from magentic import Chat, UserMessage, StreamedResponse, StreamedStr
from magentic.chat_model.message import Usage
from vibenix.ui.conversation import (
    Message, Actor, get_ui_adapter, _retry_with_rate_limit, 
    handle_model_chat
)
from vibenix.packaging_flow.model_prompts.prompt_loader import get_prompt_loader


T = TypeVar('T')


def ask_model_prompt(template_path: str, functions: Optional[List[Callable]] = None):
    """Unified decorator for model interactions using prompt templates.
    
    This decorator:
    - Loads prompts from template files
    - Supports both streaming (StreamedStr) and enum returns
    - Integrates with magentic's Chat API for function calling
    - Handles all return type conversions automatically
    - Returns a tuple of (result, usage) where usage contains token counts
    
    Args:
        template_path: Path to template file relative to model_prompts directory
        functions: Optional list of functions to make available to the model
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        # Get return type hint
        type_hints = get_type_hints(func)
        return_type = type_hints.get('return', type(None))
        
        # Determine if this returns StreamedStr or an Enum
        is_streaming = return_type == StreamedStr
        is_enum = inspect.isclass(return_type) and issubclass(return_type, Enum)
        
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
            additional_functions = template_context.pop('additional_functions', [])

            print(f"Function: {func.__name__}") # print prompt function name
            print(f"Template: {template_path}") # print prompt function template
            print(f"Args: {template_context}") # print prompt function args
            print()
            
            # Load and render the template
            prompt_loader = get_prompt_loader()
            rendered_prompt = prompt_loader.load(template_path, **template_context)
            
            # Show coordinator message (first line or whole prompt if short)
            first_line = rendered_prompt.split('\n')[0]
            coordinator_msg = f"@model {first_line}" if len(rendered_prompt) > 100 else f"@model {rendered_prompt}"
            adapter.show_message(Message(Actor.COORDINATOR, coordinator_msg))
            
            # Create chat with the rendered prompt
            chat_functions = (functions or []) + additional_functions
            
            if is_streaming:
                # For streaming responses, use StreamedResponse output type
                chat = Chat(
                    messages=[UserMessage(rendered_prompt)],
                    functions=chat_functions,
                    output_types=[StreamedResponse],
                )
            else:
                # For non-streaming (like enums), use the actual return type
                chat = Chat(
                    messages=[UserMessage(rendered_prompt)],
                    functions=chat_functions,
                    output_types=[return_type] if return_type != type(None) else None,
                )
            
            def _execute_chat():
                if is_streaming:
                    # For streaming, submit the chat and use handle_model_chat
                    submitted_chat = _retry_with_rate_limit(chat.submit)
                    # Check if tool_call_collector was passed in kwargs
                    tool_call_collector = template_context.get('tool_call_collector')
                    result, usage = handle_model_chat(submitted_chat, tool_call_collector)
                    
                    # Log the response and usage data
                    print(f"Response: {result[:100]}..." if len(result) > 100 else f"Response: {result}")
                    print(f"Input tokens: {usage.input_tokens}")
                    print(f"Output tokens: {usage.output_tokens}")
                    print()
                    
                    return result
                else:
                    # For non-streaming (enum), we need to handle differently
                    def _get_enum_result():
                        # Submit the chat and get the new chat with response
                        submitted_chat = chat.submit()
                        
                        # Get the assistant message and extract content
                        assistant_message = submitted_chat.last_message
                        result = assistant_message.content
                        
                        # Show the model's response in the UI
                        if result is not None:
                            adapter.show_message(Message(Actor.MODEL, str(result)))
                        
                        # Log the response and usage data
                        if assistant_message.usage:
                            print(f"Response: {result}")
                            print(f"Input tokens: {assistant_message.usage.input_tokens}")
                            print(f"Output tokens: {assistant_message.usage.output_tokens}")
                            print()
                        else:
                            print("Warning: No usage data available from model response")
                        
                        return result
                    
                    # Use retry wrapper for the entire enum result function
                    return _retry_with_rate_limit(_get_enum_result)
            
            try:
                return _execute_chat()
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                error_msg = f"Error in model function {func.__name__}: {str(e)}\n{tb}"
                from vibenix.ui.logging_config import logger
                logger.error(error_msg)
                raise
        
        return wrapper
    return decorator