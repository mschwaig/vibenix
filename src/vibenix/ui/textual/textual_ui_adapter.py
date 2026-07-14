"""Textual UI adapter for the coordinator pattern."""

import asyncio
from typing import Callable, Any, Optional
from datetime import datetime
from vibenix.ui.conversation import UIAdapter, Message, Actor
from textual.app import App
from textual.widgets import RichLog


class TextualUIAdapter(UIAdapter):
    """Textual-based UI adapter for the chat interface."""
    
    def __init__(self, app: App, chat_history):
        self.app = app
        self.chat_history = chat_history
        self.user_input_future: Optional[asyncio.Future] = None
        self.current_model_message = None
        self._initial_url = None
    
    def set_user_input_future(self, future: asyncio.Future):
        """Set the future that will be resolved when user provides input."""
        self.user_input_future = future
    
    def ask_user(self, prompt: str, timeout: Optional[float] = None) -> Optional[str]:
        """Ask the user for input via the textual interface.
        
        Args:
            prompt: The prompt to show to the user.
            timeout: Optional timeout in seconds. If None, wait indefinitely.
        
        Returns:
            The user's response, or None if timeout expired.
        """
        # Check if this is a progress evaluation request
        if "Please evaluate the build progress" in prompt and "Previous error:" in prompt:
            return self._handle_progress_evaluation(prompt)
        
        # Regular text input handling
        # Show coordinator message
        self.show_message(Message(Actor.COORDINATOR, prompt))
        
        # Use threading.Event for synchronization
        import threading
        response_event = threading.Event()
        response_container = [None]
        
        # Set up the future in the main thread
        def setup_future():
            self.user_input_future = asyncio.Future()
            input_field = self.app.query_one("#chat-input")
            input_field.disabled = False
            input_field.focus()
            
            def on_future_done(future):
                response_container[0] = future.result()
                response_event.set()
            
            self.user_input_future.add_done_callback(on_future_done)
        
        self.app.call_from_thread(setup_future)
        
        # Wait for response with optional timeout
        got_response = response_event.wait(timeout=timeout)
        if not got_response:
            # Timeout expired, show message and return None
            self.show_message(Message(Actor.COORDINATOR, f"⏱️ Timeout after {timeout}s waiting for response"))
            return None
        
        response = response_container[0]
        
        # Show user's response
        self.show_message(Message(Actor.USER, response))
        
        return response
    
    def _handle_progress_evaluation(self, prompt: str) -> str:
        """Handle progress evaluation with ProgressPoll widget."""
        import threading
        import re
        
        # Extract errors from the prompt
        prev_error_match = re.search(r'Previous error:\s*\n(.*?)\n\nNew error:', prompt, re.DOTALL)
        new_error_match = re.search(r'New error:\s*\n(.*?)\n\nPlease choose:', prompt, re.DOTALL)
        
        prev_error = prev_error_match.group(1).strip() if prev_error_match else "Previous error not found"
        new_error = new_error_match.group(1).strip() if new_error_match else "New error not found"
        
        response_event = threading.Event()
        response_container = [None]
        
        # Store the response event so the main app can set it
        self.progress_response_event = response_event
        self.progress_response_container = response_container
        
        def show_progress_poll():
            # Add the progress poll widget
            self.chat_history.add_progress_poll(prev_error, new_error)
        
        self.app.call_from_thread(show_progress_poll)
        
        # Wait for response (will be set by handle_progress_choice in main app)
        response_event.wait()
        choice = response_container[0]
        
        # Show user's choice
        choice_text = {
            "1": "❌ Regress (build fails earlier)",
            "2": "⚠️ Eval Error (code failed to evaluate)", 
            "3": "✅ Progress (build fails later)",
            "4": "🔧 Hash Mismatch (needs correct hash)"
        }.get(choice, f"Choice {choice}")
        
        self.show_message(Message(Actor.USER, choice_text))
        
        return choice
    
    def _enable_input(self):
        """Re-enable the input field."""
        input_field = self.app.query_one("#chat-input")
        input_field.disabled = False
    
    def handle_model_streaming(self, streamed_result) -> str:
        """Handle streaming from a model response."""
        # TODO: Implement pydantic-ai streaming
        # For now, just display the result as a single message
        
        # Create initial model message widget
        self.current_model_message = self.app.call_from_thread(
            self.chat_history.add_streaming_message,
            "",
            "model"
        )
        
        # For now, treat as non-streaming
        full_response = str(streamed_result)
        self.current_model_message.content = full_response
        self._update_last_message(full_response)
        
        return full_response
    
    def show_message(self, message: Message):
        """Display a message in the chat."""
        actor_map = {
            Actor.COORDINATOR: "coordinator",
            Actor.MODEL: "model",
            Actor.USER: "user"
        }
        
        sender = actor_map[message.actor]
        # Use call_from_thread since we're running in a worker thread
        def add_msg():
            self.chat_history.add_message(message.content, sender)
        
        self.app.call_from_thread(add_msg)
    
    def show_error(self, error: str):
        """Display an error message."""
        self.app.call_from_thread(
            self.chat_history.add_message,
            f"❌ Error: {error}",
            "coordinator"
        )
    
    def show_progress(self, message: str):
        """Show a progress update."""
        self.app.call_from_thread(
            self.chat_history.add_message,
            f"⏳ {message}",
            "coordinator"
        )
    
    def _update_last_message(self, content: str):
        """Update the last message in the chat (for streaming)."""
        if self.current_model_message:
            self.current_model_message.content = content
            # Update the message widget
            self.app.call_from_thread(
                self.current_model_message.update_content,
                content
            )