import keyring
from typing import Optional
from vibenix.ui.logging_config import logger

SERVICE_NAME = "vibenix"

# Track if we're using file backend
_using_file_backend = False
_vibenix_keyring_path = None

try:
    # Force use of PlaintextKeyring
    from keyrings.alt.file import PlaintextKeyring
    
    plain_keyring = PlaintextKeyring()
    keyring.set_keyring(plain_keyring)
    
    # Get the actual path where keys will be stored
    _vibenix_keyring_path = plain_keyring.file_path
    _using_file_backend = True
    
    logger.info(f"Set up PlaintextKeyring at: {_vibenix_keyring_path}")
    
except ImportError:
    logger.error("keyrings.alt not available - please install it")
    raise
except Exception as e:
    logger.error(f"Error setting up keyring: {e}")
    raise

def get_api_key(key_name: str) -> Optional[str]:
    """Get an API key from secure storage."""
    try:
        key = keyring.get_password(SERVICE_NAME, key_name)
        if key:
            logger.info(f"Retrieved {key_name} from secure storage")
        return key
    except Exception as e:
        logger.error(f"Error retrieving {key_name}: {e}")
        return None

def set_api_key(key_name: str, key_value: str) -> None:
    """Store an API key in secure storage."""
    try:
        keyring.set_password(SERVICE_NAME, key_name, key_value)
        logger.info(f"Stored {key_name} in secure storage")
    except Exception as e:
        logger.error(f"Error storing {key_name}: {e}")
        raise

class MissingAPIKeyError(Exception):
    """Raised when an API key is required but not available."""
    def __init__(self, key_name: str, description: str):
        self.key_name = key_name
        self.description = description
        super().__init__(f"Missing API key: {key_name}")

def is_using_file_backend():
    """Check if we're using the file backend."""
    return _using_file_backend

def get_keyring_path():
    """Get the path where keys are stored."""
    return _vibenix_keyring_path

def ensure_api_key(key_name: str, prompt_message: str = None, ui_mode: bool = False) -> str:
    """Ensure an API key is available, prompting if necessary."""
    # First try to get from keyring
    key = get_api_key(key_name)
    if key:
        return key
    
    # If not in keyring, check environment variable as fallback
    import os
    env_key = os.environ.get(key_name)
    if env_key:
        logger.info(f"Found {key_name} in environment, storing in secure storage")
        set_api_key(key_name, env_key)
        return env_key
    
    # If in UI mode, raise exception to be handled by UI
    if ui_mode:
        raise MissingAPIKeyError(
            key_name, 
            prompt_message or f"API key '{key_name}' is required but not found."
        )
    
    # CLI mode - prompt directly
    if _using_file_backend:
        print(f"\nNo keystore backend detected. API keys will be stored in plain text at {_vibenix_keyring_path or '~/.vibenix/api_keys'}")
    
    if prompt_message:
        print(f"\n{prompt_message}")
    else:
        print(f"\nAPI key '{key_name}' is required but not found.")
    
    print(f"Please enter your {key_name}:")
    key_value = input().strip()
    
    if not key_value:
        raise ValueError(f"No value provided for {key_name}")
    
    set_api_key(key_name, key_value)
    print(f"✓ {key_name} saved")
    
    return key_value