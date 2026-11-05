"""Tests for src/llm/openrouter_client.py and the OpenRouter provider
dispatch in services/app_services.py.

All HTTP is monkeypatched — no network. The OpenRouter client is
OpenAI-compatible, so these assert the OpenAI request/response shape
(system message prepended, Bearer auth, choices[0].message.content),
the fresh-key is_available behaviour, the 429-then-200 retry, and that
get_llm_client() dispatches to OpenRouterClient.
"""
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.llm.openrouter_client import OpenRouterClient


class _FakeResponse:
    """Minimal stand-in for httpx.Response."""

    def __init__(self, status_code=200, json_data=None, text="", headers=None):
        self.status_code = status_code
        self._json = json_data or {}
        self.text = text
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions"),
                response=httpx.Response(self.status_code, text=self.text),
            )


def _ok_payload(content="hello world"):
    return {
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


def test_chat_parses_openai_shape(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return _FakeResponse(200, _ok_payload("the answer"))

    monkeypatch.setattr(httpx, "post", fake_post)

    client = OpenRouterClient(model="some/model:free")
    result = client.chat(
        messages=[{"role": "user", "content": "hi"}],
        system="you are helpful",
    )

    assert result == {
        "type": "text",
        "content": "the answer",
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }

    # Endpoint + Bearer auth header sent correctly.
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer sk-or-test"

    # Model + messages (system prepended OpenAI-style) sent correctly.
    body = captured["json"]
    assert body["model"] == "some/model:free"
    assert body["messages"][0] == {"role": "system", "content": "you are helpful"}
    assert body["messages"][1] == {"role": "user", "content": "hi"}


def test_chat_no_system_message_when_empty(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, _ok_payload())

    monkeypatch.setattr(httpx, "post", fake_post)

    OpenRouterClient().chat(messages=[{"role": "user", "content": "hi"}])
    assert captured["json"]["messages"] == [{"role": "user", "content": "hi"}]


def test_response_format_passthrough(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["json"] = json
        return _FakeResponse(200, _ok_payload("{}"))

    monkeypatch.setattr(httpx, "post", fake_post)

    OpenRouterClient().chat(
        messages=[{"role": "user", "content": "give json"}],
        response_format={"type": "json_object"},
    )
    assert captured["json"]["response_format"] == {"type": "json_object"}


def test_is_available_reflects_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    # Also neutralise any config/.env reload so the unset case is honest.
    monkeypatch.setattr(
        "src.llm.openrouter_client.load_dotenv", lambda *a, **k: None
    )
    assert OpenRouterClient().is_available is False

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    assert OpenRouterClient().is_available is True


def test_retry_on_429_then_success(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr("src.llm.openrouter_client.time.sleep", lambda *_: None)

    calls = {"n": 0}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FakeResponse(429, text="rate limited", headers={"retry-after": "0"})
        return _FakeResponse(200, _ok_payload("after retry"))

    monkeypatch.setattr(httpx, "post", fake_post)

    result = OpenRouterClient().chat(messages=[{"role": "user", "content": "hi"}])
    assert calls["n"] == 2
    assert result["content"] == "after retry"


def test_generate_returns_content_string(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setattr(
        httpx, "post",
        lambda *a, **k: _FakeResponse(200, _ok_payload("generated text")),
    )
    assert OpenRouterClient().generate("prompt") == "generated text"


def test_tools_raises_not_implemented(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    with pytest.raises(NotImplementedError):
        OpenRouterClient().chat(
            messages=[{"role": "user", "content": "hi"}],
            tools=[{"type": "function", "function": {"name": "x"}}],
        )


def test_missing_key_raises(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(
        "src.llm.openrouter_client.load_dotenv", lambda *a, **k: None
    )
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouterClient().chat(messages=[{"role": "user", "content": "hi"}])


def test_get_llm_client_dispatches_to_openrouter(monkeypatch):
    """get_llm_client() returns an OpenRouterClient when the active
    provider is 'openrouter'. streamlit.session_state is mocked."""
    import services.app_services as app_services

    fake_session = {"llm_provider": "openrouter"}
    monkeypatch.setattr(app_services.st, "session_state", fake_session)
    # Bypass @st.cache_resource — call the undecorated builder directly so
    # we don't need a live Streamlit script context.
    monkeypatch.setattr(
        app_services, "get_openrouter_client", lambda: OpenRouterClient()
    )

    client = app_services.get_llm_client()
    assert isinstance(client, OpenRouterClient)
