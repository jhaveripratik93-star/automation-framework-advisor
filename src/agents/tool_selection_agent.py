"""Tool Selection Agent — uses the LLM to select tools and build arguments."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


_SYSTEM_PROMPT = (
    "You are a tool selection agent for an automation framework advisor. "
    "Given a user query and a list of available tools, select the most appropriate "
    "tool(s) and construct their arguments.\n\n"
    "Available tools:\n"
    "- search_knowledge_graph(query, entity_types): search for frameworks by keyword\n"
    "- get_framework_details(framework_name): full details for a single framework\n"
    "- run_framework_comparison(frameworks, focus_criteria): side-by-side comparison\n"
    "- recommend_frameworks(use_case, required_capability): ranked recommendations\n"
    "- find_migration_paths(from_framework, to_frameworks): migration routes and effort\n"
    "- analyze_test_case_coverage(test_cases, frameworks): coverage matrix\n"
    "- analyze_uploaded_content(search_term, document_type): search uploaded files\n"
    "- convert_test_cases(source_code, from_framework, to_framework): convert test code between frameworks\n\n"
    "Respond with a JSON array of tool calls, e.g.:\n"
    '[{"tool_name": "run_framework_comparison", '
    '"arguments": {"frameworks": ["Playwright", "Cypress"]}}]'
)


# ---------------------------------------------------------------------------
# Intent mappings
# ---------------------------------------------------------------------------

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

CLOUD_PROVIDERS = {
    "aws": "AWS",
    "amazon web services": "AWS",
    "azure": "Azure",
    "microsoft azure": "Azure",
    "gcp": "GCP",
    "google cloud": "GCP",
}

INTENT_RECOMMENDATION = "recommendation"
INTENT_MIGRATION = "migration"
INTENT_CLOUD = "cloud"
INTENT_COMPARISON = "comparison"
INTENT_COVERAGE = "coverage"
INTENT_DETAILS = "details"
INTENT_CONVERT = "convert"



"""Tool Selection Agent — uses the LLM to select tools and build arguments."""


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

class ToolSelectionAgent:
    def __init__(self, llm_client=None) -> None:
        self._client = llm_client

    # -----------------------------------------------------------------------
    # Micro-frontend detection
    # -----------------------------------------------------------------------

    def _detect_micro_frontend_intent(
        self,
        user_message: str,
    ) -> bool:
        """
        Detect micro-frontend intent.

        Explicit phrases:
            - micro frontend
            - microfrontend
            - microservice ui
            - microservice frontend

        Also detects 'microservice' + 'ui' appearing together.
        """
        message = user_message.lower().strip()

        # Explicit intent phrases
        for phrases, _intent in _INTENT_MAP:
            for phrase in phrases:
                if phrase in message:
                    return True

        # Detect microservice + UI independently
        has_microservice = "microservice" in message
        has_ui = (
            "ui" in message
            or "user interface" in message
            or "frontend" in message
        )

        return has_microservice and has_ui

    def _get_use_case(self, user_message: str) -> str | None:
        message = user_message.lower().strip()

        # Check longer/more specific phrases first
        for phrase in sorted(_USE_CASE_MAP, key=len, reverse=True):
            if phrase in message:
                return _USE_CASE_MAP[phrase]

        # Explicit microservice + UI detection
        has_microservice = "microservice" in message
        has_ui = (
            "ui" in message
            or "user interface" in message
            or "frontend" in message
        )

        if has_microservice and has_ui:
            return "micro-frontend UI automation"

        return None

    def _detect_intents(
        self,
        user_message: str,
        evaluation: QueryEvaluation,
    ) -> list[str]:

        message = user_message.lower()
        intents = []

        migration_words = [
            "migrate",
            "migration",
            "move from",
            "moving from",
            "replace",
            "switch from",
            "transition",
        ]

        recommendation_words = [
            "recommend",
            "recommendation",
            "best framework",
            "which framework",
            "what framework",
            "suggest",
        ]

        comparison_words = [
            "compare",
            "comparison",
            "difference between",
            "vs",
            "versus",
        ]

        coverage_words = [
            "coverage",
            "covered",
            "test case",
            "test cases",
        ]

        # Migration takes priority
        if any(word in message for word in migration_words):
            intents.append(INTENT_MIGRATION)

        if evaluation.cloud_provider:
            intents.append(INTENT_CLOUD)

        if any(word in message for word in comparison_words):
            intents.append(INTENT_COMPARISON)

        convert_words = ["convert", "translate", "rewrite", "port", "transform"]
        if any(word in message for word in convert_words):
            intents.append(INTENT_CONVERT)

        if any(word in message for word in coverage_words):
            intents.append(INTENT_COVERAGE)

        if any(word in message for word in recommendation_words):
            intents.append(INTENT_RECOMMENDATION)

        return intents

    def _extract_target_frameworks(
        self,
        user_message: str,
    ) -> list[str]:

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

        # Don't treat current framework as target
        if current:
            current_normalized = current.lower()

            found = [
                framework
                for framework in found
                if framework.lower() != current_normalized
            ]

        return found

    def _detect_cloud_provider(
        self,
        user_message: str,
    ) -> str | None:

        message = user_message.lower()

        providers = {
            "aws": "AWS",
            "amazon web services": "AWS",
            "azure": "Azure",
            "microsoft azure": "Azure",
            "gcp": "GCP",
            "google cloud": "GCP",
        }

        for keyword, provider in providers.items():
            if keyword in message:
                return provider

        return None

    def _detect_cloud_intent(
        self,
        user_message: str,
    ) -> str | None:

        message = user_message.lower()

        if any(
            word in message
            for word in ["deploy", "deployment", "run", "execute"]
        ):
            return "deployment"

        if any(
            word in message
            for word in ["pipeline", "ci/cd", "cicd", "continuous integration"]
        ):
            return "ci_cd"

        if any(
            word in message
            for word in ["migrate", "migration", "move"]
        ):
            return "migration"

        return "cloud_execution"

    def _build_tool_calls_from_evaluation(
        self,
        evaluation: QueryEvaluation,
        available_tools: list[str],
    ) -> list[ToolCall]:

        tool_calls = []

        # Recommendation
        if (
            INTENT_RECOMMENDATION in evaluation.intents
            and "recommend_frameworks" in available_tools
        ):
            tool_calls.append(
                ToolCall(
                    tool_name="recommend_frameworks",
                    arguments={
                        "use_case": (
                            evaluation.use_case
                            or "general automation"
                        )
                    },
                    reasoning="Recommendation intent detected.",
                )
            )

        # Migration
        if (
            INTENT_MIGRATION in evaluation.intents
            and evaluation.from_framework
            and evaluation.to_frameworks
            and "find_migration_paths" in available_tools
        ):
            tool_calls.append(
                ToolCall(
                    tool_name="find_migration_paths",
                    arguments={
                        "from_framework": evaluation.from_framework,
                        "to_frameworks": evaluation.to_frameworks,
                    },
                    reasoning="Explicit migration target detected.",
                )
            )

        # Cloud
        if (
            INTENT_CLOUD in evaluation.intents
            and "cloud_deployment" in available_tools
        ):
            tool_calls.append(
                ToolCall(
                    tool_name="cloud_deployment",
                    arguments={
                        "cloud_provider": evaluation.cloud_provider,
                        "cloud_intent": evaluation.cloud_intent,
                        "framework": evaluation.to_frameworks,
                    },
                    reasoning="Cloud intent detected.",
                )
            )

        # Convert
        if (
            INTENT_CONVERT in evaluation.intents
            and evaluation.from_framework
            and evaluation.to_frameworks
            and "convert_test_cases" in available_tools
        ):
            tool_calls.append(
                ToolCall(
                    tool_name="convert_test_cases",
                    arguments={
                        "source_code": "# paste or upload test code",
                        "from_framework": evaluation.from_framework,
                        "to_framework": evaluation.to_frameworks[0],
                    },
                    reasoning="Test case conversion intent detected.",
                )
            )

        # Comparison — include from_framework so both sides are present
        if (
            INTENT_COMPARISON in evaluation.intents
            and "run_framework_comparison" in available_tools
        ):
            all_frameworks = list(dict.fromkeys(
                ([evaluation.from_framework] if evaluation.from_framework else [])
                + evaluation.to_frameworks
            ))
            if len(all_frameworks) >= 2:
                tool_calls.append(
                    ToolCall(
                        tool_name="run_framework_comparison",
                        arguments={"frameworks": all_frameworks},
                        reasoning="Framework comparison detected.",
                    )
                )

        return tool_calls

    def evaluate_query(
        self,
        user_message: str,
    ) -> QueryEvaluation:
        """
        Extract structured intent and entities from the user query.
        """

        message = user_message.lower()

        evaluation = QueryEvaluation()

        # ---------------------------------------------------------
        # Domain / use case
        # ---------------------------------------------------------

        evaluation.use_case = self._get_use_case(user_message)

        if evaluation.use_case:
            evaluation.domain = evaluation.use_case

        # ---------------------------------------------------------
        # Current framework
        # ---------------------------------------------------------

        evaluation.from_framework = self._extract_current_framework(
            user_message
        )

        # ---------------------------------------------------------
        # Target framework
        # ---------------------------------------------------------

        evaluation.to_frameworks = self._extract_target_frameworks(
            user_message
        )

        # ---------------------------------------------------------
        # Cloud
        # ---------------------------------------------------------

        evaluation.cloud_provider = self._detect_cloud_provider(
            user_message
        )

        if evaluation.cloud_provider:
            evaluation.cloud_intent = self._detect_cloud_intent(
                user_message
            )

        # ---------------------------------------------------------
        # Intent
        # ---------------------------------------------------------

        evaluation.intents = self._detect_intents(
            user_message,
            evaluation,
        )

        return evaluation

    # -----------------------------------------------------------------------
    # Extract current framework
    # -----------------------------------------------------------------------

    def _extract_current_framework(
        self,
        user_message: str,
    ) -> str | None:
        """
        Extract a known current framework from the user query.

        This can later be replaced by an LLM/entity extractor or knowledge
        graph lookup.
        """
        message = user_message.lower()

        known_frameworks = [
            "robot framework",
            "playwright",
            "selenium",
            "cypress",
            "webdriverio",
            "puppeteer",
        ]

        for framework in known_frameworks:
            if framework in message:
                return framework

        return None

    # -----------------------------------------------------------------------
    # Main selection method
    # -----------------------------------------------------------------------

    def select(
        self,
        user_message: str,
        available_tools: list[str],
    ) -> SelectionResult:

        # 1. Evaluate the query
        evaluation = self.evaluate_query(user_message)

        logger.info(
            "Query evaluation: %s",
            evaluation,
        )

        # 2. Convert evaluation into tool calls
        tool_calls = self._build_tool_calls_from_evaluation(
            evaluation,
            available_tools,
        )

        # 3. If deterministic routing worked, return it
        if tool_calls:
            reasoning = (
                f"intents={evaluation.intents}, "
                f"use_case={evaluation.use_case}, "
                f"from={evaluation.from_framework}, "
                f"to={evaluation.to_frameworks}, "
                f"cloud={evaluation.cloud_provider}"
            )

            return SelectionResult(
                tool_calls=tool_calls,
                reasoning=reasoning,
            )

        # 4. Otherwise let the LLM decide
        return self._llm_select(
            user_message=user_message,
            available_tools=available_tools,
            evaluation=evaluation,
        )

    def _fallback_selection(
        self,
        user_message: str,
        available_tools: list[str],
        reason: str,
    ) -> SelectionResult:

        if "search_knowledge_graph" in available_tools:
            tool_calls = [
                ToolCall(
                    tool_name="search_knowledge_graph",
                    arguments={
                        "query": user_message,
                    },
                    reasoning=reason,
                )
            ]

        elif "recommend_frameworks" in available_tools:
            tool_calls = [
                ToolCall(
                    tool_name="recommend_frameworks",
                    arguments={
                        "use_case": user_message,
                    },
                    reasoning=reason,
                )
            ]

        else:
            tool_calls = []

        return SelectionResult(
            tool_calls=tool_calls,
            reasoning=f"fallback: {reason}",
        )


    def _llm_select(
        self,
        user_message: str,
        available_tools: list[str],
        evaluation: QueryEvaluation,
    ) -> SelectionResult:
        """
        Use the LLM as a fallback when deterministic evaluation
        cannot produce a tool call.
        """

        evaluation_context = {
            "domain": evaluation.domain,
            "intent": evaluation.intents,
            "use_case": evaluation.use_case,
            "from_framework": evaluation.from_framework,
            "to_frameworks": evaluation.to_frameworks,
            "cloud_provider": evaluation.cloud_provider,
            "cloud_intent": evaluation.cloud_intent,
        }

        prompt = (
            f"Available tools: {available_tools}\n\n"
            f"User query: {user_message}\n\n"
            f"Initial query evaluation:\n"
            f"{json.dumps(evaluation_context, indent=2)}\n\n"
            "Select the appropriate tool(s) and construct their arguments.\n"
            "Return ONLY a JSON array."
        )

        try:
            result = self._client.chat(
                messages=[
                    {
                        "role": "user",
                        "content": prompt,
                    }
                ],
                system=_SYSTEM_PROMPT,
            )

            raw = result.get("content", "").strip()

            logger.debug(
                "ToolSelectionAgent LLM response: %s",
                raw,
            )

            # ---------------------------------------------------------
            # Extract JSON array
            # ---------------------------------------------------------

            start = raw.find("[")
            end = raw.rfind("]") + 1

            if start == -1 or end <= start:
                logger.warning(
                    "ToolSelectionAgent: LLM returned no JSON array"
                )
                return self._fallback_selection(
                    user_message,
                    available_tools,
                    "LLM returned no JSON array",
                )

            parsed = json.loads(raw[start:end])

            if not isinstance(parsed, list):
                raise ValueError(
                    "LLM tool selection response must be a JSON array"
                )

            # ---------------------------------------------------------
            # Validate tool calls
            # ---------------------------------------------------------

            tool_calls: list[ToolCall] = []

            for tc in parsed:

                if not isinstance(tc, dict):
                    continue

                tool_name = tc.get("tool_name")

                if not tool_name:
                    continue

                if tool_name not in available_tools:
                    logger.warning(
                        "ToolSelectionAgent: LLM selected unavailable "
                        "tool '%s'",
                        tool_name,
                    )
                    continue

                arguments = tc.get("arguments", {})

                if not isinstance(arguments, dict):
                    arguments = {}

                tool_calls.append(
                    ToolCall(
                        tool_name=tool_name,
                        arguments=arguments,
                        reasoning=tc.get(
                            "reasoning",
                            "LLM tool selection",
                        ),
                    )
                )

            # ---------------------------------------------------------
            # Empty selection
            # ---------------------------------------------------------

            if not tool_calls:
                return self._fallback_selection(
                    user_message,
                    available_tools,
                    "LLM returned no valid tool calls",
                )

            reasoning = (
                f"LLM selected {len(tool_calls)} tool(s): "
                f"{[tc.tool_name for tc in tool_calls]}"
            )

            logger.info(
                "ToolSelectionAgent: %s",
                reasoning,
            )

            return SelectionResult(
                tool_calls=tool_calls,
                reasoning=reasoning,
            )

        except Exception as exc:
            logger.warning(
                "ToolSelectionAgent: LLM selection failed (%s)",
                exc,
            )

            return self._fallback_selection(
                user_message,
                available_tools,
                f"LLM selection failed: {exc}",
            )