"""Vibenix settings manager for controlling prompt tools and behavior."""

from typing import Dict, List, Callable, Optional, Any, Union
from vibenix.tools import (
    SEARCH_FUNCTIONS,
    EDIT_FUNCTIONS,
    ALL_FUNCTIONS,
    search_nixpkgs_for_package_semantic,
    search_nixpkgs_for_package_literal,
    search_nix_functions,
    search_nixpkgs_for_file,
    search_nixpkgs_manual_documentation,
    str_replace,
    insert_line_after,
    view,
    error_pagination,
)


# Default mapping of prompts to their tools
DEFAULT_PROMPT_TOOLS: Dict[str, List[Callable]] = {
    'pick_template': [],
    'summarize_github': [],
    'evaluate_code': [],
    'get_feedback': SEARCH_FUNCTIONS,
    'refine_code': ALL_FUNCTIONS,
    'fix_build_error': ALL_FUNCTIONS,
    'fix_hash_mismatch': EDIT_FUNCTIONS,
    'classify_packaging_failure': [],
    'analyze_package_failure': SEARCH_FUNCTIONS,
    'summarize_build': [],
    'choose_builders': [],
    'compare_template_builders': [search_nix_functions, search_nixpkgs_manual_documentation]+EDIT_FUNCTIONS,
}


DEFAULT_VIBENIX_SETTINGS = {
    # Individual tool toggles (disable specific tools globally)
    "tools": [
        # Empty: All tools enabled by default
    ],
    
    # Per-prompt tool configuration
    # Values are lists of function objects
    "prompt_tools": DEFAULT_PROMPT_TOOLS.copy(),

    # Toggle additional tools on or off TODO 1
    
    # General behavior, misc
    "behaviour": {
        # Enable or disable progress_evaluation
        "progress_evaluation_enabled": True,
        "build_summary_enabled": True,
        "compare_template_builders_enabled": True,
        # Enable or disable edit tools
        "edit_tools": {
            "enabled": True, # DONE but to improve TODO 2
            # Snippets to add to prompts whether edit tools are enabled or not
            "snippets": [ # DONE but to improve TODO 3
                "To perform each change to the code, use the text editor tools: [<TOOLS>].",
                "Your response should contain the full updated packaging code, wrapped like so:\n```nix\n...\n```."
            ]
        },
    }
}
# TODO create empty things if not present in config

