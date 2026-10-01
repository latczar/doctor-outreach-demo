"""Models that need no AI: a template writer for offline demos, and a scripted one for tests."""

from __future__ import annotations

import json
import re

from .base import LLMError, LLMReply


def _block(prompt: str, tag: str) -> dict:
    match = re.search(rf"<{tag}>\s*(\{{.*?\}})\s*</{tag}>", prompt, re.DOTALL)
    if not match:
        raise LLMError(f"Template writer couldn't find <{tag}> in the prompt.")
    return json.loads(match.group(1))


class TemplateLLM:
    """Fills a fixed template from the prompt's data blocks.

    It is deliberately naive: it quotes the profile notes word for word, the way a model that
    obeys everything would. That lets the checks show what they catch without a real model.
    """

    name = "fake"
    model = "template"

    def generate_json(self, system: str, prompt: str, schema: dict) -> LLMReply:
        facts = _block(prompt, "campaign_facts")
        doctor = _block(prompt, "doctor_profile")
        notes = doctor.get("profile_notes") or ""
        source = (doctor.get("source") or "web").lower()
        hook = (
            f'Your profile on the {source} mentions: "{notes}"'
            if notes
            else f"I came across your profile on the {source}."
        )
        specialty = (doctor.get("specialty") or "medicine").lower()
        body = (
            f"Dear {doctor['salutation']},\n\n"
            f"I'm getting in touch from {facts['organisation']} because of your work in {specialty} "
            f"at {doctor.get('employer') or 'your hospital'}. {hook}\n\n"
            "We arrange short observership placements for medical students and are looking for "
            f"doctors who enjoy teaching. {' '.join(facts['facts'])}\n\n"
            "Would you be open to a short call to hear more?"
        )
        reply = {
            "subject": f"Mentoring visiting medical students in {doctor.get('specialty') or 'your specialty'}",
            "body": body,
            "personal_detail_used": notes,
        }
        return LLMReply(json.dumps(reply), provider=self.name, model=self.model)


class ScriptedLLM:
    """Returns pre-written replies in order and remembers every prompt. For tests."""

    name = "scripted"
    model = "scripted"

    def __init__(self, replies: list[str | Exception]):
        self.replies = list(replies)
        self.prompts: list[str] = []

    def generate_json(self, system: str, prompt: str, schema: dict) -> LLMReply:
        self.prompts.append(prompt)
        if not self.replies:
            raise AssertionError("ScriptedLLM ran out of replies")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMReply(reply, provider=self.name, model=self.model)
