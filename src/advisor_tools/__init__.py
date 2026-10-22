"""Advisor tools — framework search, details, comparison, recommendation,
scoring, category listing, and migration-path lookup.

This is the feature-owned home for everything the chat Advisor pipeline
and the equivalent MCP tools need to answer "which framework / why / how
does it compare" questions. It replaces the advisor-facing slice of the
old monolithic ``src.tools.executor.ToolExecutor``.
"""
from src.advisor_tools.executor import AdvisorToolsExecutor

__all__ = ["AdvisorToolsExecutor"]
