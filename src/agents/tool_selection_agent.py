"""Tool Selection Agent — LLM-powered tool router with structured output.

Given a user query and available tools, uses the LLM to decide:
- Which tool(s) to call
- What arguments to pass to each tool
- Why that tool was selected

Strategy:
  1. Build a tool registry prompt describing each available tool and its parameters
  2. Ask the LLM to select tool(s) and extract arguments (JSON structured output)
  3. Fall back to keyword heuristics only if LLM is unavailable or fails
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from src.agents.memory import AgentMemory
from src.agents.retry import llm_retry

if TYPE_CHECKING:
    from src.agents.state import AgentState

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════
# TOOL DEFINITIONS — describe each tool for the LLM
# ══════════════════════════════════════════════════════════════════════════

_TOOL_DEFINITIONS = {
    "recommend_frameworks": {
        "description": (
            "Recommend frameworks based on a use case or requirement. "
            "Use when the user asks for suggestions, recommendations, or wants "
            "to know which framework is best for their needs."
        ),
        "parameters": {
            "use_case": "string — description of what the user needs (e.g., 'api testing for microservices')",
            "required_capability": (
                "string|null — specific capability filter: api_testing, browser_testing, "
                "mobile_testing, performance_testing, load_testing, ui_testing, graphql_support "
                "(null if no specific capability)"
            ),
        },
    },
    "run_framework_comparison": {
        "description": (
            "Compare two or more specific frameworks side-by-side. "
            "Use when the user names multiple frameworks and wants to see differences."
        ),
        "parameters": {
            "frameworks": "list[string] — framework names to compare (2-4 names, title-cased)",
        },
    },
    "get_framework_details": {
        "description": (
            "Get detailed information about a specific framework (capabilities, languages, limitations). "
            "Use when the user asks about a single framework."
        ),
        "parameters": {
            "framework_name": "string — the framework name (title-cased, e.g., 'Playwright', 'Cypress')",
        },
    },
    "find_migration_paths": {
        "description": (
            "Find migration paths from one framework to another. "
            "Use when the user asks about migrating, switching, or moving between frameworks."
        ),
        "parameters": {
            "from_framework": "string — source framework name (title-cased)",
            "to_frameworks": "list[string]|null — target frameworks (null to show all options)",
        },
    },
    "analyze_test_case_coverage": {
        "description": (
            "Analyze test case coverage across frameworks. Use when the user asks about "
            "test coverage, which frameworks support their test cases, or coverage analysis."
        ),
        "parameters": {
            "test_cases": "list[dict] — test case objects (can be empty for general analysis)",
            "frameworks": "list[string]|null — specific frameworks to check (null for all)",
        },
    },
    "search_knowledge_graph": {
        "description": (
            "Search the knowledge graph for entities and relationships. "
            "Use when the user asks to search, find, or look up specific information "
            "not covered by other tools."
        ),
        "parameters": {
            "query": "string — the search query",
            "entity_types": "list[string] — types to search: Framework, Capability, Language, CI_CD_Tool, Cloud_Provider",
        },
    },
    "analyze_uploaded_content": {
        "description": (
            "Analyze uploaded documents or case study files. "
            "Use when the user mentions uploaded files, documents, or case studies."
        ),
        "parameters": {
            "search_term": "string — what to look for in the documents",
            "document_type": "string — 'test_files', 'case_study', or 'all'",
        },
    },
    "convert_test_cases": {
        "description": (
            "Convert test code from one framework to another. "
            "Use when the user asks to convert, translate, rewrite, or port tests."
        ),
        "parameters": {
            "source_code": "string — the test source code to convert",
            "from_framework": "string — source framework name",
            "to_framework": "string — target framework name",
        },
    },
    "list_frameworks_by_category": {
        "description": (
            "List all frameworks grouped by category, or filter to a specific category. "
            "Use when the user asks which frameworks belong to a category (e.g. 'cloud', "
            "'infrastructure', 'mobile', 'performance'), or asks to list/show all frameworks."
        ),
        "parameters": {
            "category": "string|null — category to filter by (e.g. 'cloud_infrastructure', "
                        "'web_testing', 'mobile', 'performance') or null to list all categories",
        },
    },
}


# ══════════════════════════════════════════════════════════════════════════
# LLM TOOL SELECTION PROMPT
# ══════════════════════════════════════════════════════════════════════════

_TOOL_SELECTION_SYSTEM_PROMPT = """\
You are a tool selection agent for an Automation Framework Advisory system.

