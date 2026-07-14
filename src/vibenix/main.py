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
import csv

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

def run_terminal_ui(maintenance=None, output_dir=None, project_url=None, revision=None, fetcher=None,
                    csv_pname=None, csv_version=None, fetcher_content=None,
                    target_version=None, update_lock=False, upgrade_lock=False):
    """Run the terminal-based interface.

    Args:
        maintenance: Directory containing flake.nix and package.nix for maintenance mode
        output_dir: Directory to save successful package.nix files
        project_url: GitHub project URL to package (URL-based mode)
        revision: Project revision (commit hash, tag, or release name)
        fetcher: Path to .nix file with fetcher for the project source
        csv_pname: Package name from CSV format (CSV-based mode)
        csv_version: Package version from CSV format (CSV-based mode)
        fetcher_content: Direct fetcher content (alternative to fetcher file path)
        revision: Specific revision to update to (maintenance mode) (e.g., commit hash, tag, release name) (default: latest)
        target_version: Updated version value (maintenance mode) (default: target_revision)
        update_lock: Whether to update flake.lock (maintenance mode)
        upgrade_lock: Whether to upgrade nixpkgs (maintenance mode)
    """
    from vibenix.ui.logging_config import enable_console_logging
    enable_console_logging()

    set_ui_mode(False)

    # Use the coordinator pattern for CLI
    from vibenix.ui.conversation import set_ui_adapter, TerminalUIAdapter
    from vibenix.packaging_flow.run import run_packaging_flow

    # Set up terminal UI adapter
    set_ui_adapter(TerminalUIAdapter())

    # If project URL, Nix fetcher, or CSV mode are provided, skip interactive configuration
    if (project_url or fetcher or csv_pname or maintenance):
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
            if maintenance:
                from vibenix.packaging_flow.maintenance import run_maintenance
                run_maintenance(maintenance, output_dir=output_dir, revision=revision,
                               version=target_version, update_lock=update_lock, upgrade_lock=upgrade_lock)
            else:
                run_packaging_flow(output_dir=output_dir, project_url=project_url,
                               revision=revision, fetcher=fetcher,
                               csv_pname=csv_pname, csv_version=csv_version,
                               fetcher_content=fetcher_content)
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
  vibenix                                         # Run with interactive terminal UI (default)
  vibenix --textual                               # Run with textual UI (unmaintained)
  vibenix https://github.com/user/repo            # Package a specific repo
  vibenix --output-dir out https://github.com/user/repo  # Save output
  vibenix --configure-settings                    # Configure vibenix settings
  vibenix --help                                  # Show this help
