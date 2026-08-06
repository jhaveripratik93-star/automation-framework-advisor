"""Groq cloud LLM client — drop-in replacement for OllamaClient.

Uses the Groq REST API (llama-3.1-70b-versatile by default).
Exposes the same chat() / generate() interface so all agents work unchanged.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / "config" / ".env")

logger = logging.getLogger(__name__)

_API_BASE = "https://api.groq.com/openai/v1"
_DEFAULT_MODEL = "llama-3.1-70b-versatile"
_TIMEOUT = 60.0


class GroqClient:
    def __init__(
        self,
        api_key: str | None = None,
        model: str = _DEFAULT_MODEL,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._api_key = api_key or os.getenv("GROQ_API_KEY", "")
        if not self._api_key:
            logger.warning("GroqClient: GROQ_API_KEY not set")

    # ------------------------------------------------------------------
    # Public API (matches OllamaClient interface)
    # ------------------------------------------------------------------

    @property
    def is_available(self) -> bool:
        return bool(self._api_key)

    def chat(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict] | None = None,
    ) -> dict:
        """Send a chat completion request to Groq.

        Returns dict with keys:
          - "type": "text"
          - "content": str
        """
        all_messages = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend({"role": m["role"], "content": m.get("content", "")} for m in messages)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": all_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        try:
            response = httpx.post(
                f"{_API_BASE}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=_TIMEOUT,
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            logger.debug("GroqClient.chat: response_len=%d model=%s", len(content), self.model)
            return {"type": "text", "content": content}
        except httpx.HTTPStatusError as exc:
            logger.error("GroqClient.chat: HTTP %d — %s", exc.response.status_code, exc.response.text[:300])
            raise
        except Exception as exc:
            logger.error("GroqClient.chat: %s: %s", type(exc).__name__, exc)
            raise

    def generate(self, prompt: str, system: str = "", **_kwargs) -> str:
        """Simple text generation — wraps chat() for compatibility."""
        result = self.chat(
            messages=[{"role": "user", "content": prompt}],
            system=system,
        )
        return result.get("content", "")
