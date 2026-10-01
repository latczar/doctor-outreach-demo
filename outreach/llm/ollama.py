"""A local model through Ollama. Free, offline, and quick enough on a decent PC."""

from __future__ import annotations

import httpx

from .base import LLMError, LLMReply


class OllamaLLM:
    name = "ollama"

    def __init__(self, url: str, model: str, timeout: float = 120.0):
        self.url = url
        self.model = model
        self.timeout = timeout

    def generate_json(self, system: str, prompt: str, schema: dict) -> LLMReply:
        payload = {
            "model": self.model,
            "stream": False,
            "format": schema,  # Ollama constrains the reply to this JSON schema
            "options": {"temperature": 0.4},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        }
        try:
            response = httpx.post(f"{self.url}/api/chat", json=payload, timeout=self.timeout)
        except httpx.ConnectError as exc:
            raise LLMError(
                f"Can't reach Ollama at {self.url}. Start it with 'ollama serve', or set LLM_PROVIDER=fake."
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama didn't answer within {self.timeout:.0f} seconds.") from exc
        if response.status_code == 404:
            raise LLMError(f"Ollama doesn't have the model '{self.model}'. Run: ollama pull {self.model}")
        if response.status_code != 200:
            raise LLMError(f"Ollama returned HTTP {response.status_code}: {response.text[:200]}")
        return LLMReply(response.json()["message"]["content"], provider=self.name, model=self.model)
