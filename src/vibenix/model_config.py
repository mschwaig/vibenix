"""Model configuration for pydantic-ai integration.

This module provides model configuration compatible with the previous litellm-based system.
"""

import os
import json
from typing import Optional, Dict, Any, Tuple, cast
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.models.openrouter import OpenRouterModel, OpenRouterModelSettings
from pydantic_ai.models.anthropic import AnthropicModel, AnthropicModelSettings
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.models.google import GoogleModel, GoogleModelSettings
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.models.bedrock import BedrockConverseModel, BedrockModelSettings
from pydantic_ai.providers.bedrock import BedrockProvider
from vibenix.ui.logging_config import logger

from botocore.config import Config
import boto3

from vibenix.defaults import DEFAULT_MODEL_SETTINGS, DEFAULT_USAGE_LIMITS
from vibenix.model_retrying import RetryingBedrockClient, create_retrying_client
# Cache for model configuration to avoid repeated loading and logging
_cached_config: dict[str, Any] | None = None
_cached_model: Model | None = None
_use_prompted_output = False  # Whether to use PromptedOutput mode for structured outputs


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


def get_model_config(use_cached: bool=True, remove_model_prefix: bool=True) -> dict[str, Any]:
    """Get the model configuration from saved config, and model settings from env."""
    
    global _cached_config
    
    # Return cached config if available
    if _cached_config is not None and use_cached:
        return _cached_config
    
    # Try to load saved configuration
    saved_config = load_saved_configuration()
    
    if saved_config:
        provider_name, model_name, ollama_host, openai_api_base = saved_config
        
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
        provider_name = "openai"
        _cached_config = {
            "provider": "openai",
            "model_name": "qwen3-coder-30b-a3b",
            "base_url": "http://llama.digidow.ins.jku.at:11434/v1/"
        }
    model_settings = load_model_settings_from_env(provider_name)
    _cached_config["model_settings"] = model_settings
    
    return _cached_config

def get_cached_model_config() -> dict[str, Any]:
    """Get the cached model configuration without reloading."""
    global _cached_config

    if _cached_config is None:
        raise RuntimeError("Model configuration not initialized. Call initialize_model_config() first.")

    return _cached_config

def get_model() -> Model:
    """Get the model instance, creating it if necessary.""" # TODO not creating if necessary
    global _cached_model

    if _cached_model is None:
        # This should only happen if initialize_model_config wasn't called
        raise RuntimeError("Model not initialized. Call initialize_model_config() first.")

    return _cached_model


def use_prompted_output() -> bool:
    """Check if PromptedOutput mode should be used for structured outputs.

    PromptedOutput mode is more compatible with OpenAI-compatible endpoints
    that may not properly support tool-based structured outputs.
    """
    return _use_prompted_output



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


def create_openai_settings(settings: Dict[str, Any]) -> OpenAIChatModelSettings:
    """Create OpenAIChatModelSettings from config dict."""
    # Use constants for defaults
    defaults = DEFAULT_MODEL_SETTINGS["openai"].copy()
    
    merged_settings = {**defaults, **settings}
    logger.info(f"Creating OpenAI settings: max_tokens={merged_settings.get('max_tokens')}, temperature={merged_settings.get('temperature')}")
    return OpenAIChatModelSettings(**merged_settings)


def create_anthropic_settings(settings: Dict[str, Any]) -> AnthropicModelSettings:
    """Create AnthropicModelSettings from config dict."""
    # Use constants for defaults
    defaults = DEFAULT_MODEL_SETTINGS["anthropic"].copy()
    
    merged_settings = {**defaults, **settings}
    logger.info(f"Creating Anthropic settings: max_tokens={merged_settings.get('max_tokens')}, temperature={merged_settings.get('temperature')}, anthropic_thinking={merged_settings.get('anthropic_thinking')}")
    return AnthropicModelSettings(**merged_settings)

def create_openrouter_settings(settings: Dict[str, Any]) -> OpenRouterModelSettings:
    """Create OpenRouterModelSettings from config dict."""
    # Use constants for defaults
    defaults = DEFAULT_MODEL_SETTINGS["openrouter"].copy()
    
    merged_settings = {**defaults, **settings}
    logger.info(f"Creating OpenRouter settings: max_tokens={merged_settings.get('max_tokens')}, temperature={merged_settings.get('temperature')}")
    return OpenRouterModelSettings(**merged_settings)

