"""Claude as the hosted option. Optional: needs `pip install anthropic` and ANTHROPIC_API_KEY."""

from __future__ import annotations

from .base import LLMError, LLMReply


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, model: str = "claude-opus-5-5"):
        try:
            import anthropic
        except ImportError as exc:
            raise LLMError("The anthropic package isn't installed. Run: pip install anthropic") from exc
        self._sdk = anthropic
        self.client = anthropic.Anthropic()
        self.model = model

    def generate_json(self, system: str, prompt: str, schema: dict) -> LLMReply:
        sdk = self._sdk
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                # If a safety classifier declines, the API retries on a suitable model itself.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                system=system,
                messages=[{"role": "user", "content": prompt}],
                output_config={
                    "effort": "low",  # a short email doesn't need deep thinking
                    "format": {"type": "json_schema", "schema": schema},
                },
            )
        except sdk.AuthenticationError as exc:
            raise LLMError("Claude rejected the credentials. Check ANTHROPIC_API_KEY.") from exc
        except sdk.RateLimitError as exc:
            raise LLMError("Claude's rate limit was hit. Wait a minute and try again.") from exc
        except sdk.APIConnectionError as exc:
            raise LLMError("Can't reach the Claude API. Check the internet connection.") from exc
        except sdk.APIStatusError as exc:
            raise LLMError(f"Claude API error {exc.status_code}: {exc.message}") from exc

        if response.stop_reason == "refusal":
            raise LLMError("Claude declined to write this email.")
        if response.stop_reason == "max_tokens":
            raise LLMError("Claude's reply was cut off before it finished.")
        text = next((block.text for block in response.content if block.type == "text"), "")
        return LLMReply(text, provider=self.name, model=response.model)
