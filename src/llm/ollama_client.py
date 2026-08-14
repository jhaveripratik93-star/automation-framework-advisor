"""Ollama HTTP client for LLM completions.

Communicates with the local Ollama HTTP API for model listing,
health checking, and text generation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

SUPPORTED_MODELS = ("llama3", "mistral", "codellama")
FALLBACK_MODELS = ("llama3", "mistral", "codellama")

_MAX_RETRIES = 2
_RETRY_BACKOFF_SECONDS = 1.0


@dataclass
class HealthStatus:
    connected: bool
    available_models: list[str]
    selected_model: str
    error: str | None = None


class OllamaClient:
    def __init__(
        self,
        host: str = "http://localhost:11434",
        model: str = "llama3",
        temperature: float = 0.7,
        max_tokens: int = 256,
        top_p: float = 0.9,
    ) -> None:
        self.host = host.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.top_p = top_p
        self._timeout = 180.0  # llama3/mistral on CPU can take 2-3 min
        logger.debug("OllamaClient init: host=%s model=%s timeout=%ss", self.host, self.model, self._timeout)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def health_check(self) -> HealthStatus:
        logger.debug("OllamaClient.health_check: connecting to %s", self.host)
        try:
            models = self.list_models()
            logger.debug("OllamaClient.health_check: available_models=%s", models)
            selected = self._select_first_available_model(models)
            if selected:
                logger.info("OllamaClient.health_check: OK — selected_model=%s", selected)
                return HealthStatus(connected=True, available_models=models, selected_model=selected)
            else:
                logger.warning(
                    "OllamaClient.health_check: connected but no supported model found. "
                    "available=%s requires one of %s",
                    models, SUPPORTED_MODELS,
                )
                return HealthStatus(
                    connected=True,
                    available_models=models,
                    selected_model=self.model,
                    error="No supported model found (requires llama3, mistral, or codellama)",
                )
        except Exception as exc:
            logger.warning("OllamaClient.health_check failed: %s: %s", type(exc).__name__, exc)
            return HealthStatus(connected=False, available_models=[], selected_model=self.model, error=str(exc))

    def generate(
        self, 
        prompt: str, 
        system: str = "", 
        context: list[str] | None = None,
        tools: list[dict] | None = None
    ) -> str | dict:
        """Generate a response from Ollama, with optional tool calling support.
        
        Args:
            prompt: The user prompt
            system: System prompt
            context: Optional conversation context
            tools: Optional tool definitions for function calling (OpenAI format)
            
        Returns:
            - str: Regular text response
            - dict: Tool call response with structure {"type": "tool_calls", "tool_calls": [...]}
        """
        models_to_try = [self.model]
        for fallback in FALLBACK_MODELS:
            if fallback not in models_to_try:
                models_to_try.append(fallback)

        logger.debug(
            "OllamaClient.generate: prompt_len=%d system_len=%d models_to_try=%s tools=%s",
            len(prompt), len(system), models_to_try, "enabled" if tools else "disabled",
        )

        last_exception: Exception | None = None

        for model_name in models_to_try:
            payload: dict = {
                "model": model_name,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "num_predict": self.max_tokens,
                    "top_p": self.top_p,
                },
            }
            if system:
                payload["system"] = system
            if context:
                payload["context"] = context
            if tools:
                # Ollama supports tools in OpenAI format
                payload["tools"] = tools

            try:
                logger.debug("OllamaClient.generate: trying model=%s", model_name)
                result = self._request_with_retry(payload)
                logger.debug(
                    "OllamaClient.generate: success model=%s response_len=%d",
                    model_name, len(str(result)),
                )
                if model_name != self.model:
                    logger.info("OllamaClient.generate: switched model '%s' -> '%s'", self.model, model_name)
                    self.model = model_name
                return result
            except httpx.HTTPStatusError as exc:
                if self._is_model_not_found_error(exc):
                    logger.warning(
                        "OllamaClient.generate: model '%s' not found (HTTP %d), trying next fallback",
                        model_name, exc.response.status_code,
                    )
                    last_exception = exc
                    continue
                logger.error(
                    "OllamaClient.generate: HTTP %d for model '%s': %s",
                    exc.response.status_code, model_name, exc.response.text[:300],
                )
                raise
            except httpx.ConnectError as exc:
                logger.error("OllamaClient.generate: connection refused — is Ollama running? %s", exc)
                raise

        assert last_exception is not None
        raise last_exception

    def _request_with_retry(self, payload: dict) -> str | dict:
        """Make HTTP request with retry logic. Returns str or dict (for tool calls)."""
        last_exception: Exception | None = None

        for attempt in range(_MAX_RETRIES + 1):
            try:
                logger.debug(
                    "OllamaClient._request_with_retry: attempt=%d/%d model=%s",
                    attempt + 1, _MAX_RETRIES + 1, payload.get("model"),
                )
                response = httpx.post(
                    f"{self.host}/api/generate",
                    json=payload,
                    timeout=self._timeout,
                )
                response.raise_for_status()
                data = response.json()
                
                # Check if response contains tool calls
                if "message" in data and isinstance(data["message"], dict):
                    msg = data["message"]
                    if "tool_calls" in msg and msg["tool_calls"]:
                        # Model wants to call tools
                        logger.debug(
                            "OllamaClient._request_with_retry: tool_calls detected count=%d",
                            len(msg["tool_calls"])
                        )
                        return {
                            "type": "tool_calls",
                            "tool_calls": msg["tool_calls"],
                            "message": msg.get("content", "")
                        }
                
                # Regular text response
                text = data.get("response", "")
                logger.debug(
                    "OllamaClient._request_with_retry: got response len=%d done=%s",
                    len(text), data.get("done"),
                )
                return text
            except httpx.TimeoutException as exc:
                # Do NOT retry timeouts — model is slow, retrying wastes more time
                logger.error(
                    "OllamaClient._request_with_retry: timeout after %.0fs on model=%s — "
                    "consider switching to mistral or reducing max_tokens",
                    self._timeout, payload.get("model"),
                )
                raise
            except httpx.ConnectError as exc:
                last_exception = exc
                if attempt < _MAX_RETRIES:
                    logger.warning(
                        "OllamaClient._request_with_retry: attempt %d/%d connect error (%s). Retrying in %ss...",
                        attempt + 1, _MAX_RETRIES + 1, exc, _RETRY_BACKOFF_SECONDS,
                    )
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                else:
                    logger.error(
                        "OllamaClient._request_with_retry: all %d attempts failed. Last error: %s: %s",
                        _MAX_RETRIES + 1, type(exc).__name__, exc,
                    )
            except httpx.HTTPStatusError as exc:
                last_exception = exc
                if exc.response.status_code >= 500 and attempt < _MAX_RETRIES:
                    logger.warning(
                        "OllamaClient._request_with_retry: HTTP %d attempt %d/%d. Retrying in %ss...",
                        exc.response.status_code, attempt + 1, _MAX_RETRIES + 1, _RETRY_BACKOFF_SECONDS,
                    )
                    time.sleep(_RETRY_BACKOFF_SECONDS)
                else:
                    raise

        raise last_exception  # type: ignore[misc]

    def list_models(self) -> list[str]:
        logger.debug("OllamaClient.list_models: GET %s/api/tags", self.host)
        response = httpx.get(f"{self.host}/api/tags", timeout=self._timeout)
        response.raise_for_status()
        data = response.json()
        names = [m["name"] for m in data.get("models", []) if "name" in m]
        logger.debug("OllamaClient.list_models: found %d models: %s", len(names), names)
        return names

    @property
    def is_available(self) -> bool:
        status = self.health_check()
        available = status.connected and status.error is None
        logger.debug("OllamaClient.is_available: %s (connected=%s error=%s)", available, status.connected, status.error)
        return available

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_model_not_found_error(exc: httpx.HTTPStatusError) -> bool:
        if exc.response.status_code == 404:
            return True
        try:
            body = exc.response.text.lower()
            if "not found" in body and "model" in body:
                return True
        except Exception:
            pass
        return False

    @staticmethod
    def _select_first_available_model(available_models: list[str]) -> str | None:
        for fallback in FALLBACK_MODELS:
            for available in available_models:
                if fallback in available:
                    return available
        return None
