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

from vibenix.tools.file_tools import create_source_function_calls

from tempfile import mkdtemp

def get_names(funcs: List[Union[Callable, str]]) -> List[str]:
    return [func.__name__ if callable(func) else func for func in funcs]
tempdir = mkdtemp()
PROJECT_TOOLS = get_names(create_source_function_calls(tempdir, "project_"))
NIXPKGS_TOOLS = get_names(create_source_function_calls(tempdir, "nixpkgs_"))
GET_BUILDER_FUNCTIONS = ['get_builder_functions']
FIND_SIMILAR_BUILDER_PATTERNS = ['find_similar_builder_patterns']

ADDITIONAL_TOOLS = GET_BUILDER_FUNCTIONS + FIND_SIMILAR_BUILDER_PATTERNS + PROJECT_TOOLS + NIXPKGS_TOOLS


# TODO ADD EVALUATE PROGRESS
# Default mapping of prompts to their tools
DEFAULT_PROMPT_TOOLS: Dict[str, List[Callable]] = {
    'pick_template': [],
    'summarize_github': [],
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
DEFAULT_PROMPT_ADD_TOOLS: Dict[str, List[str]] = {
    'pick_template': [],
    'summarize_github': [],
    'get_feedback': get_names(ADDITIONAL_TOOLS),
    'refine_code': [],
    'fix_build_error': get_names(ADDITIONAL_TOOLS),
    'fix_hash_mismatch': [],
    'classify_packaging_failure': [],
    'analyze_package_failure': get_names(ADDITIONAL_TOOLS),
    'summarize_build': get_names(PROJECT_TOOLS),
    'choose_builders': get_names(PROJECT_TOOLS + NIXPKGS_TOOLS),
    'compare_template_builders': get_names(PROJECT_TOOLS + NIXPKGS_TOOLS + GET_BUILDER_FUNCTIONS),
}

DEFAULT_VIBENIX_SETTINGS = {
    # Individual tool toggles (disable specific tools globally)
    # Empty: All tools enabled by default 
    "tools": [],
    
    # Per-prompt tool configuration
    # Values are lists of function objects
    "prompt_tools": DEFAULT_PROMPT_TOOLS.copy(),
    "prompt_additional_tools": DEFAULT_PROMPT_ADD_TOOLS.copy(),
    
    # General behavior, misc
    "behaviour": {
        # Enable or disable progress_evaluation
        "progress_evaluation": True,
        "build_summary": True,
        "compare_template_builders": True,
        "refinement": {
            "enabled": True,
            "iterations": 3,
        },
        # Enable or disable edit tools
        "edit_tools": True,
        # 2 Agents edit (planning + implementation)
        "2_agents": False,
        # Snippets to add to prompts dynamically
        "snippets": { # improve TODO 3
            "tool": "To perform each change to the code, use the text editor tools: [<TOOLS>].",
            "extract": "Your response should contain the full updated packaging code, wrapped like so:\n```nix\n...\n```.",
            "feedback": "Your response should contain the list of concrete changes you would make to the packaging code. Be specific."
        }
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
        for func in ADDITIONAL_TOOLS:
            tool_map[func] = None
        return tool_map

    def initialize_additional_tools(self, tools: List[Callable]):
        """Initialize the additional tools in the tool name map."""
        for func in tools:
            self._tool_name_map[func.__name__] = func
    
    def get_snippet(self, prompt: str, snippet: str = None) -> str:
        """Get a snippet to use in the prompt template."""

        if self.is_edit_tools_prompt(prompt):
            if self.get_setting_enabled("2_agents"):
                return self.settings.get("behaviour", {}).get("snippets", {}).get("feedback", "")

            elif self.get_setting_enabled("edit_tools"):
                snippet = self.settings.get("behaviour", {}).get("snippets", {}).get("tool", "")
                tools = self.get_prompt_tools(prompt)
                enabled_edit_tools = self._filter_enabled_tools(tools)
                return snippet.replace("<TOOLS>", ", ".join([f.__name__ for f in enabled_edit_tools]))    

            else:
                return self.settings.get("behaviour", {}).get("snippets", {}).get("extract", "")
        else:
            return ""


    def is_edit_tools_prompt(self, prompt_name: str) -> bool:
        """Check if a prompt is an edit tools prompt (has code edit tools).
        
        Args:
            prompt_name: The name of the prompt
            
        Returns:
            True if the prompt uses edit tools, False otherwise
        """
        prompt_tools = DEFAULT_PROMPT_TOOLS.get(prompt_name, [])

        if any(tool in EDIT_FUNCTIONS for tool in prompt_tools):
            return True
        return False


    # General behaviour settings ====
    def get_setting_enabled(self, setting_name: str) -> bool:
        setting = self.get_setting_value(setting_name)
        if isinstance(setting, bool):
            return setting
        elif isinstance(setting, dict):
            enabled = setting.get("enabled")
            if enabled is not None:
                return enabled
            else:
                raise ValueError(f"Wrong usage of get_setting_enabled for '{setting_name}'.")
        raise ValueError(f"Setting '{setting_name}' is neither bool nor dict.")

    def set_setting_enabled(self, setting_name: str, enabled: bool):
        behaviour_settings = self.settings.get("behaviour", {})
        setting = behaviour_settings.get(setting_name)

        if isinstance(setting, bool):
            self.set_setting_value(setting_name, enabled)
        elif isinstance(setting, dict):
            if "enabled" in setting:
                setting["enabled"] = enabled
            else:
                raise ValueError(f"Wrong usage of set_setting_enabled for '{setting_name}'.")

    def get_setting_value(self, value_path: str) -> Any:
        """Get a specific value from a behaviour setting that is a dict.
        
        Args:
            value_path: Dot-separated path to the value (e.g., "refinement.iterations")
            
        Returns:
            The value associated with the key in the behaviour setting dict
        """
        behaviour_settings = self.settings.get("behaviour", {})
        parts = value_path.split(".")
        setting_name = parts[0]
        setting = behaviour_settings.get(setting_name)

        key = None
        while len(parts) > 1:
            if isinstance(setting, dict):
                key = parts[1]
                setting = setting.get(key)
                parts = parts[1:]
            else:
                raise ValueError(f"Behaviour setting '{setting_name}' is not a dict.")

        return setting

    def list_all_behaviour_settings(self) -> List[str]:
        """Get the names of all behaviour settings."""
        return list(self.settings.get("behaviour", {}).keys())

    def set_setting_value(self, value_path: str, value: bool):
        """Set the enabled status of a behaviour setting.
           This is a helper method used by dynamically generated set_*_enabled methods.
        """
        behaviour_settings = self.settings.get("behaviour", {})
        
        parts = value_path.split(".")
        setting_name = parts[0]
        setting = behaviour_settings.get(setting_name)

        if isinstance(setting, bool):
            if len(parts) == 1:
                self.settings["behaviour"][setting_name] = value
                return
            
        key = None
        while len(parts) > 1:
            if isinstance(setting, dict):
                key = parts[1]
                setting = setting.get(key)
                parts = parts[1:]
            else:
                raise ValueError(f"Behaviour setting '{setting_name}' is not a dict.")
        if key is None:
            raise ValueError(f"Error setting value for '{value_path}'.")
        if isinstance(setting, dict):
            setting[key] = value
        else:
            raise ValueError(f"Behaviour setting '{setting_name}' is not a dict.")


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

    def get_additional_tool(self, name: str) -> Optional[Callable]:
        """Get an additional tool function by name.
        
        Args:
            name: Name of the tool function
            
        Returns:
            The tool function if found, None otherwise
        """
        return self._tool_name_map.get(name)

    def get_disabled_tools(self) -> List[str]:
        """Get the list of globally disabled tool names.
        
        Returns:
            List of disabled tool names
        """
        return self.settings.get("tools", [])    

    def toggle_global_tools(self, tool: Union[Callable, str]):
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
    

    # Prompt tools
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

    def set_prompt_additional_tools(self, prompt_name: str, tool_spec: List[str]):
        """Set the list of additional tool names for a specific prompt.
        
        Args:
            prompt_name: The name of the prompt
            tool_spec: List of tool names to set for this prompt
        """
        if "prompt_additional_tools" not in self.settings:
            self.settings["prompt_additional_tools"] = {}
        
        self.settings["prompt_additional_tools"][prompt_name] = tool_spec

    def get_prompt_additional_tools(self, prompt_name: str) -> List[str]:
        """Get the list of additional tool functions for a specific prompt.
        
        Args:
            prompt_name: The name of the prompt
        Returns:
            List of additional tool functions for this prompt
        """
        additional_tools_names = self.get_prompt_additional_tools_names(prompt_name)
        tools = [self.get_additional_tool(name) for name in additional_tools_names]

        if any(t is None for t in tools):
            raise ValueError(f"One or more prompt tools have not been initialized in runtime.")
        
        return tools

    def get_prompt_additional_tools_names(self, prompt_name: str) -> List[str]:
        """Get the list of additional tool names for a specific prompt.
        
        Args:
            prompt_name: The name of the prompt
        Returns:
            List of additional tool names for this prompt
        """
        prompt_tools_config = self.settings.get("prompt_additional_tools", {})
        tool_spec = prompt_tools_config.get(prompt_name, [])
        
        # Normalize to list if needed
        if isinstance(tool_spec, list):
            tools = tool_spec
        else:
            # Single function
            tools = [tool_spec] if tool_spec else []
        
        # Convert function objects to names if needed
        if tools and callable(tools[0]):
            tools = [tool.__name__ for tool in tools]
        
        return tools

    
    def save_settings(self, filepath: str):
        """Save current settings to a JSON file.
        
        Args:
            filepath: Path to the JSON file to save settings
        """
        import json

        with open(filepath, "w") as f:
            json.dump(settings_to_json_format(self.settings), f, indent=4)

    def get_settings(self) -> Dict[str, Any]:
        """Get the current settings dictionary.
        
        Returns:
            The current settings dictionary
        """
        return self.settings
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
