"""Google Gemini LLM client.

Drop-in replacement for GroqClient — same chat() / generate() interface,
same _get_api_key()/is_available/model conventions, so every call site in
this codebase that was written against GroqClient (Advisor, Code Studio,
Test Generator, Framework Discovery, ...) works unchanged when the user
selects Gemini as the active provider via the Settings dialog
(ui/settings_dialog.py).

Scope note: none of this codebase's call sites pass a real function/tool
-calling schema to `tools` (grep confirms every call site passes either
no `tools` kwarg or `tools=None`), so this client only needs to support
plain text chat/generate plus JSON response-format mode — it deliberately
does not implement Gemini function calling.
"""

from __future__ import annotations

import logging
import os
import time
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

_API_BASE = "https://generativelanguage.googleapis.com/v1beta"
_DEFAULT_MODEL = "gemini-2.0-flash"
# Large test-file conversions under model load can take a while; 60s was
# too tight and produced ReadTimeouts. Allow more headroom per attempt.
_TIMEOUT = 180.0


class GeminiClient:

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8000,
    ) -> None:

        self._api_key_override = api_key
        self.model = model or os.getenv("GEMINI_MODEL", _DEFAULT_MODEL)
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Cumulative token usage across all calls in a session
        self._session_prompt_tokens: int = 0
        self._session_completion_tokens: int = 0
        self._session_total_tokens: int = 0

        logger.info("GeminiClient initialized: model=%s", self.model)

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

        key = self._api_key_override or os.getenv("GEMINI_API_KEY", "")

        if not key:
            load_dotenv(_ENV_PATH, override=True)
            key = self._api_key_override or os.getenv("GEMINI_API_KEY", "")

        return key

    @property
    def is_available(self) -> bool:
        return bool(self._get_api_key())

    # -----------------------------------------------------------------
    # Chat
    # -----------------------------------------------------------------

    @staticmethod
    def _to_gemini_contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Map our Groq-style {"role": "user"/"assistant", "content": ...}
        messages onto Gemini's {"role": "user"/"model", "parts": [...]}."""
        contents = []
        for m in messages:
            role = "model" if m.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
        return contents

    def chat(
        self,
        messages: list[dict[str, Any]],
        system: str = "",
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        caller: str = "",
        response_format: dict[str, str] | None = None,
        temperature: float | None = None,
    ) -> dict:

        if tools:
            raise NotImplementedError(
                "GeminiClient does not implement function/tool calling. "
                "No call site in this codebase currently needs it (every "
                "GroqClient.chat() call passes tools=None) — if that "
                "changes, Gemini's function-calling API would need to be "
                "wired in here."
            )

        api_key = self._get_api_key()

        if not api_key:
            raise RuntimeError(
                "GEMINI_API_KEY is not set. "
                "Set it in config/.env or as an environment variable."
            )

        generation_config: dict[str, Any] = {
            "temperature": temperature if temperature is not None else self.temperature,
            "maxOutputTokens": max_tokens if max_tokens is not None else self.max_tokens,
        }
        if response_format and response_format.get("type") == "json_object":
            generation_config["responseMimeType"] = "application/json"

        payload: dict[str, Any] = {
            "contents": self._to_gemini_contents(messages),
            "generationConfig": generation_config,
        }
        if system:
            payload["systemInstruction"] = {"parts": [{"text": system}]}

        url = f"{_API_BASE}/models/{self.model}:generateContent"

        try:
            logger.debug(
                "Gemini request: model=%s messages=%d",
                self.model,
                len(messages),
            )

            # Retry on transient failures: 429 (rate limit) and 5xx
            # (503 UNAVAILABLE / 500 / 502 / 504 — the model endpoint
            # temporarily overloaded). A permanent error (4xx other than
            # 429) breaks out immediately and is raised below.
            _TRANSIENT_STATUS = {429, 500, 502, 503, 504}
            max_retries = 5
            response = None
            for attempt in range(max_retries):
                last_attempt = attempt == max_retries - 1
                # Network-level transient failures (read/connect timeouts,
                # dropped connections) raise instead of returning a status,
                # so catch them here and retry with backoff just like a 5xx.
                try:
                    response = httpx.post(
                        url,
                        headers={
                            "x-goog-api-key": api_key,
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=_TIMEOUT,
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if last_attempt:
                        raise
                    wait = 5.0 * (attempt + 1)
                    logger.warning(
                        "GeminiClient.chat: %s — waiting %.1fs (attempt %d/%d)",
                        type(exc).__name__, wait, attempt + 1, max_retries,
                    )
                    time.sleep(wait)
                    continue

                if response.status_code not in _TRANSIENT_STATUS or last_attempt:
                    break
                # 429s can carry a long rate-limit window; 5xx overloads
                # are usually brief, so back off less for those.
                wait = 30.0 if response.status_code == 429 else 5.0 * (attempt + 1)
                retry_after = response.headers.get("retry-after")
                if retry_after:
                    try:
                        wait = float(retry_after) + 1.0
                    except ValueError:
                        pass
                logger.warning(
                    "GeminiClient.chat: HTTP %d transient error — waiting %.1fs (attempt %d/%d)",
                    response.status_code, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)

            response.raise_for_status()

            data = response.json()

            usage = data.get("usageMetadata", {})
            prompt_tokens = usage.get("promptTokenCount", 0)
            completion_tokens = usage.get("candidatesTokenCount", 0)
            total_tokens = usage.get("totalTokenCount", prompt_tokens + completion_tokens)

            self._session_prompt_tokens += prompt_tokens
            self._session_completion_tokens += completion_tokens
            self._session_total_tokens += total_tokens

            agent_label = f" agent={caller}" if caller else ""
            logger.info(
                "LLM TOKEN USAGE |%s model=%s | prompt=%s | completion=%s | total=%s",
                agent_label, self.model, prompt_tokens, completion_tokens, total_tokens,
            )

            candidates = data.get("candidates") or []
            content = ""
            if candidates:
                parts = candidates[0].get("content", {}).get("parts", [])
                content = "".join(p.get("text", "") for p in parts).strip()

            logger.debug(
                "Gemini response: model=%s response_len=%d",
                self.model,
                len(content),
            )

            return {
                "type": "text",
                "content": content,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "total_tokens": total_tokens,
                },
            }

        except httpx.HTTPStatusError as exc:
            logger.error(
                "GeminiClient.chat: HTTP %d — %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise

        except Exception as exc:
            logger.error(
                "GeminiClient.chat: %s: %s",
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
            messages=[{"role": "user", "content": prompt}],
            system=system,
        )

        return result.get("content", "")
