"""Verify conversational memory across agents and the orchestrator.

Run with:  python tests/test_conversation_memory.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.agents.decision_agent import DecisionAgent
from src.agents.tool_selection_agent import ToolSelectionAgent
from src.agents.synthesis_agent import SynthesisAgent
from src.agents.evaluation_agent import EvaluationAgent
from src.agents.reflection_agent import ReflectionAgent
from src.agents.memory import ConversationMemory, AgentMemory


# ══════════════════════════════════════════════════════════════════════════
# Mock LLM
# ══════════════════════════════════════════════════════════════════════════

class MockLLM:
    is_available = True

    def chat(self, messages, system="", tools=None, max_tokens=None):
        if "intent classifier" in system.lower():
            return {"content": '{"action": "tool_call", "reasoning": "needs data"}'}
        if "tool selection" in system.lower():
            return {"content": '{"tool_calls": [{"tool_name": "recommend_frameworks", "arguments": {"use_case": "web testing"}, "reasoning": "rec"}], "overall_reasoning": "recommendation"}'}
        if "data quality" in system.lower():
            return {"content": "VERDICT: data sufficient\nGAPS: none\nNEEDS_MORE: no"}
        if "expert automation" in system.lower():
            return {"content": "Playwright is recommended for modern web testing."}
        if "quality reviewer" in system.lower():
            return {"content": "VERDICT: pass\nCRITIQUE: none"}
        return {"content": "ok"}


# ══════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════

def test_per_agent_memory():
    """Each agent stores decisions in its own AgentMemory."""
    llm = MockLLM()

    decision = DecisionAgent(llm_client=llm)
    tool_sel = ToolSelectionAgent(llm_client=llm)
    synthesis = SynthesisAgent(llm_client=llm)
    evaluation = EvaluationAgent(llm_client=llm)
    reflection = ReflectionAgent(llm_client=llm)

    # --- Round 1 ---
    decision.decide("Compare Playwright and Cypress")
    tool_sel.select("Compare Playwright and Cypress", ["recommend_frameworks", "run_framework_comparison"])
    synthesis.synthesise("Compare Playwright and Cypress", [{"tool_name": "run_framework_comparison", "result": "data here"}])
    evaluation.evaluate("Compare Playwright and Cypress", [{"tool_name": "run_framework_comparison", "result": "data here"}])
    reflection.reflect("Compare Playwright and Cypress", "Playwright is better.", reflection_count=0)

    print("After Round 1:")
    print(f"  DecisionAgent memory:      {len(decision.memory)} entries")
    print(f"  ToolSelectionAgent memory:  {len(tool_sel.memory)} entries")
    print(f"  SynthesisAgent memory:      {len(synthesis.memory)} entries")
    print(f"  EvaluationAgent memory:     {len(evaluation.memory)} entries")
    print(f"  ReflectionAgent memory:     {len(reflection.memory)} entries")

    assert len(decision.memory) == 1, f"Expected 1, got {len(decision.memory)}"
    assert len(tool_sel.memory) == 1
    assert len(synthesis.memory) == 1
    assert len(evaluation.memory) == 1
    assert len(reflection.memory) == 1

    # --- Round 2 ---
    decision.decide("Now recommend something for mobile testing")
    tool_sel.select("Recommend for mobile testing", ["recommend_frameworks"])
    synthesis.synthesise("Recommend for mobile", [{"tool_name": "recommend_frameworks", "result": "Appium..."}])
    evaluation.evaluate("Recommend for mobile", [{"tool_name": "recommend_frameworks", "result": "Appium..."}])
    reflection.reflect("Recommend for mobile", "Appium is best for mobile.", reflection_count=0)

    print("\nAfter Round 2:")
    print(f"  DecisionAgent memory:      {len(decision.memory)} entries")
    print(f"  ToolSelectionAgent memory:  {len(tool_sel.memory)} entries")
    print(f"  SynthesisAgent memory:      {len(synthesis.memory)} entries")
    print(f"  EvaluationAgent memory:     {len(evaluation.memory)} entries")
    print(f"  ReflectionAgent memory:     {len(reflection.memory)} entries")

    assert len(decision.memory) == 2
    assert len(tool_sel.memory) == 2
    assert len(synthesis.memory) == 2
    assert len(evaluation.memory) == 2
    assert len(reflection.memory) == 2

    # --- Verify content ---
    print("\n  DecisionAgent recent entries:")
    for entry in decision.memory.get_recent(5):
        print(f"    [{entry.category}] {entry.content}")

    print("\n  EvaluationAgent recent entries:")
    for entry in evaluation.memory.get_recent(5):
        print(f"    [{entry.category}] {entry.content}")

    print("\n✓ Per-agent memory accumulates correctly across rounds")


def test_conversation_memory():
    """ConversationMemory tracks user/assistant turns."""
    mem = ConversationMemory(max_turns=10)

    # Simulate 3 conversation turns
    mem.add_turn("user", "Compare Playwright and Cypress")
    mem.add_turn("assistant", "Playwright offers multi-browser support, Cypress has better DX.")
    mem.add_turn("user", "Which is better for CI/CD?")
    mem.add_turn("assistant", "Playwright integrates better with CI due to headless support.")
    mem.add_turn("user", "Can I migrate from Cypress to Playwright?")
    mem.add_turn("assistant", "Yes, here's a migration plan...")

    print(f"\nConversation memory: {len(mem)} turns")
    assert len(mem) == 6

    # Get last 4 turns (last 2 exchanges)
    recent = mem.get_history(last_n=4)
    assert len(recent) == 4
    assert recent[0]["role"] == "user"
    assert "CI/CD" in recent[0]["content"]

    print("  Last 4 turns:")
    for turn in recent:
        print(f"    [{turn['role']}] {turn['content'][:60]}")

    # Verify bounded size
    for i in range(20):
        mem.add_turn("user", f"message {i}")
    assert len(mem) == 10, f"Expected 10 (bounded), got {len(mem)}"

    print("\n✓ ConversationMemory tracks turns and respects max_turns")


def test_memory_context_string():
    """AgentMemory.get_context_string() produces LLM-injectable context."""
    mem = AgentMemory(agent_name="test_agent", max_entries=10)

    mem.add("decision", "Compare PW vs Cy → tool_call")
    mem.add("decision", "Recommend for mobile → tool_call")
    mem.add("decision", "Hello → direct")

    ctx = mem.get_context_string(last_n=2)
    print(f"\nContext string (last 2):\n{ctx}")

    assert "Previous test_agent context" in ctx
    assert "Recommend for mobile" in ctx
    assert "Hello" in ctx
    # First entry should NOT be included (only last 2)
    assert "Compare PW" not in ctx

    # Empty memory returns empty string
    empty_mem = AgentMemory(agent_name="empty")
    assert empty_mem.get_context_string() == ""

    print("\n✓ get_context_string() produces correct LLM context")


def test_memory_isolation():
    """Each agent instance has its own isolated memory."""
    llm = MockLLM()

    agent_a = DecisionAgent(llm_client=llm)
    agent_b = DecisionAgent(llm_client=llm)

    agent_a.decide("Query for agent A")
    agent_a.decide("Another query for A")

    agent_b.decide("Query for agent B")

    assert len(agent_a.memory) == 2, "Agent A should have 2 entries"
    assert len(agent_b.memory) == 1, "Agent B should have 1 entry"

    print("\n✓ Memory is isolated per agent instance")


def test_orchestrator_conversation_memory():
    """Simulate orchestrator-level conversation memory without full pipeline."""
    mem = ConversationMemory(max_turns=50)

    # Simulate what the orchestrator does across multiple run() calls
    queries = [
        ("What frameworks support GraphQL testing?", "Karate and Playwright both support GraphQL."),
        ("Compare those two", "Here's the comparison: Karate is API-focused, Playwright is broader."),
        ("Which should I pick for a microservice architecture?", "For microservices, Karate is better suited."),
    ]

    for user_msg, assistant_resp in queries:
        mem.add_turn("user", user_msg)
        mem.add_turn("assistant", assistant_resp)

    # After 3 exchanges, memory should have 6 entries
    assert len(mem) == 6

    # The evaluation agent would receive this history
    history = mem.get_history(last_n=10)
    print(f"\nOrchestrator conversation memory ({len(history)} turns):")
    for turn in history:
        print(f"  [{turn['role']:>9}] {turn['content'][:70]}")

    # Verify the history maintains order
    assert history[0]["role"] == "user"
    assert "GraphQL" in history[0]["content"]
    assert history[-1]["role"] == "assistant"
    assert "Karate" in history[-1]["content"]

    print("\n✓ Orchestrator-level conversation memory works correctly")


# ══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("CONVERSATIONAL MEMORY VERIFICATION")
    print("=" * 70)

    tests = [
        test_per_agent_memory,
        test_conversation_memory,
        test_memory_context_string,
        test_memory_isolation,
        test_orchestrator_conversation_memory,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            test()
            passed += 1
        except Exception as e:
            print(f"\n✗ {test.__name__}: {e}")
            failed += 1

    print()
    print("=" * 70)
    print(f"RESULTS: {passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 70)

    if failed:
        sys.exit(1)
    else:
        print("\n🎉 All conversational memory tests passed!")
