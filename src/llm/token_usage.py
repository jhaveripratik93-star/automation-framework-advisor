"""Token usage tracker for Groq API responses."""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    @classmethod
    def from_response(cls, response: dict) -> TokenUsage:
        usage = response.get("usage", {})
        return cls(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            total_tokens=usage.get("total_tokens", 0),
        )

    def print(self) -> None:
        print(f"\n--- Token Usage ---")
        print(f"  Prompt tokens    : {self.prompt_tokens}")
        print(f"  Completion tokens: {self.completion_tokens}")
        print(f"  Total tokens     : {self.total_tokens}")
        print(f"-------------------")
