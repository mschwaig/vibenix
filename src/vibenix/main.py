#!/usr/bin/env python3
"""Vibenix - AI-powered Nix package builder.

Main entry point that supports both terminal and textual UI modes.
"""

import argparse
import os
import sys
from pydantic import BaseModel

from vibenix.ui.logging_config import logger  # Import logger first to ensure it's initialized
from vibenix import config
from typing import Optional
from functools import wraps
import hashlib
import json

config.init()

from vibenix.parsing import cache

# TODO: Configure pydantic-ai model
logger.info("Using pydantic-ai for model integration")

# Global variable to track if we're in UI mode
_ui_mode = False

def set_ui_mode(ui_mode: bool):
    """Set whether we're running in UI mode."""
    global _ui_mode
    _ui_mode = ui_mode

import logging
if "OLLAMA_HOST" in os.environ:
    logger.info(f"OLLAMA_HOST is set to: {os.environ['OLLAMA_HOST']}")
else:
    logger.warning("OLLAMA_HOST environment variable is not set")

# TODO: Configure pydantic-ai logging if needed
if "OLLAMA_HOST" in os.environ:
    logger.info(f"OLLAMA_HOST available at: {os.environ['OLLAMA_HOST']}")
    logger.info("Note: api_base will be set per-model, not globally")

def cache_streaming_response(func):
    """Decorator that caches streaming responses.
    
    For cached results, yields the cached string as a single chunk.
    For new results, streams the response and caches the complete result.
    """
    @wraps(func)
    def wrapper(*args, **kwargs):
        # Create a cache key from function name and arguments
        # Convert args to a hashable format
        key_data = {
            'func_name': func.__name__,
            'args': args,
            'kwargs': kwargs
        }
        key_str = json.dumps(key_data, sort_keys=True, default=str)
        cache_key = hashlib.md5(key_str.encode()).hexdigest()
        
        # Check if we have a cached result
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            logger.info(f"Using cached result for {func.__name__}")
            # Return a generator that yields the cached result
            def cached_generator():
                yield cached_result
            return cached_generator()
        
        # No cached result, call the original function
        logger.info(f"No cached result, calling {func.__name__}")
        stream = func(*args, **kwargs)
        
        # Collect the stream and yield chunks
        chunks = []
        def caching_generator():
            for chunk in stream:
                chunks.append(chunk)
                yield chunk
            
            # After streaming is complete, cache the full result
            full_result = ''.join(chunks)
            cache.set(cache_key, full_result)
            logger.info(f"Cached result for {func.__name__}")
        
        return caching_generator()
    
    return wrapper

class Project(BaseModel):
    name: str
    latest_commit_sha1: str
    version_tag : Optional[str]
    dependencies: list[str]

def mock_input (ask : str, reply: str):
    logger.info(ask)
    logger.info(reply + "\n")
    return reply

