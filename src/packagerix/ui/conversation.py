"""Coordinator pattern for packagerix - separates business logic from UI."""

from abc import ABC, abstractmethod
from typing import Callable, Optional, TypeVar
from functools import wraps
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from magentic import StreamedStr, Chat, FunctionCall, ToolResultMessage

# Type variable for function return types
T = TypeVar('T')


class Actor(Enum):
    """The three actors in the packagerix conversation."""
    COORDINATOR = "coordinator"
    MODEL = "model"
    USER = "user"


@dataclass
class Message:
    """A message in the conversation."""
    actor: Actor
    content: str
    timestamp: datetime = None
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


class UIAdapter(ABC):
    """Abstract base class for UI adapters - all methods are synchronous from coordinator's perspective."""
    
    @abstractmethod
    def ask_user(self, prompt: str) -> str:
        """Ask the user for input and return the complete response."""
        pass
    
    @abstractmethod
    def handle_model_streaming(self, streamed_result) -> str:
        """Handle streaming from a model response. Returns the complete response as a string."""
        pass
    
    @abstractmethod
    def show_message(self, message: Message):
        """Display a message from any actor."""
        pass
    
    @abstractmethod
    def show_error(self, error: str):
        """Display an error message."""
        pass
    
    @abstractmethod
    def show_progress(self, message: str):
        """Show a progress update."""
        pass


class TerminalUIAdapter(UIAdapter):
    """Terminal-based UI adapter for CLI mode."""
    
    def __init__(self):
        pass
    
    def ask_user(self, prompt: str) -> str:
        """Ask user for input via terminal."""
        self.show_message(Message(Actor.COORDINATOR, prompt))
        
        # Simple synchronous input
        response = input("")
        
        self.show_message(Message(Actor.USER, response))
        return response
    
    def handle_model_streaming(self, streamed_result) -> str:
        """Handle streaming from a model response."""
        from packagerix.ui.logging_config import logger
        from magentic import StreamedStr
        
        if not isinstance(streamed_result, StreamedStr):
            raise TypeError(f"Expected StreamedStr, got {type(streamed_result)}")
        
        logger.info("🤖 model: ", end="")
        
        full_response = ""
        for chunk in streamed_result:
            full_response += chunk
            print(chunk, end="", flush=True)
        
        print()  # newline at end
        return full_response
    
    def show_message(self, message: Message):
        """Display a message in terminal."""
        from packagerix.ui.logging_config import logger
        
        actor_symbol = {
            Actor.COORDINATOR: "🎯",
            Actor.MODEL: "🤖",
            Actor.USER: "👤"
        }
        
        formatted = f"\n{actor_symbol[message.actor]} {message.actor.value}: {message.content}"
        logger.info(formatted)
    
    def show_error(self, error: str):
        """Display error in terminal."""
        from packagerix.ui.logging_config import logger
        logger.error(f"❌ Error: {error}")
    
    def show_progress(self, message: str):
        """Show progress in terminal."""
        from packagerix.ui.logging_config import logger
        logger.info(f"⏳ {message}")


# Global UI adapter instance
_ui_adapter: Optional[UIAdapter] = None


def set_ui_adapter(adapter: UIAdapter):
    """Set the global UI adapter."""
    global _ui_adapter
    _ui_adapter = adapter


def get_ui_adapter() -> UIAdapter:
    """Get the current UI adapter, defaulting to terminal."""
    global _ui_adapter
    if _ui_adapter is None:
        _ui_adapter = TerminalUIAdapter()
    return _ui_adapter


