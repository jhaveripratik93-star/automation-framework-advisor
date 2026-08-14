"""Test script for the Test Case Coverage Analysis Tool."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.knowledge_base import KnowledgeBase
from src.graph import load_or_seed_graph
from src.graph.graphrag_engine import GraphRAGEngine
from src.tools.executor import ToolExecutor


def main():
    print("=" * 70)
    print("TEST CASE COVERAGE ANALYSIS TOOL - DEMO")
    print("=" * 70)

    kb = KnowledgeBase(data_dir="data/frameworks")
    kb.load()
    print(f"Loaded {len(kb.list_all())} frameworks")

    graph = load_or_seed_graph(kb)
    graphrag = GraphRAGEngine(graph=graph, knowledge_base=kb)
    tool_executor = ToolExecutor(knowledge_graph=graph, graphrag_engine=graphrag, knowledge_base=kb)

    test_cases = [
        {"id": "TC001", "description": "Verify user login",            "required_capability": "UI Automation"},
        {"id": "TC002", "description": "Verify dashboard after login", "required_capability": "UI Validation"},
        {"id": "TC003", "description": "Verify REST API response time", "required_capability": "API Performance"},
        {"id": "TC004", "description": "Verify file download",          "required_capability": "File Handling"},
        {"id": "TC005", "description": "Verify proprietary message",    "required_capability": "Custom Library"},
    ]
    frameworks = ["Robot Framework", "Selenium", "K6"]

    print(f"\nTest Cases: {len(test_cases)}, Frameworks: {', '.join(frameworks)}\n")

    result = tool_executor.execute(
        "analyze_test_case_coverage",
        {"test_cases": test_cases, "frameworks": frameworks},
    )
    print(result)
    print("\n✅ TEST COMPLETED SUCCESSFULLY")


if __name__ == "__main__":
    main()