def run_terminal_ui(output_dir=None, project_url=None, revision=None, fetcher=None):
    """Run the terminal-based interface."""
    from vibenix.ui.logging_config import enable_console_logging
    enable_console_logging()
    
    set_ui_mode(False)
    
    # Use the coordinator pattern for CLI
    from vibenix.ui.conversation import set_ui_adapter, TerminalUIAdapter
    from vibenix.packaging_flow.run import run_packaging_flow
    # TODO: Use hardcoded model configuration
    
    # Set up terminal UI adapter
    set_ui_adapter(TerminalUIAdapter())
    
    # If project URL is provided, skip interactive configuration
    if project_url:
        from vibenix.model_config import load_saved_configuration, initialize_model_config
        from vibenix.ui.raw_terminal.terminal_vibenix_settings import ensure_settings_configured

        saved_config = load_saved_configuration() # Model configuration (provider, model_name)

        if not saved_config:
            logger.error("No saved model configuration found. Please run interactively first to configure.")
            sys.exit(1)

        ensure_settings_configured() # Vibenix settings

        # Initialize and log model configuration once
        initialize_model_config()
    else:
        # Interactive mode - prompt for configuration if needed
        from vibenix.ui.raw_terminal.terminal_model_config import ensure_model_configured
        from vibenix.ui.raw_terminal.terminal_vibenix_settings import ensure_settings_configured
        
        if not ensure_model_configured():
            logger.error("Model configuration required to continue")
            sys.exit(1)
        
        # Ensure settings are configured (loads existing or prompts user)
        ensure_settings_configured()
        
        # Initialize after configuration is complete
        from vibenix.model_config import initialize_model_config
        initialize_model_config()
    
    # Run the packaging flow in background thread (like textual UI)
    import threading
    
    def run_coordinator():
        try:
            run_packaging_flow(output_dir=output_dir, project_url=project_url,
                               revision=revision, fetcher=fetcher)
        except Exception as e:
            logger.error(f"Error in packaging flow: {e}")
            import traceback
            traceback.print_exc()
    
    coordinator_thread = threading.Thread(target=run_coordinator)
    coordinator_thread.daemon = True
    coordinator_thread.start()
    
    # Wait for completion
    coordinator_thread.join()


def run_textual_ui():
    """Run the textual-based interface."""
    from vibenix.ui.textual.textual_ui import VibenixChatApp
    app = VibenixChatApp()
    app.run()

def main():
    """Main entry point for vibenix."""
    # Set up signal handlers early, while we're in the main thread
    from vibenix.ccl_log_safe import setup_safe_logging
    setup_safe_logging()
    
    parser = argparse.ArgumentParser(
        description="Vibenix - AI-powered Nix package builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  vibenix                                         # Run with interactive textual UI
  vibenix --raw                                   # Run with terminal-only interface
  vibenix --raw https://github.com/user/repo      # Package a specific repo
  vibenix --raw --output-dir out https://github.com/user/repo  # Save output
  vibenix --configure-settings                    # Configure vibenix settings
  vibenix --help                                  # Show this help
"""
    )
    
    parser.add_argument(
        "--raw",
        action="store_true",
        help="Use terminal-only interface instead of textual UI"
    )
    
    parser.add_argument(
        "--configure-vibenix",
        action="store_true",
        help="Configure vibenix settings (tools, prompts, behaviour)"
    )
    
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory to save successful package.nix files (only works with --raw)"
    )
    
    parser.add_argument(
        "project_url",
        nargs="?",
        help="GitHub project URL to package (only works with --raw)"
    )

    parser.add_argument(
        "revision",
        type=str,
        nargs="?",
        help="Project revision to package (e.g., commit hash, tag, release name) (optional, only works with --raw)."
    )

    parser.add_argument(
        "--fetcher",
        default=None,
        help="Path to .nix file with fetcher for the project source code (only works with --raw)."
    )
    
    parser.add_argument(
        "--version",
        action="version",
        version="vibenix 0.1.0"
    )
    
    args = parser.parse_args()
    
    try:
        if args.configure_vibenix:
            logger.info("Starting vibenix settings configuration")
            from vibenix.ui.raw_terminal.terminal_vibenix_settings import show_vibenix_settings_terminal
            show_vibenix_settings_terminal()
            sys.exit(0)
        
        if args.raw:
            logger.info("Starting vibenix in terminal mode")
            if args.output_dir and not args.project_url:
                parser.error("--output-dir requires a project URL to be provided")
            run_terminal_ui(output_dir=args.output_dir, project_url=args.project_url,
                            revision=args.revision, fetcher=args.fetcher)
        else:
            if args.output_dir:
                parser.error("--output-dir only works with --raw mode")
            if args.project_url:
                parser.error("project URL argument only works with --raw mode")
            logger.info("Starting vibenix in textual UI mode")
            run_textual_ui()
    except KeyboardInterrupt:
        logger.info("\nExiting...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
