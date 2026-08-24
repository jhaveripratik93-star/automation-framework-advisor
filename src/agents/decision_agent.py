"""Decision Agent — LLM-powered intent classifier with guardrails.

Uses the LLM to classify user queries into:
   - "tool_call"   : query needs data retrieval / computation
   - "direct"      : query can be answered from context alone
   - "rejected"    : query is out of scope for the Automation Framework Advisor

Strategy:
  1. Hard guardrails first (injection detection, length limits) — no LLM needed
  2. If LLM client available → ask the model to classify (reliable, handles nuance)
  3. Fallback to keyword heuristics only if LLM is unavailable or fails
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from src.agents.memory import AgentMemory
from src.agents.retry import llm_retry

if TYPE_CHECKING:
    from src.agents.state import AgentState

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# HARD GUARDRAILS — always enforced before LLM call
# ══════════════════════════════════════════════════════════════════════════

_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)",
    r"you\s+are\s+now\s+a",
    r"pretend\s+(to\s+be|you\s+are)",
    r"new\s+instructions?\s*:",
    r"system\s*prompt\s*:",
    r"forget\s+(everything|all|your\s+instructions)",
    r"override\s+(your|the)\s+(instructions|rules|guidelines)",
]

_REJECTION_MESSAGE = (
    "I'm the Automation Framework Advisor — I help engineering teams choose, "
    "compare, and migrate between test automation and infrastructure frameworks.\n\n"
    "I can help you with:\n"
    "- **Recommending** frameworks based on your project needs\n"
    "- **Comparing** frameworks (e.g., Playwright vs Cypress)\n"
    "- **Migration** planning between frameworks\n"
    "- **Evaluating** test coverage and capabilities\n"
    "- **Infrastructure** automation tools (Terraform, Ansible, etc.)\n\n"
    "Your question seems to be outside my area of expertise. "
    "Could you rephrase it in terms of automation frameworks or testing?"
)

_AMBIGUOUS_QUERIES = {
    "microservice ui": (
        "Are you asking about (a) UI automation for microservice apps, "
        "or (b) micro-frontend architecture?"
    ),
    "micro frontend": (
        "Do you want framework recommendations for testing micro-frontends, "
        "or building them?"
    ),
    "best framework": (
        "Best for what? (API testing / UI automation / performance / mobile)"
    ),
}


# ══════════════════════════════════════════════════════════════════════════
# LLM CLASSIFICATION PROMPT
# ══════════════════════════════════════════════════════════════════════════

_DECISION_SYSTEM_PROMPT = """\
You are an intent classifier for an Automation Framework Advisory system.

Your job: classify the user's message into exactly ONE of these categories:

1. "tool_call" — The user wants data, comparisons, recommendations, migration plans, \
framework details, coverage analysis, or any answer that requires retrieving structured \
information from a knowledge base.

2. "direct" — The user is greeting, thanking, asking a general explanation that can be \
answered from general knowledge about test automation, or asking a follow-up that doesn't \
need new data retrieval.

3. "rejected" — The user's question is completely unrelated to test automation, \
infrastructure automation, CI/CD, or framework selection (e.g., weather, recipes, \
creative writing, coding unrelated to testing).

IMPORTANT RULES:
- If in doubt between "tool_call" and "direct", choose "tool_call" (safer).
- Questions about ANY testing/automation topic are in-scope, even if they mention \
other technologies tangentially.
- Greetings and thanks are always "direct".
- "What is X?" where X is a framework → "tool_call" (we have specific data).
- "Explain how testing works in general" → "direct" (no specific data needed).

