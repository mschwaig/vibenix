"""Vibenix settings manager for controlling prompt tools and behavior."""

from typing import Dict, List, Callable, Optional, Any, Union, Set
from vibenix.tools import (
    SEARCH_TOOLS,
    EDIT_TOOLS,
    OUT_PATH_TOOLS,
    VM_TOOLS,
    search_nix_functions,
    search_nixpkgs_manual_documentation,
)
from vibenix.tools.file_tools import create_source_function_calls
from vibenix.tools.out_path_file_tools import create_out_path_file_tools


def deep_merge(original, update):
    """Recursively merge nested dictionaries"""
    result = original.copy()
    
    missing_bools = [key for key in result if isinstance(result[key], bool) and key not in update]
    if missing_bools:
        raise KeyError(f"Missing boolean keys in update during deep merge: {missing_bools}")
    for key, value in update.items():
        if key not in result or (type(result[key]) != type(value)):
            raise KeyError(f"Key '{key}' with type {type(result[key])} not found in original dictionary during deep merge.")
        elif (isinstance(result[key], dict) and 
            isinstance(value, dict)):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result

def deep_diff(original, updated):
    """Recursively compare nested dictionaries and only keep differences.
    Both dictionaries should have the same entries and matching types."""
    diff = {}

    for key, value in updated.items():
        if key not in original:
            raise KeyError(f"Key '{key}' not found in original dictionary during deep diff.")
        elif (type(original[key]) != type(value)):
            raise ValueError(f"Type mismatch/collision for key '{key}': {type(original[key])} vs {type(value)}")
        elif isinstance(value, dict) and isinstance(original[key], dict):
            nested_diff = deep_diff(original[key], value)
            if nested_diff:
                diff[key] = nested_diff
        elif value != original[key] or isinstance(value, bool):
            diff[key] = value
    return diff

def get_names(funcs: List[Union[Callable, str]]) -> List[str]:
    return [func.__name__ if callable(func) else func for func in funcs]


ALL_PROMPTS = [
    "pick_template",
    "summarize_project_source",
    "get_feedback",
    "refine_code",
    "fix_build_error",
    "fix_hash_mismatch",
    "evaluate_progress",
    "classify_packaging_failure",
    "analyze_package_failure",
    "compare_template_builders",
    "choose_builders",
]

from tempfile import mkdtemp
tempdir = mkdtemp()
PROJECT_TOOLS = get_names(create_source_function_calls(tempdir, "project_"))
NIXPKGS_TOOLS = get_names(create_source_function_calls(tempdir, "nixpkgs_"))
GET_BUILDER_TOOLS = ['get_builder_functions']
FIND_SIMILAR_BUILDER_PATTERNS = ['find_similar_builder_patterns']
# that require runtime initialization
ADDITIONAL_TOOLS = GET_BUILDER_TOOLS + FIND_SIMILAR_BUILDER_PATTERNS + PROJECT_TOOLS + NIXPKGS_TOOLS

def _build_tool_name_map() -> Dict[str, Callable]:
    """Build a mapping from tool names to actual functions/callables."""
    from vibenix.agent import tool_wrapper

    tool_map = {}
    for func in SEARCH_TOOLS + EDIT_TOOLS + OUT_PATH_TOOLS + VM_TOOLS:
        tool_map[func.__name__] = tool_wrapper(func)
    for func in ADDITIONAL_TOOLS:
        tool_map[func] = None
    return tool_map
TOOL_NAME_MAP = _build_tool_name_map()
ALL_TOOLS = list(TOOL_NAME_MAP.keys())


DEFAULT_PROMPT_TOOLS: Dict[str, List[str]] = {prompt: [] for prompt in ALL_PROMPTS}
DEFAULT_PROMPT_TOOLS.update(
    {
        'summarize_project_source': PROJECT_TOOLS,
        'get_feedback': get_names(SEARCH_TOOLS + VM_TOOLS) + ADDITIONAL_TOOLS, # Access to run_in_vm tools
        'refine_code': get_names(SEARCH_TOOLS + EDIT_TOOLS + OUT_PATH_TOOLS), # Access to out path tools
        'fix_build_error': get_names(SEARCH_TOOLS + EDIT_TOOLS) + ADDITIONAL_TOOLS,
        'fix_hash_mismatch': get_names(EDIT_TOOLS),
        'analyze_package_failure': get_names(SEARCH_TOOLS) + ADDITIONAL_TOOLS,
        'compare_template_builders': get_names([search_nix_functions, search_nixpkgs_manual_documentation]+EDIT_TOOLS)
            + PROJECT_TOOLS + NIXPKGS_TOOLS + GET_BUILDER_TOOLS,
        'choose_builders': PROJECT_TOOLS + NIXPKGS_TOOLS,
    }
)