class VibenixSettingsManager:
    """Manages vibenix settings and resolves prompt tools."""
    
    def __init__(self, settings: Optional[Dict[str, Any]] = None):
        self.settings = settings or DEFAULT_VIBENIX_SETTINGS.copy()
        self._tool_name_map = self._build_tool_name_map()
    

    # EDIT TOOLS ====
    def _build_tool_name_map(self) -> Dict[str, Callable]:
        """Build a mapping from tool names to actual functions."""
        tool_map = {}
        for func in ALL_FUNCTIONS:
            tool_map[func.__name__] = func
        return tool_map
    
    def get_edit_tools_snippet(self) -> str:
        """Get the snippet to use in the prompt template to match whether edit tools are used or not."""
        edit_tools_setting = self.settings.get("behaviour", {}).get("edit_tools", {})
        snippets = edit_tools_setting.get("snippets", [])

        if len(snippets) < 2:
            raise ValueError("Edit tools snippets must contain at least two entries.")

        if not self.get_edit_tools_enabled():
            return snippets[1]
        else:
            enabled_edit_tools = self._filter_enabled_tools(EDIT_FUNCTIONS)
            return snippets[0].replace("<TOOLS>", ", ".join([f.__name__ for f in enabled_edit_tools]))    

    def is_edit_tools_prompt(self, prompt_name: str) -> bool:
        """Check if a prompt is an edit tools prompt.
        
        Args:
            prompt_name: The name of the prompt
            
        Returns:
            True if the prompt uses edit tools, False otherwise
        """
        prompt_tools = self.settings.get("prompt_tools", {}).get(prompt_name, [])
        prompt_tools = self._filter_enabled_tools(prompt_tools)

        if any(tool in EDIT_FUNCTIONS for tool in prompt_tools):
            return True
        return False


    # General behaviour settings ====
    def _get_behaviour_setting_enabled(self, setting_name: str) -> bool:
        """Get the enabled status of a behaviour setting.
           This is a helper method used by dynamically generated get_*_enabled methods.
        """
        behaviour_settings = self.settings.get("behaviour", {})
        enabled = behaviour_settings.get(setting_name)
        
        # If the value is a dict, look for an "enabled" key inside it
        if isinstance(enabled, dict):
            enabled = enabled.get("enabled")
        
        # If no value found, check with "_enabled" suffix
        if enabled is None:
            enabled = behaviour_settings.get(f"{setting_name}_enabled")
        
        if enabled is None:
            raise ValueError(f"Behaviour setting '{setting_name}' not found.")
        return enabled

    def get_all_behaviour_settings(self) -> Dict[str, Any]:
        """Get the names of all behaviour settings."""
        return self.settings.get("behaviour", {}).keys()

    def _set_behaviour_setting_enabled(self, setting_name: str, value: bool):
        """Set the enabled status of a behaviour setting.
           This is a helper method used by dynamically generated set_*_enabled methods.
        """
        behaviour_settings = self.settings.get("behaviour", {})
        
        # First, try to find the setting with the given name
        if setting_name in behaviour_settings:
            # Found it directly (e.g., "edit_tools" as a dict)
            if isinstance(behaviour_settings[setting_name], dict):
                behaviour_settings[setting_name]["enabled"] = value
            else:
                # It's a boolean directly
                behaviour_settings[setting_name] = value
        elif f"{setting_name}_enabled" in behaviour_settings:
            # Try with "_enabled" suffix (e.g., "progress_evaluation_enabled")
            behaviour_settings[f"{setting_name}_enabled"] = value
        else:
            raise ValueError(f"Behaviour setting '{setting_name}' not found.")
        
        # No need to reassign, we modified the dict in place
        # self.settings["behaviour"] = behaviour_settings

    def __getattr__(self, name: str):
        """Dynamically handle <behaviour_setting> getter and setters method calls.
        
        This provides fallback support for any behaviour settings not explicitly defined.
        For better IDE support, commonly used settings should have explicit methods above.
        """

        # Check if this is a getter or setter
        if (name.startswith("get_") or name.startswith("set_")) and name.endswith("_enabled"):
            # Extract the setting name first (before creating closures that reference it)
            if name.startswith("get_"):
                setting_name = name[4:-8]  # Remove "get_" (4 chars) and "_enabled" (8 chars)
                
                def _get_behaviour_enabled():
                    return self._get_behaviour_setting_enabled(setting_name)
                
                return _get_behaviour_enabled
            
            elif name.startswith("set_"):
                setting_name = name[4:-8]  # Remove "set_" (4 chars) and "_enabled" (8 chars)
                
                def _set_behaviour_enabled(value: bool):
                    self._set_behaviour_setting_enabled(setting_name, value)
                
                return _set_behaviour_enabled
        
        # If not a get_*_enabled or set_*_enabled pattern, raise AttributeError
        raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")

    # Explicitly define methods anyway for IDE support and type checking
    def get_progress_evaluation_enabled(self) -> bool:
        """Check if progress evaluation is enabled."""
        return self._get_behaviour_setting_enabled("progress_evaluation")
    
    def get_build_summary_enabled(self) -> bool:
        """Check if build summary is enabled."""
        return self._get_behaviour_setting_enabled("build_summary")
    
    def get_compare_template_builders_enabled(self) -> bool:
        """Check if compare_template_builders is enabled."""
        return self._get_behaviour_setting_enabled("compare_template_builders")
    
    def get_edit_tools_enabled(self) -> bool:
        """Check if edit tools are enabled."""
        return self._get_behaviour_setting_enabled("edit_tools")

    # Global tool management
    def _filter_enabled_tools(self, tools: List[Callable]) -> List[Callable]:
        """Filter tools based on enabled/disabled settings.
        
        Args:
            tools: List of tool functions
            
        Returns:
            Filtered list containing only enabled tools
        """
        enabled = []
        # Get list of disabled tool names
        disabled_tool_names = self.settings.get("tools", [])
        
        for tool in tools:
            # Handle both function objects and names
            if callable(tool):
                tool_name = tool.__name__
                tool_func = tool
            else:
                # It's a string name, resolve it
                tool_name = tool
                tool_func = self._tool_name_map.get(tool_name)
                if tool_func is None:
                    print(f"Warning: Unknown tool '{tool_name}'")
                    continue
            
            # Include tool only if it's NOT in the disabled list
            if tool_name not in disabled_tool_names:
                enabled.append(tool_func)
        
        return enabled
    
    def get_disabled_tools(self) -> List[str]:
        """Get the list of globally disabled tool names.
        
        Returns:
            List of disabled tool names
        """
        return self.settings.get("tools", [])    

    def toggle_disabled_tools(self, tool: Union[Callable, str]):
        """Disable a specific tool globally.
        
        Args:
            tool: The tool function or its name to disable
        """
        tool_name = tool.__name__ if callable(tool) else tool
        if "tools" not in self.settings:
            self.settings["tools"] = []
        if tool_name not in self.settings["tools"]:
            self.settings["tools"].append(tool_name)
        else:
            self.settings["tools"].remove(tool_name)

    def set_disabled_tools(self, tools: List[Union[Callable, str]]):
        """Set the list of globally disabled tools.
        
        Args:
            tools: List of tool functions or their names to disable
        """
        all_tool_names = [func.__name__ for func in ALL_FUNCTIONS]
        disabled_tool_names = [tool.__name__ if callable(tool) else tool for tool in tools]
        if any(name not in all_tool_names for name in disabled_tool_names):
            raise ValueError(f"One or more tool names are invalid in {disabled_tool_names}.")

        self.settings["tools"] = disabled_tool_names
    

    def list_all_prompts(self) -> List[str]:
        """Get a list of all configured prompt names.
        
        Returns:
            List of prompt names
        """
        return list(self.settings.get("prompt_tools", {}).keys())
    
    def set_prompt_tools(self, prompt_name: str, tool_spec: Union[List[Callable], List[str]]):
        """Update the tool configuration for a specific prompt.
        
        Args:
            prompt_name: The name of the prompt
            tool_spec: Tool specification (list of functions, list of names, or empty list)
        """
        if "prompt_tools" not in self.settings:
            self.settings["prompt_tools"] = {}
        
        # Convert string names to function objects if needed
        if tool_spec and isinstance(tool_spec[0], str):
            tool_spec = [self._tool_name_map.get(name) for name in tool_spec if name in self._tool_name_map]
        
        self.settings["prompt_tools"][prompt_name] = tool_spec

    def get_prompt_tools(self, prompt_name: str) -> List[Callable]:
        """Get the list of tool functions for a specific prompt.
        
        Args:
            prompt_name: The name of the prompt

        Returns:
            List of tool functions that should be available to this prompt
        """
        # Get the configured tools for this prompt
        prompt_tools_config = self.settings.get("prompt_tools", {})
        tool_spec = prompt_tools_config.get(prompt_name, [])
        
        # Normalize to list if needed
        if isinstance(tool_spec, list):
            tools = tool_spec
        else:
            # Single function
            tools = [tool_spec] if tool_spec else []
        
        # Filter based on enabled/disabled settings
        return self._filter_enabled_tools(tools)
    
    def save_settings(self, filepath: str):
        """Save current settings to a JSON file.
        
        Args:
            filepath: Path to the JSON file to save settings
        """
        import json

        with open(filepath, "w") as f:
            json.dump(settings_to_json_format(self.settings), f, indent=4)

