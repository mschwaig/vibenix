"""Model configuration for pydantic-ai integration.

This module provides model configuration compatible with the previous litellm-based system.
"""

import os
import json
from typing import Optional, Dict, Any, Tuple
from pydantic_ai.models.openai import OpenAIModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from vibenix.ui.logging_config import logger

# TODO: Add retry configuration for providers that support custom HTTP clients
# For now, we'll use default retry behavior


# Cache for model configuration to avoid repeated loading and logging
_cached_config = None
_cached_model = None


def load_saved_configuration() -> Optional[Tuple[str, str, Optional[str], Optional[str]]]:
    """Load previously saved configuration, returns (provider_name, model, ollama_host, openai_api_base).
    
    This maintains compatibility with the previous configuration format.
    """
    try:
        config_path = os.path.expanduser("~/.vibenix/config.json")
        
        if os.path.exists(config_path):
            with open(config_path) as f:
                config_data = json.load(f)
            
            # Extract configuration
            provider_name = config_data.get("provider")
            model = config_data.get("model")
            ollama_host = config_data.get("ollama_host")
            openai_api_base = config_data.get("openai_api_base")
            
            if provider_name and model:
                # Set OPENAI_BASE_URL if using OpenAI with custom endpoint
                if provider_name == "openai" and openai_api_base:
                    os.environ["OPENAI_BASE_URL"] = openai_api_base
                
                return provider_name, model, ollama_host, openai_api_base
            
    except Exception as e:
        logger.warning(f"Could not load saved configuration: {e}")
    
    return None


def get_model_config() -> dict:
    """Get the model configuration from saved config or defaults."""
    global _cached_config
    
    # Return cached config if available
    if _cached_config is not None:
        return _cached_config
    
    # Try to load saved configuration
    saved_config = load_saved_configuration()
    
    if saved_config:
        provider_name, model, ollama_host, openai_api_base = saved_config
        
        # Remove provider prefix from model if present
        if "/" in model:
            model_name = model.split("/", 1)[1]
        else:
            model_name = model
        
        # Determine base URL
        if openai_api_base:
            base_url = openai_api_base
        elif ollama_host:
            base_url = ollama_host
        else:
            base_url = "http://llama.digidow.ins.jku.at:11434/v1/"
        
        # Ensure base URL ends with /v1/ for OpenAI compatibility
        if not base_url.endswith("/v1/") and not base_url.endswith("/v1"):
            base_url = base_url.rstrip("/") + "/v1/"
        
        _cached_config = {
            "provider": provider_name,
            "model_name": model_name,
            "base_url": base_url,
            "full_model": model
        }
    else:
        # Default configuration
        logger.info("No saved configuration found, using defaults")
        _cached_config = {
            "provider": "openai",
            "model_name": "qwen3-coder-30b-a3b",
            "base_url": "http://llama.digidow.ins.jku.at:11434/v1/",
            "full_model": "openai/qwen3-coder-30b-a3b"
        }
    
    return _cached_config


def get_model():
    """Get the model instance, creating it if necessary."""
    global _cached_model
    
    if _cached_model is None:
        # This should only happen if initialize_model_config wasn't called
        raise RuntimeError("Model not initialized. Call initialize_model_config() first.")
    
    return _cached_model



def get_model_name() -> str:
    """Get the current model name for logging."""
    config = get_model_config()
    # Return the full model name with provider prefix for compatibility
    return config.get("full_model", f"{config['provider']}/{config['model_name']}")


def initialize_model_config():
    """Initialize model configuration and create model instance. Must be called once at startup."""
    global _cached_model
    
    config = get_model_config()
    provider_name = config.get("provider", "openai")
    model_name = config.get("model_name")
    
    logger.info(f"Loaded configuration: {config.get('full_model', 'unknown')} from {provider_name}")
    
    # Create model based on provider
    if provider_name == "anthropic":
        # Get Anthropic API key - check environment first (as override), then secure storage
        from vibenix.secure_keys import get_api_key
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            api_key = get_api_key("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY not found in environment or secure storage. Run interactively to configure.")
        
        logger.info(f"Using Anthropic model: {model_name}")
        provider = AnthropicProvider(api_key=api_key)
        _cached_model = AnthropicModel(model_name, provider=provider)
    
    elif provider_name == "gemini":
        # Get Google API key - check environment first (as override), then secure storage
        from vibenix.secure_keys import get_api_key
        api_key = os.environ.get("GEMINI_API_KEY")
        if not api_key:
            api_key = get_api_key("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not found in environment or secure storage. Run interactively to configure.")
        
        logger.info(f"Using Gemini model: {model_name}")
        provider = GoogleProvider(api_key=api_key)
        _cached_model = GoogleModel(model_name, provider=provider)
    
    elif provider_name == "openai":
        # OpenAI-compatible endpoints
        base_url = config.get("base_url")
        api_key = os.environ.get("OPENAI_API_KEY", "dummy")
        
        # Log configuration details
        if "OPENAI_BASE_URL" in os.environ:
            logger.info(f"Set OPENAI_BASE_URL to {os.environ['OPENAI_BASE_URL']}")
        
        logger.info(f"Using OpenAI-compatible model: {model_name} at {base_url}")
        provider = OpenAIProvider(base_url=base_url, api_key=api_key)
        _cached_model = OpenAIModel(model_name, provider=provider)
    
    else:
        raise ValueError(f"Unknown provider: {provider_name}")
    
    logger.info(f"Model initialized successfully")
