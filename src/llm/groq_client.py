"""Groq cloud LLM client.

Drop-in replacement for OllamaClient.

Uses the Groq REST API and exposes the same
chat() / generate() interface used by the agents.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv


logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------

_ENV_PATH = Path(__file__).resolve().parents[2] / "config" / ".env"

load_dotenv(_ENV_PATH, override=True)


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

_API_BASE = "https://api.groq.com/openai/v1"
_DEFAULT_MODEL = "openai/gpt-oss-120b"
_TIMEOUT = 60.0


class GroqClient:

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> None:

        self._api_key_override = api_key

        # IMPORTANT:
        # Read GROQ_MODEL when the client is created,
        # rather than using a function default evaluated at import time.
        self.model = model or os.getenv(
            "GROQ_MODEL",
            _DEFAULT_MODEL,
        )

        self.temperature = temperature
        self.max_tokens = max_tokens

        logger.info(
            "GroqClient initialized: model=%s",
            self.model,
        )

    # -----------------------------------------------------------------
    # API key
    # -----------------------------------------------------------------

    def _get_api_key(self) -> str:
        """Read API key fresh on every request."""

        key = self._api_key_override or os.getenv(
            "GROQ_API_KEY",
            "",
        )

        if not key:
            load_dotenv(
                _ENV_PATH,
                override=True,
            )

            key = self._api_key_override or os.getenv(
                "GROQ_API_KEY",
                "",
            )

        return key

    @property
    def is_available(self) -> bool:
        return bool(self._get_api_key())

    # -----------------------------------------------------------------
    # Chat
    # -----------------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict] | None = None,
    ) -> dict:

        api_key = self._get_api_key()

        if not api_key:
            raise RuntimeError(
                "GROQ_API_KEY is not set. "
                "Set it in config/.env or as an environment variable."
            )

        all_messages: list[dict[str, str]] = []

        if system:
            all_messages.append(
                {
                    "role": "system",
                    "content": system,
                }
            )

        all_messages.extend(
            {
                "role": m["role"],
                "content": m.get("content", ""),
            }
            for m in messages
        )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": all_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        # Add tools only when provided.
        if tools:
            payload["tools"] = tools

        try:

            logger.debug(
                "Groq request: model=%s messages=%d",
                self.model,
                len(all_messages),
            )

            response = httpx.post(
                f"{_API_BASE}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            message = data["choices"][0]["message"]

            content = (
                message.get("content")
                or message.get("reasoning")
                or ""
            )

            logger.debug(
                "Groq response: model=%s response_len=%d",
                self.model,
                len(content),
            )

            return {
                "type": "text",
                "content": content,
            }

        except httpx.HTTPStatusError as exc:

            logger.error(
                "GroqClient.chat: HTTP %d — %s",
                exc.response.status_code,
                exc.response.text[:500],
            )

            raise

        except Exception as exc:

            logger.error(
                "GroqClient.chat: %s: %s",
                type(exc).__name__,
                exc,
            )

            raise

    # -----------------------------------------------------------------
    # Generate
    # -----------------------------------------------------------------

    def generate(
        self,
        prompt: str,
        system: str = "",
        **_kwargs,
    ) -> str:

        result = self.chat(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            system=system,
        )

        return result.get("content", "")