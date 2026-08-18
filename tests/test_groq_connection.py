"""Test script to verify Groq API connectivity and basic functionality."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from src.llm.groq_client import GroqClient


def test_groq_connection():
    load_dotenv(Path(__file__).parent.parent / "config" / ".env")
    api_key = os.getenv("GROQ_API_KEY", "")

    print("=" * 60)
    print("Testing Groq API Connection")
    print("=" * 60)

    if not api_key:
        print("❌ GROQ_API_KEY not set in config/.env")
        return False

    print(f"\n1. API Key: {api_key[:20]}...")
    client = GroqClient(api_key=api_key)
    print(f"   Model: {client.model}")

    print("\n2. Testing basic text generation...")
    try:
        response = client.generate(
            prompt="What is Playwright? Answer in 2 sentences.",
            system="You are a test automation expert.",
        )
        print(f"   Response ({len(response)} chars): {response[:150]}...")
        print("   ✅ Text generation works!")
    except Exception as exc:
        print(f"   ❌ Error: {exc}")
        return False

    print("\n3. Testing chat interface...")
    try:
        result = client.chat(
            messages=[{"role": "user", "content": "Name one advantage of Cypress over Selenium."}],
            system="You are a test automation expert. Be concise.",
        )
        print(f"   Response type: {result['type']}")
        print(f"   Content preview: {result['content'][:100]}...")
        print("   ✅ Chat interface works!")
    except Exception as exc:
        print(f"   ❌ Error: {exc}")
        return False

    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)
    return True


if __name__ == "__main__":
    success = test_groq_connection()
    sys.exit(0 if success else 1)
