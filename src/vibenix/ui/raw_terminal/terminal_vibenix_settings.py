"""Terminal-based vibenix settings configuration."""

import os
import json
from typing import Optional, Dict, Any, List, Set
from pathlib import Path

from vibenix.defaults.vibenix_settings import (
    get_settings_manager,
    load_settings,
    DEFAULT_VIBENIX_SETTINGS,
    ADDITIONAL_TOOLS,
    PROJECT_TOOLS,
    NIXPKGS_TOOLS,
    SEARCH_TOOLS,
    EDIT_TOOLS,
    ALL_TOOLS,
    settings_from_json_format
)
from vibenix.ui.logging_config import logger


def get_settings_file_path() -> Path:
    """Get the path to the settings file."""
    return Path.home() / ".vibenix" / "vibenix_settings.json"


def load_saved_settings() -> Optional[Dict[str, Any]]:
    """Load saved settings from file."""
    settings_path = get_settings_file_path()
    
    if not settings_path.exists():
        return None
    
    try:
        with open(settings_path, 'r') as f:
            json_settings = json.load(f)
        # Convert from JSON format (names) to Python format (function objects)
        return settings_from_json_format(json_settings)
    except Exception as e:
        logger.warning(f"Failed to load settings: {e}")
        return None


def save_settings_to_file():
    """Save settings to file."""
    try:
        settings_path = os.path.expanduser("~/.vibenix/vibenix_settings.json")
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)

        get_settings_manager().save_settings(settings_path)
        
        logger.info(f"Settings saved to {settings_path}")
        print(f"\n✅ Settings saved to {settings_path}")
    except Exception as e:
        logger.error(f"Failed to save settings: {e}")
        print(f"\n❌ Failed to save settings: {e}")


def show_tool_list(tools: List, disabled_tools: Set[str], title: str):
    """Display a numbered list of tools with their status."""
    print(f"\n{title}")
    print("=" * 60)
    
    for i, tool in enumerate(tools, 1):
        tool_name = tool.__name__ if callable(tool) else tool
        status = "🔴 DISABLED" if tool_name in disabled_tools else "🟢 ENABLED"
        print(f"{i:2}. {status:12} {tool_name}")


def toggle_tools_menu() -> None:
    """Interactive menu for toggling individual tools on/off."""
    all_tools = ALL_TOOLS.copy()
    
    while True:
        # Get currently disabled tools from settings (it's a list of tool names)
        disabled_tools = get_settings_manager().get_disabled_tools()
        disabled_tools = set(disabled_tools)
        print("\n" + "=" * 60)
        print("🔧 TOGGLE INDIVIDUAL TOOLS")
        print("=" * 60)
        
        # Show current disabled tools summary
        if disabled_tools:
            print(f"\n🔴 Currently disabled: {', '.join(sorted(disabled_tools))}")
        else:
            print("\n🟢 All tools are currently enabled")
        
        show_tool_list(all_tools, disabled_tools, "\nAvailable Tools:")
        
        print("\n" + "-" * 60)
        print("Enter tool number to toggle, 'c' to clear all, or 'q' to finish")
        
        choice = input("\nYour choice: ").strip().lower()
        
        if choice == 'q':
            # Update settings with disabled tools as a list
            return
        
        elif choice == 'c':
            get_settings_manager().set_disabled_tools([])
            print("\n✅ All tools enabled")
            continue
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(all_tools):
                tool = all_tools[idx]
                tool_name = tool.__name__ if callable(tool) else tool
                
                get_settings_manager().toggle_disabled_tools(tool_name)
            else:
                print(f"\n❌ Please enter a number between 1 and {len(all_tools)}")
        except ValueError:
            print("\n❌ Please enter a valid number, 'c', or 'q'")


def configure_prompt_tools_menu() -> None:
    """Interactive menu for configuring tools for each prompt."""
    prompt_names = sorted(get_settings_manager().list_all_prompts())

    while True:
        print("\n" + "=" * 60)
        print("📋 CONFIGURE PROMPT TOOLS")
        print("=" * 60)
        print("\nSelect a prompt to configure its tools:")
        
        for i, prompt_name in enumerate(prompt_names, 1):
            tools = get_settings_manager().get_prompt_tools(prompt_name)
            tool_count = len(tools) if isinstance(tools, list) else 0
            print(f"{i:2}. {prompt_name:30} ({tool_count} tools)")
        
        print("\n" + "-" * 60)
        print("Enter prompt number to configure, or 'q' to finish")
        
        choice = input("\nYour choice: ").strip().lower()
        
        if choice == 'q':
            return
        
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(prompt_names):
                prompt_name = prompt_names[idx]
                configure_single_prompt_tools(prompt_name)
            else:
                print(f"\n❌ Please enter a number between 1 and {len(prompt_names)}")
        except ValueError:
            print("\n❌ Please enter a valid number or 'q'")


