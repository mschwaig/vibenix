"""Template-aware decorators for model conversations."""

from functools import wraps
from typing import Callable, TypeVar, Optional, Union
from pathlib import Path
from enum import Enum
import inspect

from magentic import StreamedStr, prompt
from vibenix.ui.conversation import (
    Message, Actor, get_ui_adapter, _retry_with_rate_limit, 
    handle_model_chat, coordinator_message
)
from vibenix.packaging_flow.model_prompts.prompt_loader import get_prompt_loader


T = TypeVar('T')


def ask_model_template(template_path: str):
    """Decorator for functions that use prompt templates and return StreamedStr.
    
    Args:
        template_path: Path to template file relative to model_prompts directory
                      (e.g., 'error_fixing/fix_build_error.md')
    """
    def decorator(func: Callable[..., StreamedStr]) -> Callable[..., str]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> str:
            adapter = get_ui_adapter()
            
            # Get function signature to map args to param names
            sig = inspect.signature(func)
            bound_args = sig.bind(*args, **kwargs)
            bound_args.apply_defaults()
            
            # Load and render the template
            prompt_loader = get_prompt_loader()
            rendered_prompt = prompt_loader.load(template_path, **bound_args.arguments)
            
            # Show coordinator message (first line or whole prompt if short)
            first_line = rendered_prompt.split('\n')[0]
            coordinator_msg = f"@model {first_line}" if len(rendered_prompt) > 100 else f"@model {rendered_prompt}"
            adapter.show_message(Message(Actor.COORDINATOR, coordinator_msg))
            
            # Apply the magentic prompt decorator with rendered template
            prompt_decorated_func = prompt(rendered_prompt)(func)
            
            def _model_call_and_stream():
                # Call the prompt-decorated function to get StreamedStr
                streamed_result = prompt_decorated_func(*args, **kwargs)
                
                if streamed_result is None:
                    raise ValueError(f"Model function {func.__name__} returned None")
                
                # Handle the streaming in the adapter
                return adapter.handle_model_streaming(streamed_result)
            
            try:
                # Use the retry wrapper for the entire model call
                return _retry_with_rate_limit(_model_call_and_stream)
                
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                error_msg = f"Error in model function {func.__name__}: {str(e)}\n{tb}"
                from vibenix.ui.logging_config import logger
                logger.error(error_msg)
                raise
        
        return wrapper
    return decorator


def ask_model_enum_template(template_path: str):
    """Decorator for functions that use prompt templates and return an Enum.
    
    Args:
        template_path: Path to template file relative to model_prompts directory
    """
    def decorator(func: Callable) -> Callable:
        import inspect
        from enum import Enum
        
        # Get the return type annotation
        sig = inspect.signature(func)
        return_type = sig.return_annotation
        
        # Check if return type is an Enum
        if not (inspect.isclass(return_type) and issubclass(return_type, Enum)):
            raise TypeError(f"ask_model_enum_template can only be used with functions that return Enum types, got {return_type}")
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            adapter = get_ui_adapter()
            
            # Get function signature to map args to param names
            bound_args = sig.bind(*args, **kwargs)
            bound_args.apply_defaults()
            
            # Load and render the template
            prompt_loader = get_prompt_loader()
            rendered_prompt = prompt_loader.load(template_path, **bound_args.arguments)
            
            # Show coordinator message
            first_line = rendered_prompt.split('\n')[0]
            coordinator_msg = f"@model {first_line}" if len(rendered_prompt) > 100 else f"@model {rendered_prompt}"
            adapter.show_message(Message(Actor.COORDINATOR, coordinator_msg))
            
            # Apply the magentic prompt decorator
            prompt_decorated_func = prompt(rendered_prompt)(func)
            
            def _model_call():
                # Call the prompt-decorated function to get result
                result = prompt_decorated_func(*args, **kwargs)
                
                if result is None:
                    raise ValueError(f"Model function {func.__name__} returned None")
                
                # Convert result to Enum if needed
                if isinstance(result, return_type):
                    return result
                elif isinstance(result, str):
                    try:
                        return return_type(result.strip())
                    except ValueError:
                        # If direct conversion fails, try by name
                        return return_type[result.strip()]
                else:
                    return return_type(str(result).strip())
            
            try:
                return _retry_with_rate_limit(_model_call)
                
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                error_msg = f"Error in model function {func.__name__}: {str(e)}\n{tb}"
                from vibenix.ui.logging_config import logger
                logger.error(error_msg)
                raise
        
        return wrapper
    return decorator