# Helper functions for converting between JSON (names) and Python (function objects)

def settings_to_json_format(settings: Dict[str, Any] = None) -> Dict[str, Any]:
    """Convert settings with function objects to JSON-serializable format.
    
    Args:
        settings: Settings dictionary with function objects (Optional)
        
    Returns:
        Settings dictionary with function names as strings
    """
    if settings is None:
        settings = self.settings

    json_settings = settings.copy()
    
    # Convert prompt_tools
    if "prompt_tools" in json_settings:
        prompt_tools = {}
        for prompt_name, tools in json_settings["prompt_tools"].items():
            if isinstance(tools, list):
                prompt_tools[prompt_name] = [f.__name__ for f in tools if callable(f)]
            else:
                prompt_tools[prompt_name] = []
        json_settings["prompt_tools"] = prompt_tools
    
    return json_settings


def settings_from_json_format(json_settings: Dict[str, Any]) -> Dict[str, Any]:
    """Convert JSON settings with function names to settings with function objects.
    
    Args:
        json_settings: Settings dictionary with function names as strings
        
    Returns:
        Settings dictionary with function objects
    """
    # Build tool name map
    tool_name_map = {}
    for func in ALL_FUNCTIONS:
        tool_name_map[func.__name__] = func
    
    settings = json_settings.copy()
    
    # Convert prompt_tools
    if "prompt_tools" in settings:
        prompt_tools = {}
        for prompt_name, tool_names in settings["prompt_tools"].items():
            if isinstance(tool_names, list):
                # Convert names to functions
                tools = []
                for name in tool_names:
                    if name in tool_name_map:
                        tools.append(tool_name_map[name])
                    else:
                        print(f"Warning: Unknown tool '{name}' for prompt '{prompt_name}'")
                prompt_tools[prompt_name] = tools
            else:
                prompt_tools[prompt_name] = []
        settings["prompt_tools"] = prompt_tools
    
    return settings


# Global settings manager instance
_settings_manager = VibenixSettingsManager()


def get_settings_manager() -> VibenixSettingsManager:
    """Get the global settings manager instance.
    
    Returns:
        The global VibenixSettingsManager instance
    """
    return _settings_manager


def load_settings(settings: Dict[str, Any]):
    """Load new settings into the global settings manager.
    
    Args:
        settings: Settings dictionary to load
    """
    global _settings_manager
    _settings_manager = VibenixSettingsManager(settings)