def configure_single_prompt_tools(prompt_name: str) -> None:
    """Configure tools for a single prompt."""
    selected_tools = set()
    selected_additional_tools = set()
    
    selected_tools = set(get_settings_manager().get_prompt_tools(prompt_name))
    all_tools = ALL_TOOLS.copy()
    
    while True:
        print("\n" + "=" * 60)
        print(f"🎯 CONFIGURE TOOLS FOR: {prompt_name}")
        print("=" * 60)
        
        # Show current selection summary
        if selected_tools:
            print(f"\n✅ Selected tools ({len(selected_tools)}): {', '.join(sorted(selected_tools))}")
        else:
            print("\n⚠️  No tools selected (prompt will have no tools)")
        
        print(f"\n📦 Available Tools:")
        for i, tool_name in enumerate(all_tools, 1):
            status = "✅ SELECTED" if tool_name in selected_tools else "  "
            print(f"{i:2}. {status:12} {tool_name}")
        
        print("\n" + "-" * 60)
        print("Commands:")
        print("  [number]      - Toggle tool selection")
        print("  'search'      - Select all SEARCH_TOOLS")
        print("  'edit'        - Select all EDIT_TOOLS")
        print("  'project'     - Select all project-specific tools")
        print("  'nixpkgs'     - Select all nixpkgs-specific tools")
        print("  'all'         - Select all tools")
        print("  'none'        - Deselect all tools")
        print("  'q'           - Finish and save")
        
        choice = input("\nYour choice: ").strip().lower()
        
        if choice == 'q':
            get_settings_manager().set_prompt_tools(prompt_name, selected_tools)
            return
        
        elif choice == 'search':
            for tool in SEARCH_TOOLS:
                selected_tools.add(tool.__name__)
            print("\n✅ Added all SEARCH_TOOLS")
        
        elif choice == 'edit':
            for tool in EDIT_TOOLS:
                selected_tools.add(tool.__name__)
            print("\n✅ Added all EDIT_TOOLS")

        elif choice == 'project':
            for tool_name in PROJECT_TOOLS:
                selected_tools.add(tool_name)
            print("\n✅ Added all project-specific tools")

        elif choice == 'nixpkgs':
            for tool_name in NIXPKGS_TOOLS:
                selected_tools.add(tool_name)
            print("\n✅ Added all nixpkgs-specific tools")
        
        elif choice == 'all':
            for tool_name in all_tools:
                selected_tools.add(tool_name)
            print("\n✅ Selected all tools")
        
        elif choice == 'none':
            selected_tools.clear()
            print("\n✅ Deselected all tools")
        
        else:
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(all_tools):
                    tool_name = all_tools[idx]
                    
                    if tool_name in selected_tools:
                        selected_tools.remove(tool_name)
                        print(f"\n❌ Deselected: {tool_name}")
                    else:
                        selected_tools.add(tool_name)
                        print(f"\n✅ Selected: {tool_name}")
                else:
                    print(f"\n❌ Please enter a number between 1 and {len(all_tools)}")
            except ValueError:
                print("\n❌ Invalid input. Enter a number, command, or 'q'")


def configure_general_behaviour_menu() -> None:
    """Interactive menu for configuring general behaviour settings."""
    
    while True:
        print("\n" + "=" * 60)
        print("⚙️  GENERAL BEHAVIOUR SETTINGS")
        print("=" * 60)

        settings = get_settings_manager().list_all_behaviour_settings()
        behaviour_keys = []
        for setting_name in settings: # filter for settings that can be toggled
            try:
                get_settings_manager().get_setting_enabled(setting_name)
                behaviour_keys.append(setting_name)
            except Exception:
                pass

        for i in range(1, len(behaviour_keys)+1):
            setting_name = behaviour_keys[i-1]
            enabled = get_settings_manager().get_setting_enabled(setting_name)
            status = "🟢 ENABLED" if enabled else "🔴 DISABLED"
            print(f"{i}. {setting_name.replace("_", " ").capitalize():30} {status}")
        
        print("\n" + "-" * 60)
        print("Enter setting number to toggle, or 'q' to finish")
        
        choice = input("\nYour choice: ").strip().lower()
        
        if choice == 'q':
            return
        
        if choice.isdigit() and 1 <= int(choice) <= len(behaviour_keys):
            idx = int(choice) - 1
            setting_name = behaviour_keys[idx]
            enabled = get_settings_manager().get_setting_enabled(setting_name)
            new_value = not enabled
            print(f"Setting '{setting_name}' from {enabled} to {new_value}")
            get_settings_manager().set_setting_enabled(setting_name, new_value)
            status = "🟢 ENABLED" if new_value else "🔴 DISABLED"
            print(f"\n✅ {setting_name.replace("_", " ").capitalize():30} is now {status}")
        else:
            print("\n❌ Please make a choice between 'q' and 1-4")


