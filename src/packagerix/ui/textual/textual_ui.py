from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, ScrollableContainer
from textual.widgets import Input, Static, Header, Footer, Button, RichLog
from textual.message import Message
from textual import on, work
from textual.worker import Worker, WorkerState
from textual.screen import ModalScreen
from rich.text import Text
from rich.panel import Panel
from rich.console import Console
from rich.markdown import Markdown
import asyncio
from datetime import datetime

# Import existing packagerix functionality
from packagerix import config
from packagerix.packaging_flow.model_prompts import (
    set_up_project,
    summarize_github
)
from packagerix.parsing import scrape_and_process, extract_updated_code
from packagerix.flake import init_flake
from packagerix.nix import get_last_ten_lines, invoke_build, error_stack
from packagerix.errors import NixError
from packagerix.ui.logging_config import logger, log_capture
import os


class ChatMessage(Static):
    """A widget representing a single chat message."""
    
    def __init__(self, content: str, sender: str, timestamp: datetime = None):
        self.content = content
        self.sender = sender
        self.timestamp = timestamp or datetime.now()
        self.panel_static = None
        super().__init__()
        
    def compose(self) -> ComposeResult:
        """Create the message layout."""
        time_str = self.timestamp.strftime("%H:%M")
        
        if self.sender == "coordinator":
            # Coordinator message with yellow styling
            message_text = Text()
            message_text.append(f"🔧 coordinator ", style="bold yellow")
            message_text.append(f"({time_str})", style="dim")
            message_text.append(f"\n{self.content}", style="yellow")
            self.panel_static = Static(Panel(message_text, border_style="yellow", padding=(0, 1)))
            yield self.panel_static
        elif self.sender == "model":
            # AI model message with blue styling
            message_text = Text()
            message_text.append(f"🤖 model ", style="bold blue")
            message_text.append(f"({time_str})", style="dim")
            message_text.append(f"\n{self.content}", style="blue")
            self.panel_static = Static(Panel(message_text, border_style="blue", padding=(0, 1)))
            yield self.panel_static
        elif self.sender == "user":
            # User message with green styling
            message_text = Text()
            message_text.append(f"👤 user ", style="bold green")
            message_text.append(f"({time_str})", style="dim")
            message_text.append(f"\n{self.content}", style="green")
            self.panel_static = Static(Panel(message_text, border_style="green", padding=(0, 1)))
            yield self.panel_static
        else:
            # Legacy packagerix messages (for backwards compatibility)
            message_text = Text()
            message_text.append(f"🤖 packagerix ", style="bold blue")
            message_text.append(f"({time_str})", style="dim")
            message_text.append(f"\n{self.content}", style="blue")
            self.panel_static = Static(Panel(message_text, border_style="blue", padding=(0, 1)))
            yield self.panel_static
    
    def update_content(self, new_content: str):
        """Update the message content (for streaming)."""
        self.content = new_content
        if self.panel_static:
            time_str = self.timestamp.strftime("%H:%M")
            message_text = Text()
            
            if self.sender == "coordinator":
                message_text.append(f"🔧 coordinator ", style="bold yellow")
                message_text.append(f"({time_str})", style="dim")
                message_text.append(f"\n{self.content}", style="yellow")
                self.panel_static.update(Panel(message_text, border_style="yellow", padding=(0, 1)))
            elif self.sender == "model":
                message_text.append(f"🤖 model ", style="bold blue")
                message_text.append(f"({time_str})", style="dim")
                message_text.append(f"\n{self.content}", style="blue")
                self.panel_static.update(Panel(message_text, border_style="blue", padding=(0, 1)))
            elif self.sender == "user":
                message_text.append(f"👤 user ", style="bold green")
                message_text.append(f"({time_str})", style="dim")
                message_text.append(f"\n{self.content}", style="green")
                self.panel_static.update(Panel(message_text, border_style="green", padding=(0, 1)))
            else:
                # Legacy packagerix messages
                message_text.append(f"🤖 packagerix ", style="bold blue")
                message_text.append(f"({time_str})", style="dim")
                message_text.append(f"\n{self.content}", style="blue")
                self.panel_static.update(Panel(message_text, border_style="blue", padding=(0, 1)))


