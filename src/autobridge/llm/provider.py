"""LLM provider protocol stub (W1)."""

from __future__ import annotations

from typing import Protocol


class LLMProvider(Protocol):
    def complete(self, messages: list[dict[str, str]], max_tokens: int = 1024) -> str: ...
