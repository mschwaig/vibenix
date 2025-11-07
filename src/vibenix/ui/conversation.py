"""Coordinator pattern for vibenix - separates business logic from UI."""

from abc import ABC, abstractmethod
from typing import Callable, Optional, TypeVar
from functools import wraps
from enum import Enum
from dataclasses import dataclass
from datetime import datetime
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
        
        # TODO: Will be reimplemented with pydantic-ai streaming
        # For now, just return the result as a string
        logger.info("🤖 model: ", end="")
        
        full_response = str(streamed_result)
        print(full_response, flush=True)
        
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


# Usage class for tracking tokens
@dataclass
class Usage:
    """Usage tracking with token counts."""
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0, cache_read_tokens: int = 0, model: str = ""):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.cache_read_tokens = cache_read_tokens
        self.model = model

    def calculate_cost(self) -> float:
        """Calculate cost from PydanticAI RunUsage object."""

        from vibenix.model_config import calc_model_pricing
        return calc_model_pricing(self.model, self.prompt_tokens, self.completion_tokens, self.cache_read_tokens)

    def __sub__(self, other: 'Usage') -> 'Usage':
        return Usage(
            prompt_tokens=self.prompt_tokens - other.prompt_tokens,
            completion_tokens=self.completion_tokens - other.completion_tokens,
            cache_read_tokens=self.cache_read_tokens - other.cache_read_tokens,
            model=self.model
        )
