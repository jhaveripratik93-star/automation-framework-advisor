"""OpenRouter LLM client.

OpenRouter (https://openrouter.ai) is an OpenAI-compatible gateway that
fronts many models — including free, large-context, code-capable ones —
behind a single `/chat/completions` endpoint. It's a drop-in third
provider alongside GroqClient / GeminiClient: same chat() / generate()
interface, same _get_api_key()/is_available/model conventions, so every
call site written against GroqClient works unchanged when the user
selects OpenRouter as the active provider via the Settings dialog
(ui/settings_dialog.py).

Why it exists: Groq's free tier returns 413 (payload too large) on big
test-file conversions, and Gemini's free tier returns 429 (quota:0).
OpenRouter's free large-context models sidestep both.

Scope note: none of this codebase's call sites pass a real function/tool
-calling schema to `tools` (same as GroqClient/GeminiClient), so this
client only supports plain text chat/generate plus JSON response-format
mode — it deliberately does not implement function calling.
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

_API_URL = "https://openrouter.ai/api/v1/chat/completions"

# Default model: a FREE, large-context, code-capable OpenRouter model so
# the app solves the 413/429 problem out of the box. Free model ids end
# in ":free"; their exact ids and availability change over time, so this
# is a single named constant that's easy to edit, and the user can
# override it in Settings (OpenRouter tab) or via OPENROUTER_MODEL in
# config/.env.
_DEFAULT_MODEL = "deepseek/deepseek-chat-v3.1:free"

# OpenRouter's optional attribution headers — harmless, help the request
# show up under this app on the OpenRouter dashboard.
_REFERER = "https://github.com/automation-framework-advisor"
_TITLE = "automation-framework-advisor"

# Free models can be slow under load; give a generous per-attempt budget.
_TIMEOUT = 180.0


class OpenRouterClient:

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 8000,
    ) -> None:

        self._api_key_override = api_key
        self.model = model or os.getenv("OPENROUTER_MODEL", _DEFAULT_MODEL)
        self.temperature = temperature
        self.max_tokens = max_tokens

        # Cumulative token usage across all calls in a session
        self._session_prompt_tokens: int = 0
        self._session_completion_tokens: int = 0
        self._session_total_tokens: int = 0

        logger.info("OpenRouterClient initialized: model=%s", self.model)

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

        key = self._api_key_override or os.getenv("OPENROUTER_API_KEY", "")

        if not key:
            load_dotenv(_ENV_PATH, override=True)
            key = self._api_key_override or os.getenv("OPENROUTER_API_KEY", "")

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
        caller: str = "",
        response_format: dict[str, str] | None = None,
        temperature: float | None = None,
    ) -> dict:

        if tools:
            raise NotImplementedError(
                "OpenRouterClient does not implement function/tool calling. "
                "No call site in this codebase currently needs it (every "
                "GroqClient.chat() call passes tools=None) — if that "
                "changes, OpenRouter's OpenAI-compatible tool-calling API "
                "would need to be wired in here."
            )

        api_key = self._get_api_key()

        if not api_key:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. "
                "Set it in config/.env or as an environment variable."
            )

        # OpenAI chat shape: prepend the system message when `system` is
        # non-empty (unlike Gemini's separate systemInstruction field).
        all_messages: list[dict[str, str]] = []
        if system:
            all_messages.append({"role": "system", "content": system})
        all_messages.extend(
            {"role": m["role"], "content": m.get("content", "")}
            for m in messages
        )

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": all_messages,
            "temperature": temperature if temperature is not None else self.temperature,
            "max_tokens": max_tokens if max_tokens is not None else self.max_tokens,
        }

        # OpenRouter supports OpenAI's response_format passthrough.
        if response_format:
            payload["response_format"] = response_format

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": _REFERER,
            "X-Title": _TITLE,
        }

        try:
            logger.debug(
                "OpenRouter request: model=%s messages=%d",
                self.model,
                len(all_messages),
            )

            # Retry on transient failures: 429 (rate limit) and 5xx
            # (endpoint temporarily overloaded / upstream provider down).
            # A permanent error (other 4xx) breaks out immediately and is
            # raised below. Mirrors GeminiClient's resilience shape.
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
                        _API_URL,
                        headers=headers,
                        json=payload,
                        timeout=_TIMEOUT,
                    )
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if last_attempt:
                        raise
                    wait = 5.0 * (attempt + 1)
                    logger.warning(
                        "OpenRouterClient.chat: %s — waiting %.1fs (attempt %d/%d)",
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
                    "OpenRouterClient.chat: HTTP %d transient error — waiting %.1fs (attempt %d/%d)",
                    response.status_code, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)

            response.raise_for_status()

            data = response.json()

            usage = data.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

            self._session_prompt_tokens += prompt_tokens
            self._session_completion_tokens += completion_tokens
            self._session_total_tokens += total_tokens

            agent_label = f" agent={caller}" if caller else ""
            logger.info(
                "LLM TOKEN USAGE |%s model=%s | prompt=%s | completion=%s | total=%s",
                agent_label, self.model, prompt_tokens, completion_tokens, total_tokens,
            )

            content = (data["choices"][0]["message"].get("content") or "").strip()

            logger.debug(
                "OpenRouter response: model=%s response_len=%d",
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
                "OpenRouterClient.chat: HTTP %d — %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            raise

        except Exception as exc:
            logger.error(
                "OpenRouterClient.chat: %s: %s",
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