Given the user's query, select the most appropriate tool(s) to call and determine \
the correct arguments. You may select 1-3 tools if multiple are needed.

## Available Tools:
{tool_descriptions}

## Rules:
1. Select the MOST SPECIFIC tool for the query. Don't use "search_knowledge_graph" \
if a more specific tool (like "get_framework_details") fits better.
2. Extract framework names exactly as they appear. Use title case (e.g., "Playwright", not "playwright").
3. For comparisons, you MUST have at least 2 framework names. If only 1 is mentioned, \
use "get_framework_details" instead.
4. For recommendations, extract the use case and map it to a capability if possible.
5. You can select multiple tools if the query has multiple intents \
(e.g., "compare Playwright and Cypress and recommend the best for API testing" → \
comparison + recommendation).
6. For queries like "which are api frameworks", "list cloud frameworks", "show mobile frameworks", \
"what are performance testing tools" → use "list_frameworks_by_category" with the matching category \
(api, cloud, mobile, performance, web, etc.). Do NOT use recommend_frameworks for these.

## Known Framework Names:
Playwright, Cypress, Selenium, WebdriverIO, Robot Framework, TestCafe, Puppeteer, \
Appium, Karate, K6, Locust, Rest Assured, Terraform, Ansible, Chef, Pulumi, AWS CloudFormation

