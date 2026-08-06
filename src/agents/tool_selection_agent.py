"""Tool Selection Agent — given a user query, selects the most appropriate
tool(s) from the available tool definitions.

Returns an ordered list of (tool_name, arguments) pairs to execute.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.llm.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

# Mapping of intent keywords → tool name (order matters — first match wins)
_INTENT_MAP: list[tuple[list[str], str]] = [
    (["compare", "vs", "versus", "difference between"], "run_framework_comparison"),
    (["migrate", "migration", "path from", "move from"], "find_migration_paths"),
    (["coverage", "test case", "analyze test"], "analyze_test_case_coverage"),
    (["uploaded", "file", "case study", "document"], "analyze_uploaded_content"),
    # Recommendation intent — must come before generic "details" catch-all
    (["best", "recommend", "which framework", "what framework", "suggest",
      "for microservice", "for api", "for mobile", "for web", "for performance",
      "for load", "suitable", "ideal", "top framework"], "recommend_frameworks"),
    (["details", "capabilities", "about", "tell me about", "what is"], "get_framework_details"),
    (["search", "find", "look up"], "search_knowledge_graph"),
]

# Use-case keywords → capability filter for recommend_frameworks
_USE_CASE_MAP: dict[str, str] = {
    "api": "api_testing",
    "microservice": "api_testing",
    "rest": "api_testing",
    "graphql": "graphql_support",
    "web": "browser_testing",
    "browser": "browser_testing",
    "mobile": "mobile_testing",
    "performance": "performance_testing",
    "load": "load_testing",
    "ui": "ui_testing",
}

# Framework names for argument extraction
_KNOWN_FRAMEWORKS = [
    "playwright", "cypress", "selenium", "webdriverio", "robot framework",
    "testcafe", "puppeteer", "appium", "karate", "k6", "locust",
    "rest assured", "terraform", "ansible", "chef", "pulumi",
    "aws cloudformation",
]


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict
    reasoning: str = ""


@dataclass
class SelectionResult:
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning: str = ""


class ToolSelectionAgent:
    """Selects which tool(s) to call based on the user query."""

    def __init__(self, llm_client: OllamaClient | None = None) -> None:
        self._client = llm_client

    def select(self, user_message: str, available_tools: list[str]) -> SelectionResult:
        msg_lower = user_message.lower()
        tool_calls: list[ToolCall] = []

        mentioned = [fw for fw in _KNOWN_FRAMEWORKS if fw in msg_lower]

        matched_tool: str | None = None
        for keywords, tool_name in _INTENT_MAP:
            if tool_name not in available_tools:
                continue
            if any(kw in msg_lower for kw in keywords):
                matched_tool = tool_name
                break

        if matched_tool == "run_framework_comparison":
            if len(mentioned) >= 2:
                tool_calls.append(ToolCall(
                    tool_name="run_framework_comparison",
                    arguments={"frameworks": [f.title() for f in mentioned[:4]]},
                    reasoning=f"compare {mentioned}",
                ))
            else:
                # No specific frameworks named — fall back to recommendation
                matched_tool = "recommend_frameworks"

        if matched_tool == "recommend_frameworks":
            capability = next(
                (cap for kw, cap in _USE_CASE_MAP.items() if kw in msg_lower), None
            )
            tool_calls.append(ToolCall(
                tool_name="recommend_frameworks",
                arguments={"use_case": msg_lower[:120], "required_capability": capability},
                reasoning=f"recommendation query, capability={capability}",
            ))

        elif matched_tool == "find_migration_paths" and mentioned:
            tool_calls.append(ToolCall(
                tool_name="find_migration_paths",
                arguments={"from_framework": mentioned[0].title(),
                           "to_frameworks": [f.title() for f in mentioned[1:]] or None},
                reasoning=f"migration from {mentioned[0]}",
            ))

        elif matched_tool == "get_framework_details" and mentioned:
            for fw in mentioned[:2]:
                tool_calls.append(ToolCall(
                    tool_name="get_framework_details",
                    arguments={"framework_name": fw.title()},
                    reasoning=f"details for {fw}",
                ))

        elif matched_tool == "analyze_uploaded_content":
            tool_calls.append(ToolCall(
                tool_name="analyze_uploaded_content",
                arguments={"search_term": user_message[:100], "document_type": "all"},
                reasoning="uploaded content query",
            ))

        elif matched_tool == "analyze_test_case_coverage":
            tool_calls.append(ToolCall(
                tool_name="analyze_test_case_coverage",
                arguments={"test_cases": [], "frameworks": [f.title() for f in mentioned] or None},
                reasoning="coverage query",
            ))

        elif matched_tool == "search_knowledge_graph":
            tool_calls.append(ToolCall(
                tool_name="search_knowledge_graph",
                arguments={"query": user_message, "entity_types": ["Framework", "Capability"]},
                reasoning="explicit search query",
            ))

        else:
            # Unmatched — use recommend_frameworks as the safe default
            capability = next(
                (cap for kw, cap in _USE_CASE_MAP.items() if kw in msg_lower), None
            )
            tool_calls.append(ToolCall(
                tool_name="recommend_frameworks",
                arguments={"use_case": user_message[:120], "required_capability": capability},
                reasoning="default: no strong intent match, using recommendation",
            ))

        reasoning = f"selected {len(tool_calls)} tool(s): {[tc.tool_name for tc in tool_calls]}"
        logger.info("ToolSelectionAgent: %s", reasoning)
        return SelectionResult(tool_calls=tool_calls, reasoning=reasoning)
