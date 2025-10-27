"""Utilities for extracting usage data from pydantic-ai responses."""

from typing import Tuple

def extract_usage_tokens(usage_data) -> Tuple[int, int]:
    """Extract input and output tokens from usage data.
    
    Different providers return usage data in different formats:
    - Anthropic: usage_data.details['input_tokens'] and usage_data.details['output_tokens']
    - Gemini/OpenAI: usage_data.input_tokens and usage_data.output_tokens
    
    This function detects which format is being used and extracts accordingly.
    
    Returns:
        Tuple of (input_tokens, output_tokens)
    """
    if not usage_data:
        return 0, 0

    # Check if we have a details dictionary (what Anthropic uses)
    if hasattr(usage_data, 'details') and isinstance(usage_data.details, dict):
        if 'input_tokens' in usage_data.details and 'output_tokens' in usage_data.details:
            return (
                usage_data.details['input_tokens'],
                usage_data.details['output_tokens']
            )

    # Direct attributes format (Gemini)
    if hasattr(usage_data, 'input_tokens') and hasattr(usage_data, 'output_tokens'):
        return (
            usage_data.input_tokens,
            usage_data.output_tokens
        )

    # Fallback if neither format is found
    return 0, 0