## Respond with ONLY a JSON object:
{{"tool_calls": [{{"tool_name": "...", "arguments": {{...}}, "reasoning": "..."}}], \
"overall_reasoning": "one sentence summary"}}
"""


# ══════════════════════════════════════════════════════════════════════════
# INTENT CONSTANTS (preserved from original)
# ══════════════════════════════════════════════════════════════════════════

INTENT_RECOMMENDATION = "recommendation"
INTENT_MIGRATION = "migration"
INTENT_CLOUD = "cloud"
INTENT_COMPARISON = "comparison"
INTENT_COVERAGE = "coverage"
INTENT_DETAILS = "details"
INTENT_CONVERT = "convert"

CLOUD_PROVIDERS = {
    "aws": "AWS",
    "amazon web services": "AWS",
    "azure": "Azure",
    "microsoft azure": "Azure",
    "gcp": "GCP",
    "google cloud": "GCP",
}

_INTENT_MAP = [
    (
        [
            "micro frontend",
            "microfrontend",
            "microservice ui",
            "microservice frontend",
        ],
        "recommend_frameworks",
    ),
]

_USE_CASE_MAP = {
    "micro frontend": "micro-frontend UI automation",
    "microfrontend": "micro-frontend UI automation",
    "microservice ui": "micro-frontend UI automation",
    "microservice frontend": "micro-frontend UI automation",
}


# ══════════════════════════════════════════════════════════════════════════
# DATA CLASSES
# ══════════════════════════════════════════════════════════════════════════

@dataclass
class ToolCall:
    tool_name: str
    arguments: dict
    reasoning: str = ""


@dataclass
class SelectionResult:
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""


@dataclass
class QueryEvaluation:
    domain: str | None = None
    intents: list[str] = field(default_factory=list)
    use_case: str | None = None
    from_framework: str | None = None
    to_frameworks: list[str] = field(default_factory=list)
    cloud_provider: str | None = None
    cloud_intent: str | None = None
    confidence: float = 0.0


# ══════════════════════════════════════════════════════════════════════════
# AGENT CLASS
# ══════════════════════════════════════════════════════════════════════════

class ToolSelectionAgent:
    """LLM-powered tool selection with structured JSON output.

    Uses the LLM to understand query intent, extract entities (framework names,
    capabilities), and select the appropriate tool(s) with correct arguments.

    Falls back to keyword heuristics if LLM is unavailable.
    """

    def __init__(self, llm_client=None) -> None:
        self._client = llm_client
        self._memory = AgentMemory(agent_name="tool_selection_agent", max_entries=20)

    # ──────────────────────────────────────────────────────────────────
    # Main selection method
    # ──────────────────────────────────────────────────────────────────

    def select(self, user_message: str, available_tools: list[str]) -> SelectionResult:
        """Select tools — LLM-first with heuristic fallback."""

        # ── LLM-based selection (primary path) ────────────────────────
        if self._client and hasattr(self._client, "is_available") and self._client.is_available:
            llm_result = self._select_with_llm(user_message, available_tools)
            if llm_result is not None and llm_result.tool_calls:
                self._memory.add(
                    "selection",
                    llm_result.reasoning,
                    {
                        "tools": [tc.tool_name for tc in llm_result.tool_calls],
                        "method": "llm",
                    },
                )
                logger.info("ToolSelectionAgent (LLM): %s", llm_result.reasoning)
                return llm_result

        # ── Heuristic fallback ────────────────────────────────────────
        result = self._select_heuristic(user_message, available_tools)
        self._memory.add(
            "selection",
            result.reasoning,
            {
                "tools": [tc.tool_name for tc in result.tool_calls],
                "method": "heuristic-fallback",
            },
        )
        logger.info("ToolSelectionAgent (heuristic): %s", result.reasoning)
        return result

    def run_node(self, state: AgentState) -> dict[str, Any]:
        """LangGraph node entry point."""
        user_message = state["user_message"]
        available_tools = state.get("_available_tools", [])

        if state.get("synthesis_verdict") and state.get("synthesis_needs_more"):
            user_message = f"{user_message}\n\n[Synthesis feedback]: {state['synthesis_verdict']}"

        result = self.select(user_message, available_tools)
        return {
            "selected_tools": [
                {"tool_name": tc.tool_name, "arguments": tc.arguments, "reasoning": tc.reasoning}
                for tc in result.tool_calls
            ],
        }

    @property
    def memory(self) -> AgentMemory:
        return self._memory

    # ──────────────────────────────────────────────────────────────────
    # LLM-based tool selection
    # ──────────────────────────────────────────────────────────────────

    @llm_retry(max_retries=2, initial_wait=0.5)
    def _call_llm(self, user_message: str, system_prompt: str) -> str:
        """Make the LLM call with retry."""
        result = self._client.chat(
            messages=[{"role": "user", "content": user_message}],
            system=system_prompt,
            max_tokens=200,
        )
        return result.get("content", "").strip()

    def _select_with_llm(self, user_message: str, available_tools: list[str]) -> SelectionResult | None:
        """Ask the LLM to select tools. Returns None on failure."""
        # Build tool descriptions for only the available tools
        tool_desc_lines = []
        for tool_name, tool_def in _TOOL_DEFINITIONS.items():
            if tool_name not in available_tools:
                continue
            params = ", ".join(f"{k}: {v}" for k, v in tool_def["parameters"].items())
            tool_desc_lines.append(
                f"- **{tool_name}**: {tool_def['description']}\n  Parameters: {params}"
            )

        if not tool_desc_lines:
            return None

        tool_descriptions = "\n".join(tool_desc_lines)
        system_prompt = _TOOL_SELECTION_SYSTEM_PROMPT.format(tool_descriptions=tool_descriptions)

        try:
            raw = self._call_llm(user_message, system_prompt)
            parsed = self._parse_llm_response(raw, available_tools)

            if parsed:
                logger.info(
                    "ToolSelectionAgent (LLM): query='%.50s' → %d tool(s): %s",
                    user_message, len(parsed.tool_calls),
                    [tc.tool_name for tc in parsed.tool_calls],
                )
                return parsed

            logger.warning("ToolSelectionAgent: LLM returned unparseable: '%.100s'", raw)
            return None

        except Exception as exc:
            logger.warning("ToolSelectionAgent: LLM selection failed (%s), falling back", exc)
            return None

    @staticmethod
    def _parse_llm_response(raw: str, available_tools: list[str]) -> SelectionResult | None:
        """Parse the structured JSON response from the LLM."""
        json_match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not json_match:
            return None

        try:
            data = json.loads(json_match.group())
            tool_calls_data = data.get("tool_calls", [])
            overall_reasoning = data.get("overall_reasoning", "")

            if not tool_calls_data:
                return None

            tool_calls = []
            for tc_data in tool_calls_data:
                tool_name = tc_data.get("tool_name", "")
                arguments = tc_data.get("arguments", {})
                reasoning = tc_data.get("reasoning", "")

                # Validate tool name is in available tools
                if tool_name not in available_tools:
                    logger.debug(
                        "ToolSelectionAgent: LLM selected unavailable tool '%s', skipping",
                        tool_name,
                    )
                    continue

                tool_calls.append(ToolCall(
                    tool_name=tool_name,
                    arguments=arguments,
                    reasoning=f"llm: {reasoning}",
                ))

            if not tool_calls:
                return None

            return SelectionResult(
                tool_calls=tool_calls,
                reasoning=f"llm: {overall_reasoning} → {[tc.tool_name for tc in tool_calls]}",
            )

        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.debug("ToolSelectionAgent: JSON parse error: %s", exc)
            return None

    # ──────────────────────────────────────────────────────────────────
    # Heuristic fallback (keyword-based, used only when LLM unavailable)
    # ──────────────────────────────────────────────────────────────────

    def _select_heuristic(self, user_message: str, available_tools: list[str]) -> SelectionResult:
        """Keyword-based tool selection — fallback only."""
        msg_lower = user_message.lower()
        tool_calls: list[ToolCall] = []

        _KNOWN_FRAMEWORKS = [
            "playwright", "cypress", "selenium", "webdriverio", "robot framework",
            "testcafe", "puppeteer", "appium", "karate", "k6", "locust",
            "rest assured", "terraform", "ansible", "chef", "pulumi",
            "aws cloudformation",
        ]

        _HEURISTIC_USE_CASE_MAP = {
            "api": "api_testing", "microservice": "api_testing", "rest": "api_testing",
            "graphql": "graphql_support", "web": "browser_testing", "browser": "browser_testing",
            "mobile": "mobile_testing", "performance": "performance_testing",
            "load": "load_testing", "ui": "ui_testing",
        }

        mentioned = [fw for fw in _KNOWN_FRAMEWORKS if fw in msg_lower]

        # Intent matching
        if any(kw in msg_lower for kw in ["compare", "vs", "versus", "difference between"]):
            if len(mentioned) >= 2 and "run_framework_comparison" in available_tools:
                tool_calls.append(ToolCall(
                    tool_name="run_framework_comparison",
                    arguments={"frameworks": [f.title() for f in mentioned[:4]]},
                    reasoning="heuristic: comparison with named frameworks",
                ))
            elif "recommend_frameworks" in available_tools:
                cap = next((c for k, c in _HEURISTIC_USE_CASE_MAP.items() if k in msg_lower), None)
                tool_calls.append(ToolCall(
                    tool_name="recommend_frameworks",
                    arguments={"use_case": msg_lower[:120], "required_capability": cap},
                    reasoning="heuristic: comparison without enough named frameworks",
                ))

        elif any(kw in msg_lower for kw in ["migrate", "migration", "path from", "move from"]):
            if mentioned and "find_migration_paths" in available_tools:
                tool_calls.append(ToolCall(
                    tool_name="find_migration_paths",
                    arguments={
                        "from_framework": mentioned[0].title(),
                        "to_frameworks": [f.title() for f in mentioned[1:]] or None,
                    },
                    reasoning="heuristic: migration query",
                ))

        elif any(kw in msg_lower for kw in ["convert", "translate", "rewrite", "port"]):
            if len(mentioned) >= 2 and "convert_test_cases" in available_tools:
                tool_calls.append(ToolCall(
                    tool_name="convert_test_cases",
                    arguments={
                        "source_code": "# paste or upload test code",
                        "from_framework": mentioned[0].title(),
                        "to_framework": mentioned[1].title(),
                    },
                    reasoning="heuristic: test case conversion",
                ))

        elif any(kw in msg_lower for kw in ["coverage", "test case", "analyze test"]):
            if "analyze_test_case_coverage" in available_tools:
                tool_calls.append(ToolCall(
                    tool_name="analyze_test_case_coverage",
                    arguments={"test_cases": [], "frameworks": [f.title() for f in mentioned] or None},
                    reasoning="heuristic: coverage query",
                ))

        elif any(kw in msg_lower for kw in ["uploaded", "file", "case study", "document"]):
            if "analyze_uploaded_content" in available_tools:
                tool_calls.append(ToolCall(
                    tool_name="analyze_uploaded_content",
                    arguments={"search_term": user_message[:100], "document_type": "all"},
                    reasoning="heuristic: uploaded content query",
                ))

        elif any(kw in msg_lower for kw in ["details", "capabilities", "about", "what is"]):
            if mentioned and "get_framework_details" in available_tools:
                for fw in mentioned[:2]:
                    tool_calls.append(ToolCall(
                        tool_name="get_framework_details",
                        arguments={"framework_name": fw.title()},
                        reasoning=f"heuristic: details for {fw}",
                    ))

        elif any(kw in msg_lower for kw in ["list", "show all", "what are", "which are",
                                             "category", "categories"]):
            if "list_frameworks_by_category" in available_tools:
                cat = None
                for kw in ["api", "cloud", "infrastructure", "mobile", "performance",
                           "load", "web", "ui", "browser", "iac", "devops"]:
                    if kw in msg_lower:
                        cat = kw
                        break
                tool_calls.append(ToolCall(
                    tool_name="list_frameworks_by_category",
                    arguments={"category": cat},
                    reasoning=f"heuristic: category listing query (cat={cat})",
                ))

        elif any(kw in msg_lower for kw in ["best", "recommend", "which framework", "suggest"]):
            if "recommend_frameworks" in available_tools:
                cap = next((c for k, c in _HEURISTIC_USE_CASE_MAP.items() if k in msg_lower), None)
                tool_calls.append(ToolCall(
                    tool_name="recommend_frameworks",
                    arguments={"use_case": msg_lower[:120], "required_capability": cap},
                    reasoning="heuristic: recommendation query",
                ))

        # Default fallback
        if not tool_calls:
            if "recommend_frameworks" in available_tools:
                cap = next((c for k, c in _HEURISTIC_USE_CASE_MAP.items() if k in msg_lower), None)
                tool_calls.append(ToolCall(
                    tool_name="recommend_frameworks",
                    arguments={"use_case": user_message[:120], "required_capability": cap},
                    reasoning="heuristic: default fallback to recommendation",
                ))

        return SelectionResult(
            tool_calls=tool_calls,
            reasoning=f"heuristic-fallback: {len(tool_calls)} tool(s): {[tc.tool_name for tc in tool_calls]}",
        )

    # ──────────────────────────────────────────────────────────────────
    # Query evaluation (preserved for backward compatibility)
    # ──────────────────────────────────────────────────────────────────

    def evaluate_query(self, user_message: str) -> QueryEvaluation:
        """Extract structured intent and entities from the user query."""
        evaluation = QueryEvaluation()

        evaluation.use_case = self._get_use_case(user_message)
        if evaluation.use_case:
            evaluation.domain = evaluation.use_case

        evaluation.from_framework = self._extract_current_framework(user_message)
        evaluation.to_frameworks = self._extract_target_frameworks(user_message)
        evaluation.cloud_provider = self._detect_cloud_provider(user_message)

        if evaluation.cloud_provider:
            evaluation.cloud_intent = self._detect_cloud_intent(user_message)

        evaluation.intents = self._detect_intents(user_message, evaluation)
        return evaluation

    # ──────────────────────────────────────────────────────────────────
    # Micro-frontend detection
    # ──────────────────────────────────────────────────────────────────

    def _detect_micro_frontend_intent(self, user_message: str) -> bool:
        """Detect micro-frontend intent from explicit phrases or keyword combos."""
        message = user_message.lower().strip()

        for phrases, _intent in _INTENT_MAP:
            for phrase in phrases:
                if phrase in message:
                    return True

        has_microservice = "microservice" in message
        has_ui = (
            "ui" in message
            or "user interface" in message
            or "frontend" in message
        )
        return has_microservice and has_ui

    def _get_use_case(self, user_message: str) -> str | None:
        message = user_message.lower().strip()

        for phrase in sorted(_USE_CASE_MAP, key=len, reverse=True):
            if phrase in message:
                return _USE_CASE_MAP[phrase]

        has_microservice = "microservice" in message
        has_ui = (
            "ui" in message
            or "user interface" in message
            or "frontend" in message
        )
        if has_microservice and has_ui:
            return "micro-frontend UI automation"

        return None

    # ──────────────────────────────────────────────────────────────────
    # Intent detection
    # ──────────────────────────────────────────────────────────────────

    def _detect_intents(self, user_message: str, evaluation: QueryEvaluation) -> list[str]:
        message = user_message.lower()
        intents = []

        migration_words = [
            "migrate", "migration", "move from", "moving from",
            "replace", "switch from", "transition",
        ]
        recommendation_words = [
            "recommend", "recommendation", "best framework",
            "which framework", "what framework", "suggest",
        ]
        comparison_words = [
            "compare", "comparison", "difference between", "vs", "versus",
        ]
        coverage_words = ["coverage", "covered", "test case", "test cases"]
        convert_words = ["convert", "translate", "rewrite", "port", "transform"]

        if any(word in message for word in migration_words):
            intents.append(INTENT_MIGRATION)
        if evaluation.cloud_provider:
            intents.append(INTENT_CLOUD)
        if any(word in message for word in comparison_words):
            intents.append(INTENT_COMPARISON)
        if any(word in message for word in convert_words):
            intents.append(INTENT_CONVERT)
        if any(word in message for word in coverage_words):
            intents.append(INTENT_COVERAGE)
        if any(word in message for word in recommendation_words):
            intents.append(INTENT_RECOMMENDATION)

        return intents

    # ──────────────────────────────────────────────────────────────────
    # Framework extraction
    # ──────────────────────────────────────────────────────────────────

    def _extract_current_framework(self, user_message: str) -> str | None:
        """Extract the source/current framework from the user query."""
        message = user_message.lower()
        known_frameworks = [
            "robot framework", "playwright", "selenium",
            "cypress", "webdriverio", "puppeteer",
        ]
        for framework in known_frameworks:
            if framework in message:
                return framework
        return None

    def _extract_target_frameworks(self, user_message: str) -> list[str]:
        """Extract target frameworks, excluding the current/source one."""
        message = user_message.lower()
        known_frameworks = {
            "playwright": "Playwright",
            "cypress": "Cypress",
            "selenium": "Selenium",
            "robot framework": "Robot Framework",
            "webdriverio": "WebdriverIO",
            "puppeteer": "Puppeteer",
        }

        found = []
        for keyword, framework in known_frameworks.items():
            if keyword in message:
                found.append(framework)

        current = self._extract_current_framework(user_message)
        if current:
            current_normalized = current.lower()
            found = [fw for fw in found if fw.lower() != current_normalized]

        return found

    # ──────────────────────────────────────────────────────────────────
    # Cloud detection
    # ──────────────────────────────────────────────────────────────────

    def _detect_cloud_provider(self, user_message: str) -> str | None:
        message = user_message.lower()
        for keyword, provider in CLOUD_PROVIDERS.items():
            if keyword in message:
                return provider
        return None

    def _detect_cloud_intent(self, user_message: str) -> str | None:
        message = user_message.lower()

        if any(word in message for word in ["deploy", "deployment", "run", "execute"]):
            return "deployment"
        if any(word in message for word in ["pipeline", "ci/cd", "cicd", "continuous integration"]):
            return "ci_cd"
        if any(word in message for word in ["migrate", "migration", "move"]):
            return "migration"
        return "cloud_execution"

    # ──────────────────────────────────────────────────────────────────
    # Build tool calls from evaluation (preserved for backward compat)
    # ──────────────────────────────────────────────────────────────────

    def _build_tool_calls_from_evaluation(
        self,
        evaluation: QueryEvaluation,
        available_tools: list[str],
    ) -> list[ToolCall]:
        tool_calls = []

        if INTENT_RECOMMENDATION in evaluation.intents and "recommend_frameworks" in available_tools:
            tool_calls.append(ToolCall(
                tool_name="recommend_frameworks",
                arguments={"use_case": evaluation.use_case or "general automation"},
                reasoning="Recommendation intent detected.",
            ))

        if (
            INTENT_MIGRATION in evaluation.intents
            and evaluation.from_framework
            and evaluation.to_frameworks
            and "find_migration_paths" in available_tools
        ):
            tool_calls.append(ToolCall(
                tool_name="find_migration_paths",
                arguments={
                    "from_framework": evaluation.from_framework,
                    "to_frameworks": evaluation.to_frameworks,
                },
                reasoning="Explicit migration target detected.",
            ))

        if INTENT_CLOUD in evaluation.intents and "cloud_deployment" in available_tools:
            tool_calls.append(ToolCall(
                tool_name="cloud_deployment",
                arguments={
                    "cloud_provider": evaluation.cloud_provider,
                    "cloud_intent": evaluation.cloud_intent,
                    "framework": evaluation.to_frameworks,
                },
                reasoning="Cloud intent detected.",
            ))

        if (
            INTENT_CONVERT in evaluation.intents
            and evaluation.from_framework
            and evaluation.to_frameworks
            and "convert_test_cases" in available_tools
        ):
            tool_calls.append(ToolCall(
                tool_name="convert_test_cases",
                arguments={
                    "source_code": "# paste or upload test code",
                    "from_framework": evaluation.from_framework,
                    "to_framework": evaluation.to_frameworks[0],
                },
                reasoning="Test case conversion intent detected.",
            ))

        if INTENT_COMPARISON in evaluation.intents and "run_framework_comparison" in available_tools:
            all_frameworks = list(dict.fromkeys(
                ([evaluation.from_framework] if evaluation.from_framework else [])
                + evaluation.to_frameworks
            ))
            if len(all_frameworks) >= 2:
                tool_calls.append(ToolCall(
                    tool_name="run_framework_comparison",
                    arguments={"frameworks": all_frameworks},
                    reasoning="Framework comparison detected.",
                ))

        return tool_calls
