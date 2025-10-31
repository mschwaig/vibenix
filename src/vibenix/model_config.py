"""Model configuration for pydantic-ai integration.

This module provides model configuration compatible with the previous litellm-based system.
"""

import os
import json
from typing import Optional, Dict, Any, Tuple
from pydantic_ai.models.openai import OpenAIModel, OpenAIModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.providers.google import GoogleProvider
from vibenix.ui.logging_config import logger


from vibenix.defaults import DEFAULT_MODEL_SETTINGS, DEFAULT_USAGE_LIMITS
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
    """Get the model configuration from env, saved config, and defaults."""
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
            "base_url": base_url
        }
    else:
        # Default configuration
        logger.info("No saved configuration found, using defaults")
        _cached_config = {
            "provider": "openai",
            "model_name": "qwen3-coder-30b-a3b",
            "base_url": "http://llama.digidow.ins.jku.at:11434/v1/"
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
    # Always construct the full model name with provider prefix
    return f"{config['provider']}/{config['model_name']}"


def load_model_settings_from_env(provider: str) -> Dict[str, Any]:
    """Load model settings from environment variable or use defaults.
    
    Checks for VIBENIX_MODEL_SETTINGS environment variable containing JSON.
    Falls back to defaults if not found or invalid.
    
    Example GitHub CI usage:
    VIBENIX_MODEL_SETTINGS: '{"temperature": 0.1, "max_tokens": 16384}'
    """
    env_settings_json = os.environ.get("VIBENIX_MODEL_SETTINGS")
    
    if env_settings_json:
        try:
            env_settings = json.loads(env_settings_json)
            logger.info(f"Loaded model settings from VIBENIX_MODEL_SETTINGS environment variable")
            
            # Merge with defaults to ensure all required keys exist
            defaults = DEFAULT_MODEL_SETTINGS.get(provider, DEFAULT_MODEL_SETTINGS["openai"]).copy()
            merged_settings = {**defaults, **env_settings}
            
            logger.info(f"Using environment model settings: {merged_settings}")
            return merged_settings
            
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in VIBENIX_MODEL_SETTINGS environment variable: {e}")
            logger.warning("Falling back to default settings")
        except Exception as e:
            logger.warning(f"Error parsing VIBENIX_MODEL_SETTINGS: {e}")
            logger.warning("Falling back to default settings")
    
    # Return defaults
    defaults = DEFAULT_MODEL_SETTINGS.get(provider, DEFAULT_MODEL_SETTINGS["openai"]).copy()
    return defaults


def create_gemini_settings(settings: Dict[str, Any]) -> GoogleModelSettings:
    """Create GoogleModelSettings from config dict."""
    # Use constants for defaults
    defaults = DEFAULT_MODEL_SETTINGS["gemini"].copy()
    
    # Merge user settings with defaults
    merged_settings = {**defaults, **settings}
    
    # Handle thinking config - convert thinking_budget to google_thinking_config format
    try:
        thinking_budget = merged_settings.pop("thinking_budget", defaults["thinking_budget"])
        merged_settings["google_thinking_config"] = {"thinking_budget": thinking_budget}
    except Exception as e:
        pass # No thinking config provided
    info_msg = f"Creating Gemini settings:"
    for key in merged_settings:
        info_msg += f" {key}={merged_settings[key]},"
    
    logger.info(info_msg.rstrip(","))
    return GoogleModelSettings(**merged_settings)


def create_openai_settings(settings: Dict[str, Any]) -> OpenAIModelSettings:
    """Create OpenAIModelSettings from config dict."""
    # Use constants for defaults
    defaults = DEFAULT_MODEL_SETTINGS["openai"].copy()
    
    merged_settings = {**defaults, **settings}
    logger.info(f"Creating OpenAI settings: max_tokens={merged_settings.get('max_tokens')}, temperature={merged_settings.get('temperature')}")
    return OpenAIModelSettings(**merged_settings)


def create_anthropic_settings(settings: Dict[str, Any]) -> AnthropicModelSettings:
    """Create AnthropicModelSettings from config dict."""
    # Use constants for defaults
    defaults = DEFAULT_MODEL_SETTINGS["anthropic"].copy()
    
    merged_settings = {**defaults, **settings}
    logger.info(f"Creating Anthropic settings: max_tokens={merged_settings.get('max_tokens')}, temperature={merged_settings.get('temperature')}, anthropic_thinking={merged_settings.get('anthropic_thinking')}")
    return AnthropicModelSettings(**merged_settings)


def initialize_model_config():
    """Initialize model configuration and create model instance. Must be called once at startup."""
    global _cached_model
    
    config = get_model_config()
    provider_name = config.get("provider", "openai")
    model_name = config.get("model_name")
    
    logger.info(f"Loaded configuration: {provider_name}/{config['model_name']} from {provider_name}")
    
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
        provider = AnthropicProvider(api_key=api_key, http_client=create_retrying_client())
        
        # Always use env settings or defaults, never from config file
        env_settings = load_model_settings_from_env("anthropic")
        model_settings = create_anthropic_settings(env_settings)
        _cached_model = AnthropicModel(model_name, provider=provider, settings=model_settings)
    
    elif provider_name == "gemini":
        # Get Google API key - check environment first (as override), then secure storage
        from vibenix.secure_keys import get_api_key
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            api_key = get_api_key("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("GEMINI_API_KEY not found in environment or secure storage. Run interactively to configure.")
        
        logger.info(f"Using Gemini model: {model_name}")
        provider = GoogleProvider(api_key=api_key, http_client=create_retrying_client())
        
        env_settings = load_model_settings_from_env("gemini")
        model_settings = create_gemini_settings(env_settings)
        _cached_model = GoogleModel(config["model_name"], provider=provider, settings=model_settings)
    else:
        # Default to OpenAI-compatible models
        api_key = os.environ.get("OPENAI_API_KEY", "dummy")
        base_url = config.get("base_url")
        
        # Log configuration details
        if "OPENAI_BASE_URL" in os.environ:
            logger.info(f"Set OPENAI_BASE_URL to {os.environ['OPENAI_BASE_URL']}")
        
        logger.info(f"Using OpenAI-compatible model: {model_name} at {base_url}")
        provider = OpenAIProvider(base_url=base_url, api_key=api_key, http_client=create_retrying_client())
        
        env_settings = load_model_settings_from_env("openai")
        model_settings = create_openai_settings(env_settings)
        _cached_model = OpenAIModel(config["model_name"], provider=provider, settings=model_settings)


def calc_model_pricing(model: str, prompt_tokens: int, completion_tokens: int,
                       cache_read_tokens: int = 0) -> float:
    try:
        provider, model_ref = model.split("/", 1)
        from genai_prices import calc_price, Usage
        # Get pricing from genai-prices library
        price_data = calc_price(
            Usage(input_tokens=prompt_tokens, output_tokens=completion_tokens,
                  cache_read_tokens=cache_read_tokens),
            model_ref=model_ref,
            provider_id=provider,
            )
        return float(price_data.total_price)
    except ImportError:
        pass
    except Exception:
        # If genai-prices doesn't have this model, fall back to 0
        pass
    # Default to 0 for unknown models (like local/Ollama models)
    return 0.0


def create_retrying_client():
    """Create a client with smart retry handling for multiple error types."""

    from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
    from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
    from httpx import AsyncClient, HTTPStatusError, Response
    from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

    def should_retry_status(response: Response):
        """Raise exceptions for retryable HTTP status codes."""
        if response.status_code in (429, 502, 503, 504):
            response.raise_for_status()  # This will raise HTTPStatusError

    transport = AsyncTenacityTransport(
        # Create retry configuration for handling transient failures
        config = RetryConfig(
            retry=retry_if_exception_type((HTTPStatusError,  ConnectionError)),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=5, min=3, max=60),
                max_wait=300
            ),
            stop=stop_after_attempt(5),
            reraise=True,
        ),
        validate_response=should_retry_status
    )
    return AsyncClient(transport=transport)