def create_bedrock_settings(settings: Dict[str, Any]) -> BedrockModelSettings:
    """Create BedrockModelSettings from config dict."""
    # Use constants for defaults
    defaults = DEFAULT_MODEL_SETTINGS["bedrock"].copy()
    
    merged_settings = {**defaults, **settings}
    logger.info(f"Creating Bedrock settings: max_tokens={merged_settings.get('max_tokens')}, temperature={merged_settings.get('temperature')}")
    return BedrockModelSettings(**merged_settings)

def initialize_model_config(model_settings = None):
    """Initialize model configuration and create model instance. Must be called once at startup."""
    global _cached_model, _use_prompted_output

    config = get_model_config()
    provider_name = config.get("provider", "openai")
    model_name = config.get("model_name")
    if not isinstance(model_name, str):
        raise RuntimeError("Model name missing from configuration.")
    base_url = config.get("base_url", "")
    
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
        
        if not model_settings:
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

        # Create a google.genai.Client with our retrying transport
        from google.genai import Client
        from google.genai.types import HttpOptions
        from pydantic_ai.models import get_user_agent

        # Create the retrying client and extract its transport
        retrying_client = create_retrying_client()

        http_options = HttpOptions(
            headers={'User-Agent': get_user_agent()},
            async_client_args={'transport': retrying_client._transport}
        )

        gemini_client = Client(
            api_key=api_key,
            http_options=http_options
        )

        # Pass the configured client to GoogleProvider
        provider = GoogleProvider(client=gemini_client)

        if not model_settings:
            env_settings = load_model_settings_from_env("gemini")
            model_settings = create_gemini_settings(env_settings)
        _cached_model = GoogleModel(config["model_name"], provider=provider, settings=model_settings)
    else:
        # Default to OpenAI-compatible models
        base_url = config.get("base_url")

        # Check if using OpenRouter or AWS Bedrock
        is_openrouter = provider_name == "openrouter" or (base_url and 'openrouter.ai' in base_url)
        is_bedrock = provider_name == "bedrock" or (base_url and 'bedrock' in base_url and 'api.aws' in base_url)

        # Auto-enable PromptedOutput mode for endpoints that don't reliably support tool-based structured outputs
        if is_openrouter or is_bedrock:
            _use_prompted_output = True
            logger.info("Auto-enabled PromptedOutput mode for better compatibility with this endpoint")

        if is_bedrock:
            from vibenix.secure_keys import get_api_key
            api_key = os.environ.get("AWS_BEARER_TOKEN_BEDROCK")
            if not api_key:
                api_key = get_api_key("AWS_BEARER_TOKEN_BEDROCK")
                if not api_key:
                    raise ValueError("AWS_BEARER_TOKEN_BEDROCK not found in environment or secure storage. Run interactively to configure.")

            logger.info(f"Using AWS Bedrock model: {model_name} at {base_url}")
            bedrock_boto_config = Config(
                retries={
                    'mode': 'standard',
                    'total_max_attempts': 1
                }
            )
            raw_bedrock_client = boto3.client(
                'bedrock-runtime',
                region_name='us-east-1',
                config=bedrock_boto_config
            )
            bedrock_client = RetryingBedrockClient(raw_bedrock_client, max_retries=2)
            bedrock_provider = BedrockProvider(bedrock_client=bedrock_client, api_key=api_key)  # type: ignore[call-overload]

            if not model_settings:
                env_settings = load_model_settings_from_env("bedrock")
                model_settings = create_bedrock_settings(env_settings)
            _cached_model = BedrockConverseModel(model_name, provider=bedrock_provider, settings=model_settings)

        elif is_openrouter:
            # Use OpenRouterProvider for OpenRouter endpoints
            from vibenix.secure_keys import get_api_key
            api_key = os.environ.get("OPENROUTER_API_KEY")
            if not api_key:
                api_key = get_api_key("OPENROUTER_API_KEY")
                if not api_key:
                    raise ValueError("OPENROUTER_API_KEY not found in environment or secure storage. Run interactively to configure.")

            config = get_model_config(use_cached=False, remove_model_prefix=False)
            model_name = config.get("model_name")
            if not isinstance(model_name, str):
                raise RuntimeError("Model name missing from configuration.")
            model_name = provider_name + "/" + model_name if '/' not in model_name else model_name

            logger.info(f"Using OpenRouter model: {model_name}")
            provider = OpenRouterProvider(api_key=api_key, http_client=create_retrying_client())

            # Update the config cache with the corrected model name
            if _cached_config is None:
                raise RuntimeError("Model configuration not initialized.")
            _cached_config["model_name"] = model_name

            if not model_settings:
                env_settings = load_model_settings_from_env("openrouter")
                model_settings = create_openrouter_settings(env_settings)
            _cached_model = OpenRouterModel(model_name, provider=provider, settings=model_settings)
        else:
            # Use OpenAIProvider for other OpenAI-compatible endpoints
            from vibenix.secure_keys import get_api_key
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                api_key = get_api_key("OPENAI_API_KEY")
                if not api_key:
                    # For local/Ollama endpoints, API key is optional
                    logger.info("No OPENAI_API_KEY found in environment or secure storage, using 'dummy'")
                    api_key = "dummy"

            # Log configuration details
            if "OPENAI_BASE_URL" in os.environ:
                logger.info(f"Set OPENAI_BASE_URL to {os.environ['OPENAI_BASE_URL']}")

            logger.info(f"Using OpenAI-compatible model: {model_name} at {base_url}")
            provider = OpenAIProvider(base_url=base_url, api_key=api_key, http_client=create_retrying_client())

            if not model_settings:
                env_settings = load_model_settings_from_env("openai")
                model_settings = create_openai_settings(env_settings)
            _cached_model = OpenAIChatModel(config["model_name"], provider=provider, settings=model_settings)


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
    """Create a client with smart retry handling for rate limits and transient failures.

    This follows pydantic-ai best practices:
    - Respects Retry-After headers from 429 responses (when provided by API)
    - Uses exponential backoff as fallback for better behavior with concurrent jobs
    - Retries on network errors and server errors (5xx)
    - Up to 10 retries with ~5.5 minutes total wait time to handle persistent rate limiting
    """

    from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
    from httpx import AsyncClient, HTTPStatusError, Response
    from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

    def should_retry_status(response: Response):
        """Raise HTTPStatusError for retryable status codes (429, 5xx).

        The wait_retry_after strategy will automatically extract and respect
        Retry-After headers from 429 responses before they become exceptions.
        """
        if response.status_code in (429, 502, 503, 504):
            response.raise_for_status()

    def log_retry_attempt(retry_state):
        """Log retry attempts with wait time information."""
        exception = retry_state.outcome.exception() if retry_state.outcome else None
        attempt_number = retry_state.attempt_number

        # Calculate wait time using the same strategy
        wait_func = wait_retry_after(
            fallback_strategy=wait_exponential(multiplier=3, min=3, max=60),
            max_wait=300
        )
        wait_seconds = wait_func(retry_state)

        # Check if we got a Retry-After header
        retry_after_header = None
        if isinstance(exception, HTTPStatusError):
            retry_after_header = exception.response.headers.get('retry-after')

        if retry_after_header:
            logger.warning(
                f"Rate limited by API (attempt {attempt_number}/10). "
                f"Retry-After header: {retry_after_header}. Waiting {wait_seconds:.1f} seconds..."
            )
        else:
            logger.warning(
                f"Request failed (attempt {attempt_number}/10). "
                f"Using exponential backoff: waiting {wait_seconds:.1f} seconds... "
                f"Error: {type(exception).__name__}"
                f": {str(exception)[:200].replace(chr(10), ' ')}"
            )

    transport = AsyncTenacityTransport(
        config=RetryConfig(
            retry=retry_if_exception_type((HTTPStatusError, ConnectionError)),
            wait=wait_retry_after(
                fallback_strategy=wait_exponential(multiplier=3, min=3, max=60),
                max_wait=300
            ),
            stop=stop_after_attempt(10),
            reraise=True,
            before_sleep=log_retry_attempt,
        ),
        validate_response=should_retry_status
    )
    return AsyncClient(transport=transport)