class ProgressPoll(Static):
    """A widget for choosing build progress evaluation."""

    class ProgressChoice(Message):
        """Message sent when user makes a progress choice."""

        def __init__(self, choice: int):
            self.choice = choice
            super().__init__()

    def __init__(self, prev_error: str, new_error: str):
        self.prev_error = prev_error
        self.new_error = new_error
        super().__init__()

    def compose(self) -> ComposeResult:
        """Create the progress poll layout."""
        content = Text()
        content.append("🔍 Build Progress Evaluation\n\n", style="bold blue")
        content.append("Previous error:\n", style="bold")
        content.append(f"{self.prev_error}\n\n", style="red")
        content.append("New error:\n", style="bold")
        content.append(f"{self.new_error}\n\n", style="red")
        content.append("Did we make progress?", style="bold")

        yield Static(Panel(content, border_style="yellow", padding=(1, 2)))

        with Horizontal(id="progress-buttons"):
            regress_btn = Button("❌ Regress", id="choice-1", variant="error")
            regress_btn.tooltip = "Build fails earlier"
            yield regress_btn

            eval_btn = Button("⚠️ Eval Error", id="choice-2", variant="warning")
            eval_btn.tooltip = "Code failed to evaluate"
            yield eval_btn

            progress_btn = Button("✅ Progress", id="choice-3", variant="success")
            progress_btn.tooltip = "Build fails later"
            yield progress_btn

            hash_btn = Button("🔧 Hash Mismatch", id="choice-4", variant="primary")
            hash_btn.tooltip = "Needs correct hash to be filled in"
            yield hash_btn

    @on(Button.Pressed)
    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle progress choice button press."""
        if event.button.id == "choice-1":
            choice = 1
        elif event.button.id == "choice-2":
            choice = 2
        elif event.button.id == "choice-3":
            choice = 3
        elif event.button.id == "choice-4":
            choice = 4
        else:
            return

        self.post_message(self.ProgressChoice(choice))


class ChatHistory(ScrollableContainer):
    """Container for chat messages with auto-scroll."""

    def add_message(self, content: str, sender: str):
        """Add a new message to the chat."""
        message = ChatMessage(content, sender)
        self.mount(message)
        # Auto-scroll to bottom
        self.scroll_end(animate=False)
        return message

    def add_streaming_message(self, initial_content: str, sender: str):
        """Add a new message that will be updated via streaming."""
        message = ChatMessage(initial_content, sender)
        self.mount(message)
        self.scroll_end(animate=False)
        return message

    def add_progress_poll(self, prev_error: str, new_error: str):
        """Add a progress evaluation poll to the chat."""
        poll = ProgressPoll(prev_error, new_error)
        self.mount(poll)
        self.scroll_end(animate=False)
        return poll


class ChatInput(Input):
    """Custom input widget for chat messages."""
    
    class MessageSent(Message):
        """Message sent when user submits input."""
        
        def __init__(self, content: str):
            self.content = content
            super().__init__()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle input submission."""
        if event.value.strip():
            self.post_message(self.MessageSent(event.value.strip()))
            self.clear()
            event.stop()
            event.prevent_default()



class APIKeyScreen(ModalScreen):
    """Modal screen for entering API keys."""
    
    CSS = """
    APIKeyScreen {
        align: center middle;
    }
    
    #api-dialog {
        width: 90;
        height: auto;
        min-height: 10;
        max-height: 20;
        border: thick $background 80%;
        background: $surface;
        padding: 1 2;
    }
    
    #api-title {
        text-align: center;
        margin-bottom: 1;
    }
    
    #api-input {
        margin: 1 0;
        width: 100%;
    }
    
    #api-buttons {
        height: 3;
        align: center middle;
        margin-top: 1;
    }
    
    #api-buttons Button {
        margin: 0 1;
    }
    
    """
    
    def __init__(self, key_name: str, description: str):
        super().__init__()
        self.key_name = key_name
        self.description = description
        
    def compose(self) -> ComposeResult:
        with Vertical(id="api-dialog"):
            yield Static(f"[bold]API Key Required: {self.key_name}[/bold]", id="api-title")
            yield Static(self.description)
            
            
            yield Input(placeholder="Enter your API key", id="api-input")
            yield Static("", id="api-preview")  # For showing the entered key
            with Horizontal(id="api-buttons"):
                yield Button("Cancel", variant="error", id="cancel")
                yield Button("Save", variant="primary", id="save")
    
    @on(Button.Pressed, "#cancel")
    def cancel_pressed(self) -> None:
        self.dismiss(None)
    
    @on(Button.Pressed, "#save")
    def save_pressed(self) -> None:
        input_widget = self.query_one("#api-input", Input)
        key_value = input_widget.value.strip()
        if key_value:
            self.dismiss(key_value)
        else:
            preview = self.query_one("#api-preview", Static)
            preview.update("[red]Please enter an API key[/red]")
    
    @on(Input.Changed)
    def on_input_changed(self, event: Input.Changed) -> None:
        """Update preview when input changes."""
        preview = self.query_one("#api-preview", Static)
        if event.value.strip():
            preview.update(f"[green]Entered: {event.value}[/green]")
        else:
            preview.update("")
    
    @on(Input.Submitted)
    def input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.dismiss(event.value.strip())


