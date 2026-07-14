"""Retrying and error-handling helpers for model providers."""

from __future__ import annotations

import re
from typing import Any, Optional

from botocore.exceptions import ClientError
from httpx import AsyncClient, HTTPStatusError, Response
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import retry_if_exception_type, stop_after_attempt, wait_exponential

from vibenix.ui.logging_config import logger


_BEDROCK_TOOL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
_BEDROCK_TOOL_FIELD_PATH_PATTERN = re.compile(
    r"messages\.(\d+)(?:\.member)?\.content\.(\d+)(?:\.member)?\.toolUse\.(name|input)"
)


def _extract_bedrock_tool_uses(messages: list[dict]) -> list[dict[str, Any]]:
    """Extract toolUse entries from Bedrock converse messages for diagnostics."""
    tool_uses: list[dict[str, Any]] = []

    for message_index, message in enumerate(messages):
        content_blocks = message.get("content", []) if isinstance(message, dict) else []
        if not isinstance(content_blocks, list):
            continue

        for content_index, content_block in enumerate(content_blocks):
            if not isinstance(content_block, dict):
                continue
            tool_use = content_block.get("toolUse")
            if isinstance(tool_use, dict):
                tool_uses.append(
                    {
                        "message_index": message_index,
                        "content_index": content_index,
                        "tool_use": tool_use,
                    }
                )

    return tool_uses


def _extract_bedrock_tool_field_at_path(messages: list[dict], message_index: int, content_index: int, field_name: str) -> Any:
    """Return toolUse.<field_name> at the specific Bedrock error path indices."""
    if message_index < 0 or content_index < 0:
        return None
    if message_index >= len(messages):
        return None

    message = messages[message_index]
    if not isinstance(message, dict):
        return None

    content_blocks = message.get("content")
    if not isinstance(content_blocks, list) or content_index >= len(content_blocks):
        return None

    content_block = content_blocks[content_index]
    if not isinstance(content_block, dict):
        return None

    tool_use = content_block.get("toolUse")
    if not isinstance(tool_use, dict):
        return None

    return tool_use.get(field_name)


def _extract_failed_tool_field_from_error(error: Exception, messages: list[dict]) -> dict[str, Any] | None:
    """Parse Bedrock validation path and return referenced toolUse field value."""
    match = _BEDROCK_TOOL_FIELD_PATH_PATTERN.search(str(error))
    if not match:
        return None

    message_index = int(match.group(1))
    content_index = int(match.group(2))
    field_name = match.group(3)
    field_value = _extract_bedrock_tool_field_at_path(messages, message_index, content_index, field_name)

    return {
        "message_index": message_index,
        "content_index": content_index,
        "field_name": field_name,
        "field_value": field_value,
        "field_value_repr": repr(field_value),
        "field_value_type": type(field_value).__name__,
    }


def _normalize_bedrock_tool_name(tool_name: Any) -> str:
    """Drop everything from the first non-[a-zA-Z0-9_-] character onward."""
    normalized = str(tool_name) if tool_name is not None else ""
    match = re.match(r"[a-zA-Z0-9_-]+", normalized)
    return match.group(0) if match else ""


def _normalize_bedrock_tool_names_in_messages(messages: list[dict]) -> list[dict[str, Any]]:
    """Normalize toolUse.name entries in outgoing Bedrock messages in-place."""
    rewrites: list[dict[str, Any]] = []

    for message_index, message in enumerate(messages):
        if not isinstance(message, dict):
            continue

        content_blocks = message.get("content", [])
        if not isinstance(content_blocks, list):
            continue

        for content_index, content_block in enumerate(content_blocks):
            if not isinstance(content_block, dict):
                continue

            tool_use = content_block.get("toolUse")
            if not isinstance(tool_use, dict):
                continue

            original_name = tool_use.get("name")
            normalized_name = _normalize_bedrock_tool_name(original_name)

            if original_name != normalized_name:
                tool_use["name"] = normalized_name
                rewrites.append(
                    {
                        "message_index": message_index,
                        "content_index": content_index,
                        "old_name": original_name,
                        "new_name": normalized_name,
                    }
                )

    return rewrites


