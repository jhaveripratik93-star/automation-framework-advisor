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

import re
import time

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
        self.model = model or os.getenv("GROQ_MODEL", _DEFAULT_MODEL)
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Cumulative token usage across all calls in a session
        self._session_prompt_tokens: int = 0
        self._session_completion_tokens: int = 0
        self._session_total_tokens: int = 0

        logger.info("GroqClient initialized: model=%s", self.model)

    def reset_session_usage(self) -> None:
        """Reset cumulative token counters — call before each pipeline run."""
        self._session_prompt_tokens = 0
        self._session_completion_tokens = 0
        self._session_total_tokens = 0

    def get_session_usage(self) -> dict:
        """Return cumulative token usage since last reset."""
        return {
            "prompt_tokens": self._session_prompt_tokens,
            "completion_tokens": self._session_completion_tokens,
            "total_tokens": self._session_total_tokens,
        }

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
        max_tokens: int | None = None,
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
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
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

            max_retries = 3
            for attempt in range(max_retries):
                response = httpx.post(
                    f"{_API_BASE}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=_TIMEOUT,
                )
                if response.status_code != 429 or attempt == max_retries - 1:
                    break
                # Parse retry-after from error message, default to 5s
                wait = 5.0
                match = re.search(r"try again in ([\d.]+)s", response.text)
                if match:
                    wait = float(match.group(1)) + 0.5
                logger.warning(
                    "GroqClient.chat: 429 rate limit — waiting %.1fs (attempt %d/%d)",
                    wait, attempt + 1, max_retries,
                )
                time.sleep(wait)

            response.raise_for_status()

            data = response.json()

            # Capture token usage
            usage = data.get("usage", {})

            self._session_prompt_tokens     += usage.get("prompt_tokens", 0)
            self._session_completion_tokens += usage.get("completion_tokens", 0)
            self._session_total_tokens      += usage.get("total_tokens", 0)

            logger.info(
                "LLM TOKEN USAGE | model=%s | prompt=%s | completion=%s | total=%s",
                    self.model,
                    usage.get("prompt_tokens", 0),
                    usage.get("completion_tokens", 0),
                    usage.get("total_tokens", 0),
                )

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
                "usage": usage,
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