class LogWindow(Vertical):
    """A full-screen log window that displays captured logs."""
    
    def __init__(self):
        super().__init__()
        self.last_update_index = 0
        
    def compose(self) -> ComposeResult:
        """Create the log window layout."""
        yield Header()
        yield RichLog(highlight=True, markup=True, id="log-content")
        yield Static(
            "[dim]💡 Tip: Hold Shift and drag to select text, then use Ctrl+C to copy[/dim]",
            id="log-tip"
        )
        yield Footer()
        
    def update_logs(self):
        """Update the log widget with new log entries."""
        log_widget = self.query_one("#log-content", RichLog)
        logs = log_capture.get_logs()
        
        # If we're behind, clear and show all logs
        if self.last_update_index == 0 and logs:
            log_widget.clear()
            for log in logs:
                log_widget.write(log)
            self.last_update_index = len(logs)
        else:
            # Only add new logs since last update
            for log in logs[self.last_update_index:]:
                log_widget.write(log)
            self.last_update_index = len(logs)
        
    def clear_logs(self):
        """Clear all logs."""
        log_widget = self.query_one("#log-content", RichLog)
        log_widget.clear()
        log_capture.clear()
        self.last_update_index = 0


class PackagerixChatApp(App):
    """Main chat application with integrated packagerix functionality."""
    
    CSS = """
    Screen {
        layout: vertical;
    }

    #chat-container {
        height: 1fr;
        border: solid $primary;
        margin: 1;
    }

    #input-container {
        height: 4;
        margin: 0 1 2 1;
    }

    ChatInput {
        height: 3;
        margin: 1 0;
        border: none;
    }

    ChatHistory {
        height: 1fr;
        padding: 1;
    }

    ChatMessage {
        margin-bottom: 1;
    }

    ProgressPoll {
        margin-bottom: 1;
    }

    #progress-buttons {
        width: 100%;
        height: 3;
        align: center middle;
    }

    ProgressPoll Button {
        margin: 0 1;
        min-width: 20;
        height: 3;
        content-align: center middle;
        text-align: center;
        border: solid $accent;
        padding: 0 1;
        width: 1fr;
    }
    
    LogWindow {
        layer: logs;
        display: none;
    }
    
    LogWindow.-show-logs {
        display: block;
    }
    
    #log-tip {
        height: 1;
        text-align: center;
        background: $panel;
        padding: 0 1;
    }
    """
    
    BINDINGS = [
        ("ctrl+c", "quit", "Quit"),
        ("ctrl+d", "quit", "Quit"),
        ("ctrl+l", "toggle_logs", "Toggle Logs"),
    ]
    
    def __init__(self):
        super().__init__()
        self.current_project_url = None
        self.current_flake_dir = None
        self.packaging_state = "idle"  # idle, analyzing, building, complete
        self.log_window = None
        self.log_update_timer = None
        
    def compose(self) -> ComposeResult:
        """Create the chat interface layout."""
        yield Header(show_clock=True)
        
        with Vertical(id="chat-container"):
            yield ChatHistory(id="chat-history")
        
        with Vertical(id="input-container"):
            yield Static("Type your message and press Enter:")
            yield ChatInput(placeholder="Ask packagerix about packaging your project...", id="chat-input")
        
        self.log_window = LogWindow()
        yield self.log_window
        
        yield Footer()
    
    def on_mount(self) -> None:
        """Initialize the chat with a welcome message."""
        # Initialize packagerix config
        config.init()
        
        # Set UI mode
        from packagerix.main import set_ui_mode
        set_ui_mode(True)
        
        # Log startup
        logger.info("Packagerix Chat UI started")
        
        chat_history = self.query_one("#chat-history", ChatHistory)
        
        # Check if model is configured
        self.check_model_configuration()
        
        # Don't show welcome message here - coordinator will handle it
        # Disable input initially - coordinator will enable when needed
        chat_input = self.query_one("#chat-input", ChatInput)
        chat_input.disabled = True
    
    def check_model_configuration(self):
        """Show model configuration dialog on every launch."""
        # Always show dialog on launch
        from packagerix.ui.textual.model_config_dialog import ModelConfigDialog
        self.push_screen(ModelConfigDialog(), self.handle_model_config_result)
    
    def handle_model_config_result(self, result):
        """Handle result from model configuration dialog."""
        chat_history = self.query_one("#chat-history", ChatHistory)
        chat_history.add_message(
            f"✅ AI model configured: {result['model']} from {result['provider']}",
            "packagerix"
        )
        
        # Start the packaging flow after model is configured
        self.start_packaging_flow()
    
    @on(ChatInput.MessageSent)
    def handle_user_message(self, event: ChatInput.MessageSent) -> None:
        """Handle a new user message."""
        # Check if the coordinator is waiting for user input
        from packagerix.ui.conversation import get_ui_adapter
        adapter = get_ui_adapter()
        
        # Resolve the future with the user's input
        if hasattr(adapter, 'user_input_future') and adapter.user_input_future:
            adapter.user_input_future.set_result(event.content)
            # Disable input until next prompt
            self.query_one("#chat-input", ChatInput).disabled = True

    @on(ProgressPoll.ProgressChoice)
    def handle_progress_choice(self, event: ProgressPoll.ProgressChoice) -> None:
        """Handle progress evaluation choice."""
        # Check if we have a UI adapter waiting for progress response
        from packagerix.ui.conversation import get_ui_adapter
        adapter = get_ui_adapter()
        
        if hasattr(adapter, 'progress_response_event') and adapter.progress_response_event:
            # Set the choice in the response container and signal completion
            adapter.progress_response_container[0] = str(event.choice)
            adapter.progress_response_event.set()
            # Clear the event so it doesn't interfere with future calls
            adapter.progress_response_event = None
            adapter.progress_response_container = None
        else:
            # Fallback: show message in chat (legacy behavior)
            chat_history = self.query_one("#chat-history", ChatHistory)
            choices = {
                1: "❌ Regress (build fails earlier)",
                2: "⚠️ Eval Error (code failed to evaluate)",
                3: "✅ Progress (build fails later)",
                4: "🔧 Hash Mismatch (needs correct hash)"
            }
            choice_text = choices.get(event.choice, "Unknown choice")
            chat_history.add_message(f"Selected: {choice_text}", "user")
    
    @work(exclusive=False, thread=True)
    def start_packaging_flow(self) -> None:
        """Start the packaging flow with the coordinator."""
        # Set up the textual UI adapter
        from packagerix.ui.conversation import set_ui_adapter
        from packagerix.ui.textual.textual_ui_adapter import TextualUIAdapter
        from packagerix.packaging_flow.run import run_packaging_flow
        
        chat_history = self.query_one("#chat-history", ChatHistory)
        adapter = TextualUIAdapter(self.app, chat_history)
        set_ui_adapter(adapter)
        
        # Run the packaging flow
        try:
            run_packaging_flow()
        except Exception as e:
            self.call_from_thread(chat_history.add_message, f"❌ Error: {str(e)}", "packagerix")
    
    @work(exclusive=False, thread=True)
    def process_user_input(self, user_input: str) -> None:
        """Process user input - not used anymore as coordinator handles input."""
        pass
    
    def action_toggle_logs(self) -> None:
        """Toggle the log window display."""
        self.log_window.toggle_class("-show-logs")
        # Update logs when showing
        if self.log_window.has_class("-show-logs"):
            self.log_window.update_logs()
            # Start periodic updates
            if not self.log_update_timer:
                self.log_update_timer = self.set_interval(0.5, self.update_logs)
        else:
            # Stop periodic updates
            if self.log_update_timer:
                self.log_update_timer.stop()
                self.log_update_timer = None
    
    def update_logs(self) -> None:
        """Periodically update the log window with new logs."""
        if self.log_window and self.log_window.has_class("-show-logs"):
            self.log_window.update_logs()
    
    def _handle_api_key_result(self, key_name: str, key_value: str, url: str, chat_history: ChatHistory) -> None:
        """Handle the result from the API key dialog."""
        if key_value:
            # Save the key
            from packagerix.secure_keys import set_api_key
            set_api_key(key_name, key_value)
            os.environ[key_name] = key_value
            logger.info(f"API key {key_name} saved and set in environment")
            
            # Retry the operation
            self.process_user_input(url)
        else:
            chat_history.add_message(
                f"❌ API key required but not provided. Please provide your {key_name} to continue.",
                "packagerix"
            )


def main():
    """Main entry point for the chat UI."""
    app = PackagerixChatApp()
    app.run()


if __name__ == "__main__":
    main()