Respond with ONLY a JSON object, no other text:
{"action": "tool_call"|"direct"|"rejected", "reasoning": "one sentence explanation"}
"""


@dataclass
class DecisionResult:
    action: str          # "tool_call" | "direct" | "rejected" | "clarify"
    reasoning: str       # short explanation for logging
    rejection_message: str = ""   # populated only when action == "rejected"
    clarification: str | None = None  # populated only when action == "clarify"


class DecisionAgent:
    """LLM-powered decision agent with hard guardrails and heuristic fallback.

    Flow:
      1. Hard guardrails (injection, length) → reject immediately if triggered
      2. Ambiguity check → ask user to clarify if query is ambiguous
      3. LLM classification → ask the model to decide (handles nuance)
      4. Heuristic fallback → only used if LLM is unavailable/fails
    """

    def __init__(self, llm_client=None) -> None:
        self._client = llm_client
        self._memory = AgentMemory(agent_name="decision_agent", max_entries=20)

    def decide(self, user_message: str, graph_context: str = "") -> DecisionResult:
        """Classify the user message — LLM-first with guardrail pre-check."""
        msg_lower = user_message.lower().strip()

        # ── Hard guardrails (always enforced, no LLM needed) ──────────

        if len(user_message.strip()) < 2:
            result = DecisionResult(
                action="rejected",
                reasoning="guardrail: input too short",
                rejection_message="Please provide a more detailed question about automation frameworks.",
            )
            self._log_and_store(user_message, result)
            return result

        if len(user_message) > 5000:
            result = DecisionResult(
                action="rejected",
                reasoning="guardrail: input too long",
                rejection_message="Your message is too long. Please keep questions concise (under 5000 characters).",
            )
            self._log_and_store(user_message, result)
            return result

        for pattern in _INJECTION_PATTERNS:
            if re.search(pattern, msg_lower):
                result = DecisionResult(
                    action="rejected",
                    reasoning="guardrail: prompt injection attempt detected",
                    rejection_message=_REJECTION_MESSAGE,
                )
                logger.warning("DecisionAgent: BLOCKED injection attempt: '%.60s'", user_message)
                self._log_and_store(user_message, result)
                return result

        # ── Ambiguity detection ───────────────────────────────────────
        ambiguity_result = self._get_ambiguity(user_message)
        if ambiguity_result is not None:
            logger.info("DecisionAgent: ambiguity detected in query='%s'", user_message)
            self._log_and_store(user_message, ambiguity_result)
            return ambiguity_result

        # ── LLM-based classification (primary path) ───────────────────
        if self._client and hasattr(self._client, "is_available") and self._client.is_available:
            llm_result = self._classify_with_llm(user_message)
            if llm_result is not None:
                self._log_and_store(user_message, llm_result)
                return llm_result

        # ── Heuristic fallback (only if LLM unavailable or fails) ─────
        result = self._classify_heuristic(msg_lower, graph_context)
        self._log_and_store(user_message, result)
        return result

    def run_node(self, state: AgentState) -> dict[str, Any]:
        """LangGraph node entry point."""
        user_message = state["user_message"]
        graph_context = state.get("graph_context", "")
        result = self.decide(user_message, graph_context)

        output: dict[str, Any] = {
            "decision_action": result.action,
            "decision_reasoning": result.reasoning,
        }
        if result.action == "rejected":
            output["final_response"] = result.rejection_message
        return output

    @property
    def memory(self) -> AgentMemory:
        return self._memory

    # ──────────────────────────────────────────────────────────────────
    # Ambiguity detection
    # ──────────────────────────────────────────────────────────────────

    def _get_ambiguity(self, user_message: str) -> DecisionResult | None:
        """Check if the query matches a known ambiguous phrase."""
        try:
            message = user_message.lower().strip()
            for phrase, clarification in _AMBIGUOUS_QUERIES.items():
                if phrase in message:
                    return DecisionResult(
                        action="clarify",
                        reasoning="Query matches ambiguous topic",
                        clarification=clarification,
                    )
            return None
        except Exception as exc:
            logger.warning("DecisionAgent: ambiguity check failed (%s)", exc)
            return None

    # ──────────────────────────────────────────────────────────────────
    # LLM classification
    # ──────────────────────────────────────────────────────────────────

    @llm_retry(max_retries=2, initial_wait=0.5)
    def _call_llm(self, user_message: str) -> str:
        """Make the LLM call with retry on transient errors."""
        result = self._client.chat(
            messages=[{"role": "user", "content": user_message}],
            system=_DECISION_SYSTEM_PROMPT,
        )
        return result.get("content", "").strip()

    def _classify_with_llm(self, user_message: str) -> DecisionResult | None:
        """Ask the LLM to classify the intent. Returns None on failure."""
        try:
            raw = self._call_llm(user_message)
            parsed = self._parse_llm_response(raw)

            if parsed:
                logger.info(
                    "DecisionAgent (LLM): '%s' → %s (%s)",
                    user_message[:50], parsed.action, parsed.reasoning,
                )
                return parsed

            logger.warning("DecisionAgent: LLM returned unparseable response: '%.100s'", raw)
            return None

        except Exception as exc:
            logger.warning("DecisionAgent: LLM classification failed (%s), falling back to heuristics", exc)
            return None

    @staticmethod
    def _parse_llm_response(raw: str) -> DecisionResult | None:
        """Parse the JSON response from the LLM."""
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return None

        try:
            data = json.loads(json_match.group())
            action = data.get("action", "").lower().strip()
            reasoning = data.get("reasoning", "")

            if action not in ("tool_call", "direct", "rejected"):
                return None

            rejection_message = _REJECTION_MESSAGE if action == "rejected" else ""
            return DecisionResult(
                action=action,
                reasoning=f"llm: {reasoning}",
                rejection_message=rejection_message,
            )
        except (json.JSONDecodeError, KeyError):
            return None

    # ──────────────────────────────────────────────────────────────────
    # Heuristic fallback (used only when LLM is unavailable)
    # ──────────────────────────────────────────────────────────────────

    def _classify_heuristic(self, msg_lower: str, graph_context: str) -> DecisionResult:
        """Keyword-based fallback — only used when LLM is unavailable."""
        _TOOL_SIGNALS = [
            "compare", "comparison", "vs", "versus", "recommend", "best",
            "which framework", "migrate", "migration", "roadmap", "coverage",
            "analyze", "analyse", "details", "capabilities", "limitations",
            "score", "evaluate", "search", "find",
        ]
        _DIRECT_SIGNALS = [
            "hello", "hi", "hey", "thanks", "thank you",
            "what is", "explain", "tell me about", "how does", "why is",
        ]

        if any(t in msg_lower for t in _TOOL_SIGNALS):
            return DecisionResult(action="tool_call", reasoning="heuristic-fallback: tool-signal keyword")

        if any(t in msg_lower for t in _DIRECT_SIGNALS) and len(msg_lower) < 80:
            return DecisionResult(action="direct", reasoning="heuristic-fallback: direct-signal keyword")

        if graph_context and len(graph_context) > 200:
            return DecisionResult(action="direct", reasoning="heuristic-fallback: graph context sufficient")

        return DecisionResult(action="tool_call", reasoning="heuristic-fallback: default to tool_call")

    # ──────────────────────────────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────────────────────────────

    def _log_and_store(self, user_message: str, result: DecisionResult) -> None:
        self._memory.add(
            "decision",
            f"{user_message[:60]} → {result.action}",
            {"reasoning": result.reasoning},
        )
        logger.info("DecisionAgent: %s → %s (%s)", user_message[:60], result.action, result.reasoning)
