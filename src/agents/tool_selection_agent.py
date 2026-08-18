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
    "- analyze_uploaded_content(search_term, document_type): search uploaded files\n\n"
    "Respond with a JSON array of tool calls, e.g.:\n"
    '[{"tool_name": "run_framework_comparison", "arguments": {"frameworks": ["Playwright", "Cypress"]}}]'
)


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
    def __init__(self, llm_client=None) -> None:
        self._client = llm_client

    def select(self, user_message: str, available_tools: list[str]) -> SelectionResult:
        prompt = (
            f"Available tools: {available_tools}\n\n"
            f"User query: {user_message}\n\n"
            "Select the appropriate tool(s) and return a JSON array."
        )
        try:
            result = self._client.chat(
                messages=[{"role": "user", "content": prompt}],
                system=_SYSTEM_PROMPT,
            )
            raw = result.get("content", "").strip()
            # Extract JSON array from response
            start, end = raw.find("["), raw.rfind("]") + 1
            tool_calls = [
                ToolCall(tool_name=tc["tool_name"], arguments=tc.get("arguments", {}))
                for tc in json.loads(raw[start:end])
                if tc.get("tool_name") in available_tools
            ] if start != -1 else []
        except Exception as exc:
            logger.warning("ToolSelectionAgent: LLM call failed (%s) — using search fallback", exc)
            tool_calls = [ToolCall(
                tool_name="search_knowledge_graph",
                arguments={"query": user_message},
                reasoning="fallback",
            )]

        if not tool_calls:
            tool_calls = [ToolCall(
                tool_name="recommend_frameworks",
                arguments={"use_case": user_message},
                reasoning="fallback: empty LLM response",
            )]

        reasoning = f"selected {len(tool_calls)} tool(s): {[tc.tool_name for tc in tool_calls]}"
        logger.info("ToolSelectionAgent: %s", reasoning)
        return SelectionResult(tool_calls=tool_calls, reasoning=reasoning)