def _log_bedrock_retry_diagnostics(error: Exception, params: dict[str, Any], retry_number: int, max_retries: int) -> None:
    """Log concise diagnostics before retrying a failed Bedrock converse call."""
    messages = params.get("messages", [])
    parsed_messages = messages if isinstance(messages, list) else []
    tool_uses = _extract_bedrock_tool_uses(parsed_messages)
    failing_tool_from_error = _extract_failed_tool_field_from_error(error, parsed_messages)

    invalid_tool_names: list[dict[str, Any]] = []
    for entry in tool_uses:
        tool_use = entry["tool_use"]
        tool_name = tool_use.get("name")
        if not isinstance(tool_name, str) or not _BEDROCK_TOOL_NAME_PATTERN.match(tool_name):
            invalid_tool_names.append(
                {
                    "message_index": entry["message_index"],
                    "content_index": entry["content_index"],
                    "tool_name": tool_name,
                    "tool_name_repr": repr(tool_name),
                }
            )

    error_summary = str(error).replace("\n", " ")[:220]
    logger.warning(
        f"Bedrock retry {retry_number}/{max_retries} after ValidationException: {error_summary}"
    )

    if failing_tool_from_error:
        field_name = failing_tool_from_error["field_name"]
        field_type = failing_tool_from_error["field_value_type"]
        logger.warning(f"Bedrock validation target: toolUse.{field_name} (type={field_type})")

    if invalid_tool_names:
        logger.warning(f"Bedrock detected {len(invalid_tool_names)} invalid toolUse.name value(s)")
    elif tool_uses:
        logger.warning(f"Bedrock request contains {len(tool_uses)} toolUse block(s); names look valid")
    else:
        logger.warning("Bedrock request contains no toolUse blocks")


class RetryingBedrockClient:
    """Wrapper around boto Bedrock runtime client with deterministic retry behavior."""

    def __init__(self, bedrock_client, max_retries: int = 2):
        self._client = bedrock_client
        self._max_retries = max_retries

    def __getattr__(self, name: str):
        return getattr(self._client, name)

    def converse(self, **params):
        total_attempts = self._max_retries + 1

        messages = params.get("messages")
        if isinstance(messages, list):
            rewrites = _normalize_bedrock_tool_names_in_messages(messages)
            if rewrites:
                logger.warning(
                    f"Applied Bedrock toolUse.name normalization to {len(rewrites)} entr{'y' if len(rewrites) == 1 else 'ies'} before request"
                )

        for attempt in range(1, total_attempts + 1):
            try:
                return self._client.converse(**params)
            except ClientError as error:
                error_code = error.response.get("Error", {}).get("Code")
                if attempt >= total_attempts:
                    raise

                if error_code == "ValidationException":
                    _log_bedrock_retry_diagnostics(error, params, retry_number=attempt, max_retries=self._max_retries)
                else:
                    logger.warning(
                        f"Bedrock client error before retry {attempt}/{self._max_retries}: "
                        f"code={error_code}, message={str(error)}"
                    )
            except Exception as error:
                if attempt >= total_attempts:
                    raise

                logger.warning(f"Bedrock unexpected error before retry {attempt}/{self._max_retries}: {str(error)}")


def create_retrying_client():
    """Create a client with smart retry handling for rate limits and transient failures.

    This follows pydantic-ai best practices:
    - Respects Retry-After headers from 429 responses (when provided by API)
    - Uses exponential backoff as fallback for better behavior with concurrent jobs
    - Retries on network errors and server errors (5xx)
    - Up to 10 retries with ~5.5 minutes total wait time to handle persistent rate limiting
    """

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

        wait_func = wait_retry_after(
            fallback_strategy=wait_exponential(multiplier=3, min=3, max=60),
            max_wait=300,
        )
        wait_seconds = wait_func(retry_state)

        retry_after_header = None
        if isinstance(exception, HTTPStatusError):
            retry_after_header = exception.response.headers.get("retry-after")

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
                max_wait=300,
            ),
            stop=stop_after_attempt(10),
            reraise=True,
            before_sleep=log_retry_attempt,
        ),
        validate_response=should_retry_status,
    )
    return AsyncClient(transport=transport)


