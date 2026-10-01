"""The one interface every model sits behind. The rest of the code never knows which model it's using."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class LLMReply:
    text: str
    provider: str
    model: str


class LLMError(Exception):
    """The model couldn't be reached or wouldn't answer. The message is shown to the user as-is."""


class LLM(Protocol):
    name: str

    def generate_json(self, system: str, prompt: str, schema: dict) -> LLMReply: ...
