"""
Signal-safe CCL logging implementation.
"""

import signal
import atexit
import threading
import sys
import traceback
from typing import Optional

from .ccl_log import get_logger, close_logger


def ensure_log_cleanup(signal_name: Optional[str] = None):
    """Ensure log is properly closed on exit.
    
    Args:
        signal_name: Name of the signal that caused termination, or None for normal exit
    """
    try:
        logger = get_logger()
        # Log session end with signal information
        from vibenix.ui.conversation_templated import get_model_prompt_manager
        logger.log_clear_path()
        logger.log_total_tool_cost()
        logger.log_session_end(
            signal=signal_name,
            total_cost=get_model_prompt_manager().get_session_cost()
        )
        close_logger()
    except Exception:
        # Ignore errors during cleanup
        pass


def handle_uncaught_exception(exc_type, exc_value, exc_traceback):
    """Handle uncaught exceptions by logging them before exit."""
    # Don't handle KeyboardInterrupt (let it be handled by signal handler)
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    
    try:
        logger = get_logger()
        # Format the exception
        exception_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        # Log the exception
        logger.log_exception(exception_str)
        # Log session end with signal indicating exception
        # TODO: Get usage from pydantic-ai
        logger.log_session_end(
            signal="EXCEPTION",
            total_cost=None  # TODO: Get from pydantic-ai usage tracking
        )
        close_logger()
    except Exception:
        # If logging fails, at least try to print the exception
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
    
    # Exit with error code
    import os
    os._exit(1)


def setup_safe_logging():
    """Set up signal handlers to ensure logs are written on termination."""
    # Register cleanup function for normal exit
    atexit.register(ensure_log_cleanup)
    
    # Set up global exception handler
    sys.excepthook = handle_uncaught_exception
    
    # Handle common termination signals
    def signal_handler(signum, frame):
        # Convert signal number to name
        signal_name = signal.Signals(signum).name
        ensure_log_cleanup(signal_name)
        # Re-raise the signal to allow normal termination
        signal.signal(signum, signal.SIG_DFL)
        signal.raise_signal(signum)
    
    # Register handlers for common termination signals
    for sig in [signal.SIGTERM, signal.SIGINT]:
        try:
            signal.signal(sig, signal_handler)
        except OSError:
            # Some signals may not be available on all platforms
            pass
    
    # Set up timeout (default 1800 seconds = 30 minutes, configurable via env var)
    import os
    timeout_seconds = int(os.environ.get('VIBENIX_TIMEOUT_SECONDS', '1800'))
    
    if timeout_seconds > 0:
        def timeout_handler():
            """Handler for the timeout."""
            ensure_log_cleanup("TIMEOUT")
            # Force exit the entire process immediately
            import os
            os._exit(124)  # Exit code 124 is commonly used for timeouts
        
        # Start the timeout timer
        timeout_timer = threading.Timer(float(timeout_seconds), timeout_handler)
        timeout_timer.daemon = True  # Ensure it doesn't prevent program exit
        timeout_timer.start()
        
        # Store the timer reference so it can be cancelled if needed
        import builtins
        if not hasattr(builtins, '_vibenix_timeout_timer'):
            builtins._vibenix_timeout_timer = timeout_timer
        
        # Log that timeout is set
        try:
            from vibenix.ui.logging_config import logger
            logger.info(f"Timeout set to {timeout_seconds} seconds")
        except:
            pass