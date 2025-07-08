from litellm.integrations.custom_logger import CustomLogger
from litellm import ModelResponse
import litellm


class EndStreamLogger(CustomLogger):
    """A custom callback handler to log usage and cost at the end of a successful call."""
    def __init__(self):
        super().__init__()
        self.total_cost = 0.0
        
    def log_success_event(self, kwargs, response_obj: ModelResponse, start_time, end_time):
        print("\n--- STREAM COMPLETE (Callback Triggered) ---")
        try:
            # response_obj is the final, aggregated response.
            # For streaming, the usage is available in the final chunk's response_obj.
            if response_obj and hasattr(response_obj, 'usage'):
                usage = response_obj.usage
                print(f"Final Prompt Tokens: {usage.prompt_tokens}")
                print(f"Final Completion Tokens: {usage.completion_tokens}")
                print(f"Final Total Tokens: {usage.total_tokens}")

                # Calculate cost from the final aggregated response
                cost = litellm.completion_cost(completion_response=response_obj)
                print(f"Total Stream Cost: ${cost:.6f}")
                self.total_cost += cost
            else:
                if kwargs.get("response_cost") is not None:
                     cost = kwargs['response_cost']
                     print(f"Total Stream Cost (from kwargs): ${cost:.6f}")
                     self.total_cost += cost

        except Exception as e:
            print(f"Error in success_callback: {e}")
        finally:
            print("------------------------------------------\n")


class TokenLimitEnforcer(CustomLogger):
    """Enforce token limit."""

    def __init__(self, limit=32000):
        super().__init__()
        self.limit = limit

    def log_pre_api_call(self, model, messages, kwargs):
        """Check estimated tokens and crash if over limit."""
        estimated_tokens = litellm.token_counter(model=model, messages=messages)

        if estimated_tokens > self.limit:
            print(f"\n\nFATAL ERROR: Token limit exceeded!")
            print(f"Estimated input tokens: {estimated_tokens}")
            print(f"Token limit: {self.limit}")
            print("Terminating application.\n")
            sys.exit(1)

        # Set max_tokens to remaining space
        remaining = self.limit - estimated_tokens
        kwargs["max_tokens"] = min(kwargs.get("max_tokens", remaining), remaining)

    def log_success_event(
        self, kwargs, response_obj: ModelResponse, start_time, end_time
    ):
        """Verify actual usage didn't exceed limit."""
        if not response_obj or not hasattr(response_obj, "usage"):
            print(
                "\n\nFATAL ERROR: Cannot verify token usage - response missing usage data"
            )
            print("Terminating application.\n")
            sys.exit(1)

        total_tokens = response_obj.usage.total_tokens
        if total_tokens > self.limit:
            print(f"\n\nFATAL ERROR: Token limit exceeded!")
            print(f"Total tokens used: {total_tokens}")
            print(f"Token limit: {self.limit}")
            print("Terminating application.\n")
            sys.exit(1)


token_limit_enforcer = TokenLimitEnforcer(limit=32000)
end_stream_logger = EndStreamLogger()
litellm.callbacks = [token_limit_enforcer, end_stream_logger]