def show_settings_main_menu():
    """Show the main settings configuration menu."""
    print("\n" + "=" * 60)
    print("🎛️  VIBENIX SETTINGS CONFIGURATION")
    print("=" * 60)
    print("\n1. Toggle individual tools (enable/disable globally)")
    print("2. Configure tools per prompt")
    print("3. General behavior settings")
    print("4. Reset to defaults")
    print("5. View current settings")
    print("6. Save and exit")
    print("7. Exit without saving")


def view_current_settings():
    """Display the current settings."""
    print("\n" + "=" * 60)
    print("📊 CURRENT SETTINGS")
    print("=" * 60)
    
    # Show disabled tools
    disabled_tools = get_settings_manager().get_disabled_tools()
    
    if len(disabled_tools) > 0:
        print(f"\n🔴 Disabled tools ({len(disabled_tools)}):")
        for tool in sorted(disabled_tools):
            print(f"   - {tool}")
    else:
        print("\n🟢 All tools enabled globally")
    
    # Show prompt tools summary
    prompts = get_settings_manager().list_all_prompts()
    print(f"\n📋 Prompt configurations ({len(prompts)} prompts):")
    for prompt_name in sorted(prompts):
        tools = get_settings_manager().get_prompt_tools(prompt_name)
        tool_count = len(tools) if isinstance(tools, list) else 0
        print(f"   {prompt_name:30} - {tool_count} tools")
    
    print("\n⚙️  General behaviour settings:")
    # Show general settings
    settings = get_settings_manager().list_all_behaviour_settings()
    behaviour_keys = []
    for setting_name in settings: # filter for settings that can be toggled
        try:
            get_settings_manager().get_setting_enabled(setting_name)
            behaviour_keys.append(setting_name)
        except Exception:
            pass

    for setting_name in behaviour_keys:
        enabled = get_settings_manager().get_setting_enabled(setting_name)
        print(f"   {setting_name.replace("_", " ").capitalize():30} - {'🟢 Enabled' if enabled else '🔴 Disabled'}")
    
    input("\nPress Enter to continue...")


def show_vibenix_settings_terminal() -> bool:
    """Show terminal-based vibenix settings configuration.
    
    Returns:
        True if settings were configured and saved, False if cancelled
    """
    print("\n🎛️  Configure Vibenix Settings")
    print("=" * 60)
    
    # Load existing settings or use defaults
    saved_settings = load_saved_settings()
    
    if saved_settings:
        print("\n✅ Found existing settings")
        use_existing = input("Load existing settings? (Y/n): ").strip().lower()
        if use_existing != 'n':
            settings = saved_settings
        else:
            settings = DEFAULT_VIBENIX_SETTINGS.copy()
    else:
        print("\n📝 No existing settings found. Using defaults.")
        settings = DEFAULT_VIBENIX_SETTINGS.copy()

    load_settings(settings)
    
    # Main configuration loop
    while True:
        show_settings_main_menu()
        
        choice = input("\nSelect option (1-7): ").strip()
        
        if choice == '1':
            toggle_tools_menu()
        
        elif choice == '2':
            configure_prompt_tools_menu()
        
        elif choice == '3':
            configure_general_behaviour_menu()
        
        elif choice == '4':
            confirm = input("\n⚠️  Reset all settings to defaults? (y/N): ").strip().lower()
            if confirm == 'y':
                load_settings(DEFAULT_VIBENIX_SETTINGS.copy())
                print("\n✅ Settings reset to defaults")
        
        elif choice == '5':
            view_current_settings()
        
        elif choice == '6':
            # Save and exit
            save_settings_to_file()
            print("\n✅ Settings saved and applied!")
            return True
        
        elif choice == '7' or choice == 'q':
            # Exit without saving
            confirm = input("\n⚠️  Exit without saving changes? (y/N): ").strip().lower()
            if confirm == 'y':
                print("\n❌ Settings not saved")
                return False
        
        else:
            print("\n❌ Please enter a number between 1 and 7")


def ensure_settings_configured() -> None:
    """Ensure settings are configured for terminal mode.
    
    Returns:
        True if settings are configured, False if user cancelled
    """
    # Try to load saved settings
    saved_settings = load_saved_settings()
    
    if saved_settings:
        logger.info("Loaded saved vibenix settings from file")
        load_settings(saved_settings)
    else:
        logger.info("No saved vibenix settings found. Using defaults.")
        # If no settings exist, use defaults
        load_settings(DEFAULT_VIBENIX_SETTINGS.copy())


if __name__ == "__main__":
    # Allow running this module directly for testing
    show_vibenix_settings_terminal()
