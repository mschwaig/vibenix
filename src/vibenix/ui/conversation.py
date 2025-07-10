"""Coordinator pattern for vibenix - separates business logic from UI."""

from abc import ABC, abstractmethod
from typing import Callable, Optional, TypeVar
from functools import wraps
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
from magentic import StreamedStr, Chat, FunctionCall, ToolResultMessage
from magentic.chat_model.message import Usage
from vibenix.ccl_log import get_logger

# Type variable for function return types
T = TypeVar('T')


class Actor(Enum):
    """The three actors in the vibenix conversation."""
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
        from vibenix.ui.logging_config import logger
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
        from vibenix.ui.logging_config import logger
        
        actor_symbol = {
            Actor.COORDINATOR: "🎯",
            Actor.MODEL: "🤖",
            Actor.USER: "👤"
        }
        
        formatted = f"\n{actor_symbol[message.actor]} {message.actor.value}: {message.content}"
        logger.info(formatted)
    
    def show_error(self, error: str):
        """Display error in terminal."""
        from vibenix.ui.logging_config import logger
        logger.error(f"❌ Error: {error}")
    
    def show_progress(self, message: str):
        """Show progress in terminal."""
        from vibenix.ui.logging_config import logger
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


def _retry_with_rate_limit(func, *args, max_retries=20, base_delay=5, **kwargs):
    """Execute a function with rate limit retry logic.
    
    Args:
        func: The function to execute
        *args: Positional arguments for func
        max_retries: Maximum number of retry attempts
        base_delay: Base delay in seconds for exponential backoff
        **kwargs: Keyword arguments for func
        
    Returns:
        The result of func
        
    Raises:
        The last exception if all retries are exhausted
    """
    import time
    import traceback
    from vibenix.ui.logging_config import logger
    
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Import litellm to check for rate limit and server errors
            try:
                import litellm
                is_rate_limit_error = isinstance(e, litellm.RateLimitError)
                is_overload_error = isinstance(e, litellm.InternalServerError)
                
                # Also check for AnthropicError from litellm.llms.anthropic.common_utils
                try:
                    from litellm.llms.anthropic.common_utils import AnthropicError
                    is_anthropic_error = isinstance(e, AnthropicError)
                except ImportError:
                    is_anthropic_error = False
            except ImportError:
                is_rate_limit_error = False
                is_overload_error = False
                is_anthropic_error = False
            
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
            
            # Check if it's a StopIteration error (can happen during stream parsing)
            is_stop_iteration_error = isinstance(e, StopIteration)
            
            if (is_rate_limit_error or is_overload_error or is_anthropic_error or is_429_error or is_stop_iteration_error) and attempt < max_retries - 1:
                # Calculate delay based on retry-after header or polynomial backoff
                if retry_after:
                    try:
                        delay = int(retry_after)
                        logger.warning(f"Rate limit hit, waiting {delay} seconds as specified by retry-after header...")
                    except ValueError:
                        # If retry-after is not a valid integer, fall back to polynomial backoff
                        logger.warning(f"Invalid retry-after header value: {retry_after}, falling back to polynomial backoff")
                        delay = _calculate_retry_delay(attempt, base_delay)
                        # Add extra 5 minutes for good measure when retry-after parsing fails
                        delay += 300
                        logger.warning(f"Rate limit hit, waiting {delay:.1f} seconds (polynomial backoff + 5 min buffer)...")
                else:
                    # Polynomial backoff
                    delay = _calculate_retry_delay(attempt, base_delay)
                    if is_rate_limit_error or is_429_error:
                        logger.warning(f"Rate limit hit, waiting {delay:.1f} seconds before retry...")
                    elif is_anthropic_error:
                        logger.warning(f"Anthropic API error ({str(e)}), waiting {delay:.1f} seconds before retry...")
                    elif is_stop_iteration_error:
                        logger.warning(f"Stream parsing error (StopIteration), waiting {delay:.1f} seconds before retry...")
                    else:
                        logger.warning(f"API overloaded, waiting {delay:.1f} seconds before retry...")
                
                time.sleep(delay)
                continue
            else:
                # Either not a retryable error, or we've exhausted retries
                if attempt == max_retries - 1:
                    logger.error(f"Exhausted {max_retries} retries due to rate limiting")
                raise


def handle_model_chat(chat: Chat, tool_call_collector=None) -> tuple[str, Usage]:
    """Handle a model chat session with function calls and streaming responses.
    
    Args:
        chat: The Chat instance to handle
        tool_call_collector: Optional list to collect tool calls made during this chat
        
    Returns:
        A tuple of (response_text, usage) where usage contains token counts
    """
    def _chat_processing():
        output = None
        ends_with_function_call = True
        adapter = get_ui_adapter()
        current_chat = chat
        last_message = None

        response_chunk_num = 1
        while ends_with_function_call:
            ends_with_function_call = False
            last_message = current_chat.last_message
            for item in last_message.content:
                if isinstance(item, StreamedStr):
                    adapter.handle_model_streaming(item)
                    output = str(item)
                    get_logger().reply_chunk_text(response_chunk_num, output, 4)
                elif isinstance(item, FunctionCall):
                    # Capture tool call info before execution
                    if tool_call_collector is not None:
                        # Extract function name and arguments from the FunctionCall object
                        tool_info = {
                            'function': item.function.__name__,
                            'arguments': item.arguments
                        }
                        tool_call_collector.append(tool_info)

                    get_logger().reply_chunk_function_call(response_chunk_num, 4)
                    function_call = item()

                    adapter.show_message(Message(Actor.MODEL, function_call))
                    current_chat = current_chat.add_message(ToolResultMessage(function_call, item._unique_id))
                    ends_with_function_call = True
                response_chunk_num = response_chunk_num + 1
            if ends_with_function_call:
                current_chat = _retry_with_rate_limit(current_chat.submit)

        # Ensure we always have usage data
        output_str = str(output)
        #if not last_message or not last_message.usage:
        #    raise ValueError("No usage data available from model response")
        return (output_str, last_message.usage)
    
    # Use retry wrapper for the entire chat processing
    return _chat_processing()
