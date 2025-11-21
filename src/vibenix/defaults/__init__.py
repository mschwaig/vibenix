"""Make all json files available as python objects."""

import json
import os


model_settings = os.path.join(os.path.dirname(__file__), "model_settings.json")
with open(model_settings, "r") as f:
    DEFAULT_MODEL_SETTINGS = json.load(f)
usage_limits = os.path.join(os.path.dirname(__file__), "usage_limits.json")
with open(usage_limits, "r") as f:
    DEFAULT_USAGE_LIMITS = json.load(f)

# Import vibenix settings manager
from vibenix.defaults.vibenix_settings import (
    get_settings_manager,
    load_settings,
    DEFAULT_VIBENIX_SETTINGS,
    DEFAULT_PROMPT_TOOLS,
    settings_to_json_format,
    settings_from_json_format,
    VibenixSettingsManager
)

__all__ = [
    "DEFAULT_MODEL_SETTINGS",
    "DEFAULT_USAGE_LIMITS",
    "get_settings_manager",
    "load_settings",
    "DEFAULT_VIBENIX_SETTINGS",
    "DEFAULT_PROMPT_TOOLS",
    "settings_to_json_format",
    "settings_from_json_format",
    "VibenixSettingsManager",
]