### Settings ##################################################################
DEFAULT_VIBENIX_SETTINGS = {
    # Individual tool toggles (disable specific tools globally)
    # Disabled by default: semantic search and nix function search
    "tools": {
        **{tool: True for tool in ALL_TOOLS},
        "search_nixpkgs_for_package_semantic": False,
        "search_nix_functions": False,
    },

    # Per-prompt tool configuration
    "prompt_tools": {prompt: DEFAULT_PROMPT_TOOLS[prompt] for prompt in ALL_PROMPTS},

    # General behavior, misc
    "behaviour": {
        "progress_evaluation": True,
        "compare_template_builders": True,
        "packaging_loop": {
            "max_iterations": 20,
            "max_consecutive_non_build_errors": 99,
            "max_consecutive_rebuilds_without_progress": 10,
        },
        "refinement": {
            "enabled": False,
            "chat_history": True,
            "max_iterations": 2,
        },
        "edit_tools": True,
        # Snippets to add to prompts dynamically
        "snippets": {
            "tool": "To perform each change to the code, use the text editor tools: [<TOOLS>].",
            "extract": "Please respond with the full updated packaging code, wrapped like so:\n```nix\n...\n```.",
            "object": "Please respond with a valid ModelCodeResponse object containing the full updated packaging code.",
            "feedback": "Please respond with the list of concrete changes you would make to the packaging code. Be specific."
        }
    }
}
###############################################################################


