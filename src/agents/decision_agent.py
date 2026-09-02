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
}


# ══════════════════════════════════════════════════════════════════════════
# LLM CLASSIFICATION PROMPT
# ══════════════════════════════════════════════════════════════════════════

_DECISION_SYSTEM_PROMPT = """\
You are an intent classifier for an Automation Framework Advisory system.

Your job: classify the user's message into exactly ONE of these categories:

1. "tool_call" — The user wants specific data that requires a knowledge base lookup:
   - Comparing named frameworks (e.g. "Playwright vs Cypress")
   - Asking for a recommendation WITH a specific use case (e.g. "best framework for Python API testing")
   - Migration planning between named frameworks
   - Coverage analysis for specific test cases
   - Details about a specific named framework
   - Asking which frameworks belong to a category

2. "direct" — Answer from general knowledge, NO data lookup needed:
   - Greetings, thanks, small talk
   - Vague/casual questions without a specific use case (e.g. "what is a good test framework?", \
"what is the best automation tool?", "which framework is awesome?")
   - General explanations about testing concepts
   - Follow-up clarifications that don't need new data

3. "rejected" — Completely unrelated to test automation, infrastructure automation, CI/CD, \
or framework selection (e.g. weather, recipes, creative writing).

CRITICAL RULES:
- Vague recommendation questions WITHOUT a specific use case → "direct". \
Examples: "what is a good framework?", "which is the best tool?", "what framework should I use?" → "direct".
- Specific recommendation questions WITH a use case → "tool_call". \
Examples: "best framework for React SPA with Python", "recommend for API testing" → "tool_call".
- "What is X?" where X is a named framework → "tool_call".
- "What is a test framework?" (generic concept) → "direct".
- Greetings and thanks are always "direct".
- If genuinely unsure, prefer "direct" over triggering an unnecessary tool call.

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
    """Intent classifier with heuristics-first strategy.

    Flow:
      1. Hard guardrails (injection, length) → reject immediately
      2. Ambiguity check → clarify if query is ambiguous
      3. Greeting/small-talk heuristic → direct (zero tokens)
      4. Keyword heuristics → classify obvious queries (zero tokens)
      5. LLM classification → only for ambiguous/uncertain cases
    """

    def __init__(self, llm_client=None) -> None:
        self._client = llm_client
        self._memory = AgentMemory(agent_name="decision_agent", max_entries=20)

    def decide(self, user_message: str, graph_context: str = "", conversation_history: list[dict[str, str]] | None = None) -> DecisionResult:
        """Classify the user message — heuristics-first, LLM only for edge cases."""
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

        # ── Fast heuristic for greetings/small-talk (no LLM needed) ───
        if self._is_greeting_or_smalltalk(msg_lower):
            result = DecisionResult(action="direct", reasoning="heuristic: greeting/small-talk")
            self._log_and_store(user_message, result)
            return result

        # ── Keyword heuristics (handles 80%+ of queries, zero tokens) ─
        heuristic_result = self._classify_heuristic(msg_lower, graph_context)
        if "default" not in heuristic_result.reasoning:
            # Heuristic matched a clear signal — use it, skip LLM
            self._log_and_store(user_message, heuristic_result)
            return heuristic_result

        # ── LLM escalation (only for uncertain/ambiguous cases) ───────
        if self._client and hasattr(self._client, "is_available") and self._client.is_available:
            llm_input = user_message
            if conversation_history and len(user_message.strip()) < 80:
                recent = conversation_history[-4:]
                context_lines = [f"{t['role']}: {t['content'][:150]}" for t in recent]
                llm_input = "[Conversation context]\n" + "\n".join(context_lines) + f"\n\n[Current message] {user_message}"

            llm_result = self._classify_with_llm(llm_input)
            if llm_result is not None:
                self._log_and_store(user_message, llm_result)
                return llm_result

        # ── Final fallback: heuristic default (tool_call) ─────────────
        self._log_and_store(user_message, heuristic_result)
        return heuristic_result

    def run_node(self, state: AgentState) -> dict[str, Any]:
        """LangGraph node entry point."""
        user_message = state["user_message"]
        graph_context = state.get("graph_context", "")
        conversation_history = state.get("conversation_history", [])
        result = self.decide(user_message, graph_context, conversation_history=conversation_history)

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

    @staticmethod
    def _is_greeting_or_smalltalk(msg_lower: str) -> bool:
        """Detect greetings and small-talk that need no LLM or tools.

        Catches: hi, hello, hey, thanks, how are you, what's up, etc.
        Only triggers for short messages (< 50 chars) to avoid false
        positives on real queries that happen to start with 'hi'.
        """
        if len(msg_lower) > 50:
            return False

        _GREETING_PATTERNS = [
            r"^h(i|ello|ey|owdy)[\s!.,?]*$",
            r"^(good\s+)?(morning|afternoon|evening|night)[\s!.,?]*$",
            r"^how\s+(are|r)\s+(you|u|ya)[\s!.,?]*$",
            r"^(hi|hello|hey)\s+(how\s+(are|r)\s+(you|u|ya)|there|everyone)[\s!.,?]*$",
            r"^what'?s?\s+up[\s!.,?]*$",
            r"^sup[\s!.,?]*$",
            r"^yo[\s!.,?]*$",
            r"^thanks?(\s+you)?[\s!.,?]*$",
            r"^thank\s+you[\s!.,?]*$",
            r"^(ok|okay|cool|great|nice|awesome|got\s+it)[\s!.,?]*$",
            r"^bye[\s!.,?]*$",
            r"^(good)?bye[\s!.,?]*$",
        ]

        return any(re.match(p, msg_lower) for p in _GREETING_PATTERNS)

    # ──────────────────────────────────────────────────────────────────
    # LLM classification
    # ──────────────────────────────────────────────────────────────────

    @llm_retry(max_retries=2, initial_wait=0.5)
    def _call_llm(self, user_message: str) -> str:
        """Make the LLM call with retry on transient errors.
        Uses response_format=json_object to force valid JSON output."""
        result = self._client.chat(
            messages=[{"role": "user", "content": user_message}],
            system=_DECISION_SYSTEM_PROMPT,
            max_tokens=150,
            caller="DecisionAgent",
            response_format={"type": "json_object"},
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
            "which framework", "which tool", "suitable", "should i use",
            "migrate", "migration", "roadmap", "coverage",
            "analyze", "analyse", "details", "capabilities", "limitations",
            "score", "evaluate", "search", "find", "suggest",
        ]
        _DIRECT_SIGNALS = [
            "hello", "hi", "hey", "thanks", "thank you",
            "what is", "explain", "tell me about", "how does", "why is",
        ]

        if any(t in msg_lower for t in _TOOL_SIGNALS):
            return DecisionResult(action="tool_call", reasoning="heuristic-fallback: tool-signal keyword")

        # A query naming a framework/testing subject implies a data lookup
        _SUBJECT_SIGNALS = [
            "framework", "framwork", "automation", "testing", "test",
            "api", "graphql", "microservice", "selenium", "playwright",
            "cypress", "appium", "karate", "robot",
        ]
        if any(t in msg_lower for t in _SUBJECT_SIGNALS):
            return DecisionResult(action="tool_call", reasoning="heuristic-fallback: framework/testing subject")

        if any(t in msg_lower for t in _DIRECT_SIGNALS) and len(msg_lower) < 80:
            return DecisionResult(action="direct", reasoning="heuristic-fallback: direct-signal keyword")

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