"""
    )

    parser.add_argument(
        "--textual",
        action="store_true",
        help="Use textual UI instead of terminal interface (unmaintained)"
    )

    parser.add_argument(
        "--raw",
        action="store_true",
        help="Deprecated: terminal mode is now the default (this flag does nothing)"
    )

    parser.add_argument(
        "--configure-vibenix",
        action="store_true",
        help="Configure vibenix settings (tools, prompts, behaviour)"
    )
    
    parser.add_argument(
        "--output-dir",
        type=str,
        help="Directory to save successful package.nix files"
    )

    parser.add_argument(
        "project_url",
        default=None,
        nargs="?",
        help="GitHub project URL to package"
    )

    parser.add_argument(
        "--revision", "-r",
        dest="revision",
        type=str,
        nargs="?",
        help="Project revision to package (e.g., commit hash, tag, release name)."
    )

    parser.add_argument(
        "--fetcher",
        default=None,
        help="Path to .nix file with fetcher for the project source code."
    )

    parser.add_argument(
        "--csv-dataset",
        type=str,
        default=None,
        metavar="CSV_FILE",
        help="Path to CSV dataset file containing package info. Requires --csv-package."
    )

    parser.add_argument(
        "--csv-package",
        type=str,
        default=None,
        metavar="PACKAGE_NAME",
        help="Package name to look up in the CSV dataset. Requires --csv-dataset."
    )

    parser.add_argument(
        "--nixpkgs-commit",
        type=str,
        default=None,
        help="Nixpkgs commit hash to use for building. Overrides value from CSV if provided."
    )

    parser.add_argument(
        "--maintenance",
        type=str,
        default=None,
        metavar="MAINTENANCE_DIR",
        help="Directory containing flake.nix and package.nix (flake.lock optional) for maintenance mode. Requires --raw. Incompatible with --csv-dataset, --csv-package, and --fetcher."
    )
    
    parser.add_argument(
        "--update-lock",
        type=bool,
        default=False,
        metavar="UPDATE_LOCK",
        help="Whether to update the flake.lock file if present during maintenance mode. Requires --raw. Default: False"
    )

    parser.add_argument(
        "--upgrade-lock",
        type=bool,
        default=False,
        metavar="UPGRADE_LOCK",
        help="Whether to upgrade/bump the nixpkgs release used during maintenance mode. Requires --raw. Default: False"
    )

    parser.add_argument(
        "--target-version",
        type=str,
        default=None,
        metavar="TARGET_VERSION",
        help="Optional target version to set in package.nix . Requires --maintenance, --revision. Default: --revision value."
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

        # Validate maintenance mode constraints
        if args.maintenance:
            # Maintenance mode requires --raw
            if not args.raw:
                parser.error("--maintenance requires --raw to be set")
            
            # Maintenance mode is incompatible with project_url
            if args.project_url:
                parser.error("--maintenance is incompatible with project_url argument")
            
            # Maintenance mode is incompatible with CSV dataset mode
            if args.csv_dataset or args.csv_package or args.fetcher:
                parser.error("--maintenance is incompatible with --csv-dataset, --csv-package and --fetcher arguments")

        if args.textual:
            # Textual UI mode (unmaintained)
            # Check for incompatible arguments
            if args.output_dir:
                parser.error("--output-dir only works with terminal mode (default)")
            if args.project_url:
                parser.error("project URL argument only works with terminal mode (default)")
            if args.revision:
                parser.error("revision argument only works with terminal mode (default)")
            if args.fetcher:
                parser.error("--fetcher only works with terminal mode (default)")
            if args.csv_dataset:
                parser.error("--csv-dataset only works with terminal mode (default)")
            if args.csv_package:
                parser.error("--csv-package only works with terminal mode (default)")
            if args.nixpkgs_commit:
                parser.error("--nixpkgs-commit only works with terminal mode (default)")
            logger.info("Starting vibenix in textual UI mode (unmaintained)")
            run_textual_ui()
        else:
            # Terminal mode is now the default
            logger.info("Starting vibenix in terminal mode")

            # Handle CSV dataset mode if provided
            csv_pname = None
            csv_version = None
            fetcher_content = None

            if args.csv_dataset or args.csv_package:
                # Both flags must be provided together
                if not args.csv_dataset or not args.csv_package:
                    parser.error("--csv-dataset and --csv-package must be used together")

                # Parse CSV dataset and extract package info
                logger.info(f"Loading package '{args.csv_package}' from CSV dataset: {args.csv_dataset}")

                with open(args.csv_dataset, 'r') as f:
                    reader = csv.DictReader(f)
                    package_found = False

                    for row in reader:
                        if row['package_name'] == args.csv_package:
                            package_found = True
                            csv_pname = row['pname']
                            csv_version = row['version']
                            fetcher_content = row['fetcher']

                            # Set nixpkgs commit from CSV if not overridden by CLI
                            if not args.nixpkgs_commit:
                                config.nixpkgs_commit = row['nixpkgs_target_bump']

                            logger.info(f"Found package: pname={csv_pname}, version={csv_version}")
                            logger.info(f"Using nixpkgs commit: {config.nixpkgs_commit}")
                            break

                    if not package_found:
                        logger.error(f"Package '{args.csv_package}' not found in CSV dataset")
                        sys.exit(1)

            # Set nixpkgs commit if provided via CLI (overrides CSV value)
            if args.nixpkgs_commit:
                config.nixpkgs_commit = args.nixpkgs_commit

            # Validate output-dir requires some input mode
            if args.output_dir and not (args.project_url or args.fetcher or csv_pname or args.maintenance):
                parser.error("--output-dir requires a project URL, Nix fetcher, or CSV dataset to be provided")

            run_terminal_ui(maintenance=args.maintenance, output_dir=args.output_dir, project_url=args.project_url,
                            revision=args.revision, fetcher=args.fetcher,
                            csv_pname=csv_pname, csv_version=csv_version,
                            fetcher_content=fetcher_content,
                            target_version=args.target_version,
                            update_lock=args.update_lock, upgrade_lock=args.upgrade_lock)
    except KeyboardInterrupt:
        logger.info("\nExiting...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