class VibenixSettingsManager:
    """Manages vibenix settings and resolves prompt tools."""
    
    def __init__(self, settings: Optional[Dict[str, Any]] = {}, tool_name_map: Dict[str, Union[None, Callable]] = TOOL_NAME_MAP):
        # Merge provided settings with DEFAULT_VIBENIX_SETTINGS in one line
        self.settings = deep_merge(DEFAULT_VIBENIX_SETTINGS.copy(), settings)
        self._tool_name_map = tool_name_map

    def initialize_additional_tools(self, tools: List[Callable]):
        """Initialize the additional tools in the tool name map."""
        from vibenix.agent import tool_wrapper
        for func in tools:
            self._tool_name_map[func.__name__] = tool_wrapper(func)

    def get_snippet(self, prompt: str = "", snippet: str = "") -> str:
        """Get a snippet to use in the prompt template."""

        if snippet:
            return self.settings.get("behaviour", {}).get("snippets", {}).get(snippet, "")

        if self.get_setting_enabled("edit_tools"):
            snippet = self.settings.get("behaviour", {}).get("snippets", {}).get("tool", "")
            tools = self.get_prompt_tools(prompt)
            enabled_edit_tools = self._filter_enabled_tools(tools)
            return snippet.replace("<TOOLS>", ", ".join([f_name for f_name in enabled_edit_tools]))    
        else:
            return self.settings.get("behaviour", {}).get("snippets", {}).get("object", "")

    def is_edit_tools_prompt(self, prompt_name: str) -> bool:
        """Check if a prompt is an edit tools prompt (has code edit tools).
        
        Args:
            prompt_name: The name of the prompt
            
        Returns:
            True if the prompt uses edit tools, False otherwise
        """
        prompt_tools = DEFAULT_PROMPT_TOOLS.get(prompt_name, [])

        if any(tool_name in get_names(EDIT_TOOLS) for tool_name in prompt_tools):
            return True
        return False


    # General behaviour settings ====
    def get_setting_enabled(self, setting_name: str) -> bool:
        setting = self.get_setting_value(setting_name)
        if isinstance(setting, bool):
            return setting
        raise ValueError(f"Setting '{setting_name}' is not a boolean (toggleable).")

    def set_setting_enabled(self, setting_name: str, enabled: bool):
        self.get_setting_enabled(setting_name)  # Validate it's toggleable
        self.set_setting_value(setting_name, enabled)

    def get_setting_value(self, value_path: str) -> Any:
        """Get a specific value from a behaviour setting.
        
        Args:
            value_path: Dot-separated path to the value (e.g., "refinement.max_iterations")
            
        Returns:
            The value at the specified path
        """
        behaviour_settings = self.settings.get("behaviour", {})
        parts = value_path.split(".")
        current = behaviour_settings
        
        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise ValueError(f"Setting path '{value_path}' not found.")
        final_key = parts[-1]
        if isinstance(current, dict) and final_key in current:
            current = current[final_key]
        else:
            raise ValueError(f"Setting path '{value_path}' not found.")
        
        return current

    def list_all_behaviour_settings(self) -> List[str]:
        """Get the names of all behaviour settings, including nested keys with dot notation.
        
        Returns:
            List of setting paths (e.g., ["progress_evaluation", "packaging_loop.max_iterations"])
        """
        behaviour_settings = self.settings.get("behaviour", {})
        settings_list = []
        
        def extract_paths(settings_dict: Dict, prefix: str = ""):
            """Recursively extract all paths from a nested dictionary."""
            for key, value in settings_dict.items():
                current_path = f"{prefix}.{key}" if prefix else key
                
                if isinstance(value, dict):
                    extract_paths(value, current_path)
                else:
                    settings_list.append(current_path)
        
        extract_paths(behaviour_settings)
        return settings_list
    
    def set_setting_value(self, value_path: str, value: Any):
        """Set a value in behaviour settings using dot-separated path.
        
        Args:
            value_path: Dot-separated path to the value (e.g., "refinement.max_iterations")
            value: The value to set
        """
        behaviour_settings = self.settings.get("behaviour", {})
        parts = value_path.split(".")
        current = behaviour_settings
        
        # Navigate to parent of target
        for part in parts[:-1]:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                raise ValueError(f"Setting path '{value_path}' not found.")
        
        # Set the final value
        final_key = parts[-1]
        if isinstance(current, dict) and final_key in current:
            current[final_key] = value
        else:
            raise ValueError(f"Setting path '{value_path}' not found.")


    # Global tool management
    def _filter_enabled_tools(self, tools: List[str]) -> List[str]:
        """Filter tools based on enabled/disabled settings.
        
        Args:
            tools: List of tool functions
            
        Returns:
            Filtered list containing only enabled tools
        """
        enabled = []
        # Get list of disabled tool names
        enabled_tools = self.settings.get("tools", {})
        # if edit_tools disabled, filter out all edit tools
        if not self.get_setting_enabled("edit_tools"):
            for edit_tool in get_names(EDIT_TOOLS):
                enabled_tools[edit_tool] = False
        
        for tool_name in tools:
            if tool_name not in enabled_tools:
                raise ValueError(f"Tool '{tool_name}' is not recognized.")
            
            if enabled_tools[tool_name]:
                enabled.append(tool_name)
        
        return enabled

    def get_tool_callable(self, name: str) -> Optional[Callable]:
        """Get the callable for a tool function by name.
        
        Args:
            name: Name of the tool function
            
        Returns:
            The tool function if found, None if not initialized
        Raises:
            ValueError: If the tool name is not recognized
        """
        if name not in self._tool_name_map:
            raise ValueError(f"Tool '{name}' is not recognized.")
        elif self._tool_name_map[name] is None:
            raise ValueError(f"Tool '{name}' has not been initialized yet.")
        return self._tool_name_map.get(name)

    def get_disabled_tools(self) -> List[str]:
        """Get a list of globally disabled tool names.
        
        Returns:
            List of disabled tool names
        """
        disabled = []
        for tool_name, is_enabled in self.settings.get("tools", {}).items():
            if not is_enabled:
                disabled.append(tool_name)
        return disabled

    def toggle_disabled_tools(self, tool: Union[Callable, str]):
        """Disable a specific tool globally.
        
        Args:
            tool: The tool function or its name to disable
        """
        tool_name = tool.__name__ if callable(tool) else tool
        if "tools" not in self.settings:
            self.settings["tools"] = {}
        if tool_name not in self.settings["tools"]:
            raise ValueError(f"Tool '{tool_name}' is not recognized.")
        else:
            self.settings["tools"][tool_name] = not self.settings["tools"][tool_name]

    def set_disabled_tools(self, tools: List[Union[Callable, str]]):
        """Set the list of globally disabled tools.
        
        Args:
            tools: List of tool functions or their names to disable
        """
        to_disable = [tool.__name__ if callable(tool) else tool for tool in tools]

        if any(name not in ALL_TOOLS for name in to_disable):
            raise ValueError(f"One or more tool names are invalid in {to_disable}.")

        all_tools = {name: True for name in ALL_TOOLS}
        all_tools.update({name: False for name in to_disable})
        self.settings["tools"] = all_tools

    # Prompt tools
    def list_all_tools(self) -> List[str]:
        """Get a list of all available tool names.

        Returns:
            List of all tool names
        """
        return list(self._tool_name_map.keys())

    def list_all_prompts(self) -> List[str]:
        """Get a list of all configured prompt names.

        Returns:
            List of prompt names (managed by settings)
        """
        return list(self.settings.get("prompt_tools", {}).keys())
    
    def set_prompt_tools(self, prompt_name: str, tool_spec: List[str]):
        """Update the tool configuration for a specific prompt.
        
        Args:
            prompt_name: The name of the prompt
            tool_spec: Tool specification (list function names, or empty list)
        """
        if "prompt_tools" not in self.settings:
            self.settings["prompt_tools"] = {}
        
        if any(not isinstance(name, str) for name in tool_spec):
            raise ValueError(f"Tool specification must be a list of tool names (strings).")
        elif any(name not in self._tool_name_map for name in tool_spec):
            raise ValueError(f"One or more unknown tools in {tool_spec}.")
        elif prompt_name not in self.list_all_prompts():
            raise ValueError(f"Prompt '{prompt_name}' is not recognized.")

        self.settings["prompt_tools"][prompt_name] = tool_spec

    def get_prompt_tools(self, prompt_name: str, filter_disabled: bool=True) -> List[str]:
        """Get the list of tool functions for a specific prompt.
        
        Args:
            prompt_name: The name of the prompt
            filter_disabled: Whether to filter out disabled tools

        Returns:
            List of tool functions that should be available to this prompt
        """
        # Get the configured tools for this prompt
        prompt_tools_config = self.settings.get("prompt_tools", {})
        tool_spec = prompt_tools_config.get(prompt_name, []) # TODO complain if prompt not found
        
        # Filter based on enabled/disabled settings
        if filter_disabled:
            return self._filter_enabled_tools(tool_spec)
        else:
            return tool_spec

    
    def save_settings(self, filepath: str, diff_only: bool=True):
        """Save current settings to a JSON file.
        
        Args:
            filepath: Path to the JSON file to save settings
        """
        import json
        if diff_only:
            settings_diff = deep_diff(DEFAULT_VIBENIX_SETTINGS, self.settings)
        else:
            settings_diff = self.settings

        with open(filepath, "w") as f:
            json.dump(settings_to_json_format(settings_diff), f, indent=4)

    def get_settings(self, diff_only: bool=False) -> Dict[str, Any]:
        """Get the current settings dictionary.
        
        Returns:
            The current settings dictionary
        """
        if diff_only:
            return deep_diff(DEFAULT_VIBENIX_SETTINGS, self.settings)
        return self.settings

# Helper functions for converting between JSON (names) and Python (function objects)
def settings_to_json_format(settings: Dict[str, Any]) -> Dict[str, Any]:
    """Convert settings with function objects to JSON-serializable format.
    
    Args:
        settings: Settings dictionary with function objects (Optional)
        
    Returns:
        Settings dictionary with function names as strings
    """
    return settings.copy()


def settings_from_json_format(json_settings: Dict[str, Any]) -> Dict[str, Any]:
    """Convert JSON settings with function names to settings with function objects.
    
    Args:
        json_settings: Settings dictionary with function names as strings
        
    Returns:
        Settings dictionary with function names
    """
    settings = json_settings.copy()
    
    if "prompt_tools" in settings:
        prompt_tools = {}
        for prompt_name, tool_names in settings["prompt_tools"].items():
            if isinstance(tool_names, list):
                for name in tool_names:
                    if name not in get_settings_manager().list_all_tools():
                        raise ValueError(f"Tool name '{name}' in prompt '{prompt_name}' is not recognized.")
                    else:
                        prompt_tools.setdefault(prompt_name, []).append(name)
            else:
                raise ValueError(f"Tool specification for prompt '{prompt_name}' must be a list.")
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
