"""Pydantic-AI agent implementation for vibenix.

This module provides the core agent functionality using pydantic-ai.
"""

import asyncio
import time
from typing import Optional, List, Callable, Any
from pydantic import BaseModel
from pydantic_ai import Agent
from pydantic_ai.exceptions import UnexpectedModelBehavior
from vibenix.model_config import get_model
from vibenix.ui.conversation import get_ui_adapter, Message, Actor, Usage
from vibenix.usage_utils import extract_usage_tokens
from vibenix.ccl_log import get_logger
import logging

logger = logging.getLogger(__name__)


async def _retry_with_backoff(func, max_retries=3, base_delay=5.0):
    """Retry function with exponential backoff for specific model errors."""
    for attempt in range(max_retries + 1):
        try:
            return await func()
        except UnexpectedModelBehavior as e:
            error_msg = str(e)
            if "Content field missing" in error_msg:
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Model empty response (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                else:
                    logger.warning("Model returned empty response after max retries, treating as None")
                    # Return None to signal no improvements after all retries
                    return None, Usage(prompt_tokens=0, completion_tokens=0)
            else:
                raise
        except Exception as e:
            # Check for server errors (503 UNAVAILABLE)
            error_msg = str(e)
            if ("503" in error_msg and "UNAVAILABLE" in error_msg and "overloaded" in error_msg):
                if attempt < max_retries:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Model server overloaded (attempt {attempt + 1}/{max_retries + 1}), retrying in {delay}s...")
                    time.sleep(delay)
                    continue
                else:
                    logger.error("Model server overloaded after max retries")
                    raise
            else:
                raise
    
    # Should never reach here, but just in case
    raise RuntimeError("Unexpected error in retry logic")


class VibenixAgent:
    """Main agent for vibenix using pydantic-ai."""
    
    def __init__(self, instructions: str = None, output_type: type = None):
        """Initialize the agent with optional custom instructions and output type."""
        self.model = get_model()
        default_instructions = "You are a software packaging expert who can build any project using the Nix programming language."
        
        # Only pass output_type if it's not None
        if output_type is not None:
            self.agent = Agent(
                model=self.model,
                instructions=instructions or default_instructions,
                output_type=output_type
            )
        else:
            self.agent = Agent(
                model=self.model,
                instructions=instructions or default_instructions
            )
        
        self._tools = []
        self._output_type = output_type  # Store output type for later checks
    
    def add_tool(self, func: Callable) -> None:
        """Add a tool function to the agent."""
        # Use tool_plain since our tools don't need RunContext
        self.agent.tool_plain(func)
        self._tools.append(func)
    
    async def run_async(self, prompt: str) -> tuple[Any, Usage]:
        """Run the agent asynchronously and return response with usage."""
        async def _run_agent():
            result = await self.agent.run(prompt)
            
            # Convert pydantic-ai usage to our Usage dataclass
            usage_data = result.usage() if hasattr(result, 'usage') else None
            prompt_tokens, completion_tokens = extract_usage_tokens(usage_data)
            usage = Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
            
            # Handle both text and structured output
            output = result.output
            
            return output, usage
        
        return await _retry_with_backoff(_run_agent)
    
    def run(self, prompt: str) -> tuple[str, Usage]:
        """Run the agent synchronously."""
        # Create event loop if needed
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.run_async(prompt))
    
    async def run_stream_async(self, prompt: str) -> tuple[str, Usage]:
        """Run the agent with streaming and return complete response with usage."""
        adapter = get_ui_adapter()
        
        # For text output, we use regular run method to avoid streaming issues
        # We can get rid of this by switching away from text output to structured output for the updated code
        if self._output_type is None:
            async def _run_stream_agent():
                result = await self.agent.run(prompt)
                
                # Get the text output
                output = result.output
                full_response = ""
                if output:
                    full_response = str(output)
                    # Show the complete response in the UI
                    adapter.show_message(Message(Actor.MODEL, full_response))
                    print(full_response)
                
                # Get usage data
                usage_data = result.usage() if hasattr(result, 'usage') else None
                prompt_tokens, completion_tokens = extract_usage_tokens(usage_data)
                usage = Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
                
                return full_response, usage
            
            return await _retry_with_backoff(_run_stream_agent)
        else:
            # For structured output, use streaming
            async def _run_structured_agent():
                async with self.agent.run_stream(prompt) as result:
                    output = await result.get_output()
                    full_response = str(output)
                    print(full_response)
                    
                    # Get usage data
                    usage_data = result.usage() if hasattr(result, 'usage') else None
                    prompt_tokens, completion_tokens = extract_usage_tokens(usage_data)
                    usage = Usage(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
                    
                    return full_response, usage
            
            return await _retry_with_backoff(_run_structured_agent)
    
    def run_stream(self, prompt: str) -> tuple[str, Usage]:
        """Run the agent with streaming synchronously."""
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        
        return loop.run_until_complete(self.run_stream_async(prompt))
