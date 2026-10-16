"""sampling.py — True MCP sampling (sampling/createMessage) for the
code-conversion tool, with an automatic fallback.

Point 2 of the conversion rewrite: "when configured in mcp config it
should use mcp's llm in backend but tool call should be done from the
automation-framework-advisor tool." This is the real mechanism — the MCP
server asks the CONNECTED CLIENT's LLM to do the generation via
`ctx.session.create_message()` (the standard MCP sampling request), so no
server-side API key is spent and the user's own configured model (e.g.
Kiro's) produces the code, while the tool call itself and all the
validation/repair logic stay owned by this server.

Why a fallback exists: MCP sampling requires the CLIENT to declare the
`sampling` capability during initialization (ClientCapabilities.sampling
in the MCP spec). Not every MCP client implements this today — if the
connected client didn't declare it, `ctx.session.check_client_capability`
returns False and we transparently fall back to the Groq-backed path
(server.py's existing manual-mode / Groq flow) instead of raising. The
caller never has to know which path ran; `make_llm_call()` returns one
callable either way.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["make_llm_call", "client_supports_sampling"]


def client_supports_sampling(ctx: Any) -> bool:
    """True if the connected MCP client declared the `sampling` capability.

    Returns False (never raises) if `ctx` doesn't have the expected shape
    (e.g. called outside a real request context, as in unit tests).
    """
    try:
        import mcp.types as types

        session = ctx.session
        return bool(session.check_client_capability(types.ClientCapabilities(sampling=types.SamplingCapability())))
    except Exception as exc:  # noqa: BLE001 — capability probing must never crash a tool call
        logger.debug("client_supports_sampling: could not determine capability (%s) — assuming False", exc)
        return False


async def _sample_via_mcp(ctx: Any, system_prompt: str, user_prompt: str, *, max_tokens: int = 4096) -> str:
    """Issue a real sampling/createMessage request to the connected client."""
    import mcp.types as types

    result = await ctx.session.create_message(
        messages=[
            types.SamplingMessage(
                role="user",
                content=types.TextContent(type="text", text=user_prompt),
            )
        ],
        max_tokens=max_tokens,
        system_prompt=system_prompt,
        temperature=0.1,
    )
    content = result.content
    if isinstance(content, list):
        texts = [c.text for c in content if getattr(c, "type", "") == "text"]
        return "\n".join(texts)
    return getattr(content, "text", str(content))


def _groq_llm_call(system_prompt: str, user_prompt: str) -> str:
    from mcp_server import services as svc

    client = svc.get_groq_client()
    if not client.is_available:
        raise RuntimeError(
            "Neither MCP sampling (client didn't declare the 'sampling' "
            "capability) nor GROQ_API_KEY is available. Configure one of "
            "the two to use code conversion from this MCP server."
        )
    result = client.chat(
        messages=[{"role": "user", "content": user_prompt}],
        system=system_prompt,
        temperature=0.1,
    )
    return result.get("content") or result.get("reasoning") or ""


def make_llm_call(ctx: Any | None) -> tuple[Any, str]:
    """Build a synchronous `(system_prompt, user_prompt) -> str` callable for
    src.conversion.service.convert(), preferring true MCP sampling and
    falling back to Groq when the client doesn't support it or `ctx` is
    unavailable (e.g. running outside an MCP request, or from Streamlit).

    IMPORTANT — threading model: `src.conversion.service.convert()` is a
    synchronous function with a retry loop, so the MCP tool wrapper must
    run it inside a worker thread via `anyio.to_thread.run_sync(convert,
    request)` (sampling itself is async). The "sampling" callable returned
    here therefore bridges back to the event loop per call via
    `anyio.from_thread.run(...)` — this ONLY works when the callable is
    itself invoked from inside such a worker thread. See
    mcp_server/server.py's convert_test_code tool for the calling pattern.

    Returns (llm_call, mode) where mode is "sampling" or "groq" — callers
    use this to report which path actually ran, for transparency.
    """
    if ctx is not None and client_supports_sampling(ctx):
        import anyio

        def _sampling_call(system_prompt: str, user_prompt: str) -> str:
            return anyio.from_thread.run(_sample_via_mcp, ctx, system_prompt, user_prompt)

        return _sampling_call, "sampling"

    return _groq_llm_call, "groq"