## agent.py model prompt error logging and debugging utils ##
def _log_model_failure(messages, exception):
    """Log minimal details about model output before validation/tool errors.

    This helps diagnose UnexpectedModelBehavior / ToolRetryError cases by
    recording the last model-related message without dumping huge transcripts.
    """
    try:
        logger.error("Model failure: %s: %s", type(exception).__name__, str(exception))
    except Exception as log_exc:
        # Never let logging failures interfere with the main error path
        logger.warning("Failed to log model failure details: %s", log_exc)

def _extract_message_content(message) -> Optional[str]:
    """Best-effort extraction of readable content from pydantic-ai messages."""
    try:
        # Some message objects may expose direct content-like fields
        for attr in ("content", "text", "output", "message"):
            if hasattr(message, attr):
                value = getattr(message, attr)
                if value:
                    return str(value)

        parts = getattr(message, "parts", None)
        if not parts:
            return None

        chunks = []
        for part in parts:
            part_type = type(part).__name__
            part_content = None

            if hasattr(part, "content") and getattr(part, "content"):
                part_content = str(getattr(part, "content"))
            elif hasattr(part, "text") and getattr(part, "text"):
                part_content = str(getattr(part, "text"))
            elif hasattr(part, "args") and getattr(part, "args") is not None:
                part_content = str(getattr(part, "args"))
            elif hasattr(part, "tool_name") and getattr(part, "tool_name"):
                part_content = f"tool={getattr(part, 'tool_name')}"
            else:
                part_content = str(part)

            chunks.append(f"[{part_type}] {part_content}")

        return "\n".join(chunks)
    except Exception:
        return None

def _has_retry_prompt_part(message) -> bool:
    """Check whether a message includes a pydantic-ai RetryPromptPart."""
    try:
        parts = getattr(message, "parts", None)
        if not parts:
            return False
        return any(type(part).__name__ == "RetryPromptPart" for part in parts)
    except Exception:
        return False

def _is_model_response_message(message) -> bool:
    """Check whether message looks like a model response message."""
    try:
        if type(message).__name__ == "ModelResponse":
            return True

        parts = getattr(message, "parts", None)
        if not parts:
            return False

        response_like_part_names = {
            "TextPart",
            "ThinkingPart",
            "ToolCallPart",
            "BuiltinToolCallPart",
            "BuiltinToolReturnPart",
            "ToolReturnPart",
        }
        return any(type(part).__name__ in response_like_part_names for part in parts)
    except Exception:
        return False

def _log_internal_retry_responses(messages, level: str = "warning", log_when_none: bool = False) -> None:
    """Log model response content that led to each internal pydantic-ai retry."""
    try:
        log_func = getattr(logger, level, logger.warning)
        retry_count = 0

        for idx, message in enumerate(messages):
            if not _has_retry_prompt_part(message):
                continue

            retry_count += 1
            retry_prompt_content = _extract_message_content(message)

            prev_response_content = None
            prev_response_index = None
            for prev_idx in range(idx - 1, -1, -1):
                candidate = messages[prev_idx]
                if _is_model_response_message(candidate):
                    prev_response_index = prev_idx
                    prev_response_content = _extract_message_content(candidate)
                    break

            log_func("Internal pydantic-ai retry #%d detected.", retry_count)

            current_retry_message_content = _extract_message_content(message)
            log_func(
                "  Current retry-triggering message (message %d): %s",
                idx,
                (current_retry_message_content or "<no extractable content>")[:2000],
            )

            if prev_response_index is not None:
                log_func(
                    "  Response that led to retry (message %d): %s",
                    prev_response_index,
                    (prev_response_content or "<no extractable content>")[:2000],
                )
            else:
                log_func("  Could not find preceding model response for this retry.")

            if retry_prompt_content:
                log_func("  Retry prompt details: %s", retry_prompt_content[:2000])

        if retry_count == 0 and log_when_none:
            log_func("No internal pydantic-ai retry prompts were captured in messages.")
    except Exception as e:
        logger.warning("Could not log internal retry response details: %s", e)