def ask_user(prompt_text: str):
    """Decorator for functions that need user input. Prompt should start with '@user'."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs):
            adapter = get_ui_adapter()
            
            # Simple synchronous call - adapter handles any async internally
            user_input = adapter.ask_user(prompt_text)
            
            # Call the original function with user input as first argument
            return func(user_input, *args, **kwargs)
        
        return wrapper
    return decorator


def _calculate_retry_delay(attempt: int, base_delay: int = 5, max_delay: int = 600) -> float:
    """Calculate retry delay using polynomial backoff.
    
    Args:
        attempt: The current attempt number (0-indexed)
        base_delay: Base delay in seconds
        max_delay: Maximum delay cap in seconds
        
    Returns:
        Delay in seconds
    """
    import math
    delay = base_delay + base_delay * math.pow(float(attempt), 1.5)
    return min(delay, max_delay)


def ask_model(prompt_text: str):
    """Decorator for functions that need model input. Should only be used on stub functions.
    
    This decorator:
    1. Applies the @prompt decorator with the given prompt
    2. Shows the coordinator message in the UI
    3. Handles streaming responses
    4. Returns the complete string
    """
    def decorator(func: Callable[..., StreamedStr]) -> Callable[..., str]:
        # Apply the prompt decorator using the same prompt text
        from magentic import prompt, StreamedStr
        prompt_decorated_func = prompt(prompt_text.replace("@model ", ""))(func)
        
        @wraps(func)
        def wrapper(*args, **kwargs) -> str:
            adapter = get_ui_adapter()
            max_retries = 20
            base_delay = 5  # seconds
            
            for attempt in range(max_retries):
                try:
                    # Show the coordinator message (only on first attempt)
                    if attempt == 0:
                        adapter.show_message(Message(Actor.COORDINATOR, prompt_text))
                    else:
                        from packagerix.ui.logging_config import logger
                        logger.info(f"Retrying model call (attempt {attempt + 1}/{max_retries})")
                    
                    # Call the prompt-decorated function to get StreamedStr
                    streamed_result = prompt_decorated_func(*args, **kwargs)
                    
                    if streamed_result is None:
                        raise ValueError(f"Model function {func.__name__} returned None - this indicates a problem with the prompt or model configuration")
                    
                    # Handle the streaming in the adapter and return final string
                    return adapter.handle_model_streaming(streamed_result)
                    
                except Exception as e:
                    # Import litellm to check for rate limit and server errors
                    try:
                        import litellm
                        is_rate_limit_error = isinstance(e, litellm.RateLimitError)
                        is_overload_error = isinstance(e, litellm.InternalServerError)
                    except ImportError:
                        is_rate_limit_error = False
                        is_overload_error = False
                    
                    # Check if it's an httpx error with 429 status (for completeness)
                    is_429_error = False
                    retry_after = None
                    if hasattr(e, '__cause__') and e.__cause__:
                        cause = e.__cause__
                        if hasattr(cause, 'response') and hasattr(cause.response, 'status_code'):
                            is_429_error = cause.response.status_code == 429
                            # Try to get retry-after header
                            if is_429_error and hasattr(cause.response, 'headers'):
                                retry_after = cause.response.headers.get('retry-after')
                    
                    if (is_rate_limit_error or is_overload_error or is_429_error) and attempt < max_retries - 1:
                        # Calculate delay based on retry-after header or polynomial backoff
                        if retry_after:
                            try:
                                delay = int(retry_after)
                                from packagerix.ui.logging_config import logger
                                logger.warning(f"Rate limit hit, waiting {delay} seconds as specified by retry-after header...")
                            except ValueError:
                                # If retry-after is not a valid integer, fall back to polynomial backoff
                                from packagerix.ui.logging_config import logger
                                logger.warning(f"Invalid retry-after header value: {retry_after}, falling back to polynomial backoff")
                                delay = _calculate_retry_delay(attempt, base_delay)
                                # Add extra 5 minutes for good measure when retry-after parsing fails
                                delay += 300
                                logger.warning(f"Rate limit hit, waiting {delay:.1f} seconds (polynomial backoff + 5 min buffer)...")
                        else:
                            # Polynomial backoff
                            delay = _calculate_retry_delay(attempt, base_delay)
                            from packagerix.ui.logging_config import logger
                            if is_rate_limit_error or is_429_error:
                                logger.warning(f"Rate limit hit, waiting {delay:.1f} seconds before retry...")
                            else:
                                logger.warning(f"API overloaded, waiting {delay:.1f} seconds before retry...")
                        
                        import time
                        time.sleep(delay)
                        continue
                    else:
                        # Either not a retryable error, or we've exhausted retries
                        import traceback
                        tb = traceback.format_exc()
                        error_msg = f"Error in model function {func.__name__}: {str(e)}\n{tb}"
                        from packagerix.ui.logging_config import logger
                        logger.error(error_msg)
                        raise
        
        return wrapper
    return decorator


def ask_model_enum(prompt_text: str):
    """Decorator for functions that need model input and return an Enum. Should only be used on stub functions.
    
    This decorator:
    1. Applies the @prompt decorator with the given prompt
    2. Shows the coordinator message in the UI  
    3. Handles non-streaming responses
    4. Converts string result to Enum based on function's return type annotation
    """
    def decorator(func: Callable) -> Callable:
        # Apply the prompt decorator using the same prompt text
        from magentic import prompt
        import inspect
        from enum import Enum
        
        # Get the return type annotation
        sig = inspect.signature(func)
        return_type = sig.return_annotation
        
        # Check if return type is an Enum
        if not (inspect.isclass(return_type) and issubclass(return_type, Enum)):
            raise TypeError(f"ask_model_enum can only be used with functions that return Enum types, got {return_type}")
        
        prompt_decorated_func = prompt(prompt_text.replace("@model ", ""))(func)
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            adapter = get_ui_adapter()
            max_retries = 20
            base_delay = 5  # seconds
            
            for attempt in range(max_retries):
                try:
                    # Show the coordinator message (only on first attempt)
                    if attempt == 0:
                        adapter.show_message(Message(Actor.COORDINATOR, prompt_text))
                    else:
                        from packagerix.ui.logging_config import logger
                        logger.info(f"Retrying model call (attempt {attempt + 1}/{max_retries})")
                    
                    # Call the prompt-decorated function to get result
                    result = prompt_decorated_func(*args, **kwargs)
                    
                    if result is None:
                        raise ValueError(f"Model function {func.__name__} returned None - this indicates a problem with the prompt or model configuration")
                    
                    # Convert result to Enum if needed
                    if isinstance(result, return_type):
                        # Already the correct enum type
                        return result
                    elif isinstance(result, str):
                        # String result, convert to enum
                        try:
                            return return_type(result.strip())
                        except ValueError:
                            # If direct conversion fails, try by name
                            return return_type[result.strip()]
                    else:
                        # Try to convert whatever we got
                        return return_type(str(result).strip())
                        
                except Exception as e:
                    # Import litellm to check for rate limit and server errors
                    try:
                        import litellm
                        is_rate_limit_error = isinstance(e, litellm.RateLimitError)
                        is_overload_error = isinstance(e, litellm.InternalServerError)
                    except ImportError:
                        is_rate_limit_error = False
                        is_overload_error = False
                    
                    # Check if it's an httpx error with 429 status (for completeness)
                    is_429_error = False
                    retry_after = None
                    if hasattr(e, '__cause__') and e.__cause__:
                        cause = e.__cause__
                        if hasattr(cause, 'response') and hasattr(cause.response, 'status_code'):
                            is_429_error = cause.response.status_code == 429
                            # Try to get retry-after header
                            if is_429_error and hasattr(cause.response, 'headers'):
                                retry_after = cause.response.headers.get('retry-after')
                    
                    if (is_rate_limit_error or is_overload_error or is_429_error) and attempt < max_retries - 1:
                        # Calculate delay based on retry-after header or polynomial backoff
                        if retry_after:
                            try:
                                delay = int(retry_after)
                                from packagerix.ui.logging_config import logger
                                logger.warning(f"Rate limit hit, waiting {delay} seconds as specified by retry-after header...")
                            except ValueError:
                                # If retry-after is not a valid integer, fall back to polynomial backoff
                                from packagerix.ui.logging_config import logger
                                logger.warning(f"Invalid retry-after header value: {retry_after}, falling back to polynomial backoff")
                                delay = _calculate_retry_delay(attempt, base_delay)
                                # Add extra 5 minutes for good measure when retry-after parsing fails
                                delay += 300
                                logger.warning(f"Rate limit hit, waiting {delay:.1f} seconds (polynomial backoff + 5 min buffer)...")
                        else:
                            # Polynomial backoff
                            delay = _calculate_retry_delay(attempt, base_delay)
                            from packagerix.ui.logging_config import logger
                            if is_rate_limit_error or is_429_error:
                                logger.warning(f"Rate limit hit, waiting {delay:.1f} seconds before retry...")
                            else:
                                logger.warning(f"API overloaded, waiting {delay:.1f} seconds before retry...")
                        
                        import time
                        time.sleep(delay)
                        continue
                    else:
                        # Either not a retryable error, or we've exhausted retries
                        import traceback
                        tb = traceback.format_exc()
                        error_msg = f"Error in model function {func.__name__}: {str(e)}\n{tb}"
                        from packagerix.ui.logging_config import logger
                        logger.error(error_msg)
                        raise
        
        return wrapper
    return decorator


def coordinator_message(content: str):
    """Send a message from the coordinator."""
    adapter = get_ui_adapter()
    adapter.show_message(Message(Actor.COORDINATOR, content))


def coordinator_error(error: str):
    """Show an error from the coordinator."""
    adapter = get_ui_adapter()
    adapter.show_error(error)


def coordinator_progress(message: str):
    """Show a progress update from the coordinator."""
    adapter = get_ui_adapter()
    adapter.show_progress(message)


def handle_model_chat(chat: Chat) -> str:
    """Handle a model chat session with function calls and streaming responses.
    
    Args:
        chat: The Chat instance to handle
        
    Returns:
        The final string response from the model
    """
    output = None
    ends_with_function_call = True
    adapter = get_ui_adapter()

    while ends_with_function_call:
        ends_with_function_call = False
        for item in chat.last_message.content:
            if isinstance(item, StreamedStr):
                adapter.handle_model_streaming(item)
                output = item
                ends_with_function_call = False
            elif isinstance(item, FunctionCall):
                function_call = item()
                adapter.show_message(Message(Actor.MODEL, function_call))
                chat = chat.add_message(ToolResultMessage(function_call, item._unique_id))
                ends_with_function_call = True
        
        if ends_with_function_call:
            chat = chat.submit()

    return str(output)