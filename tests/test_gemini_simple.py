"""Simple Gemini client test — sends a single prompt and shows the response
with input/output token usage.

Prerequisites:
  Set GEMINI_API_KEY in config/.env or as an environment variable.
  Get a key from https://aistudio.google.com/apikey

Run:
  python tests/test_gemini_simple.py
"""
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load API key from config/.env
_ENV_PATH = Path(__file__).resolve().parents[1] / "config" / ".env"
load_dotenv(_ENV_PATH, override=True)

API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = "gemini-3.6-flash"
API_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"


def main():
    if not API_KEY:
        print("ERROR: GEMINI_API_KEY is not set.")
        print(f"Add it to {_ENV_PATH}:")
        print("  GEMINI_API_KEY=your_key_here")
        print("Get a key from: https://aistudio.google.com/apikey")
        sys.exit(1)

    prompt = (
        "What are the capabilities and limitations of "
        "Robot Framework for API testing?"
    )

    print(f"Model:  {MODEL}")
    print(f"Prompt: {prompt}")
    print("-" * 60)

    response = httpx.post(
        API_URL,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.7,
            "max_tokens": 2048,
        },
        timeout=90.0,
    )

    if response.status_code != 200:
        print(f"ERROR: HTTP {response.status_code}")
        print(response.text[:500])
        sys.exit(1)

    data = response.json()

    # Extract response text
    content = data["choices"][0]["message"]["content"]

    # Extract token usage
    usage = data.get("usage", {})
    input_tokens = usage.get("prompt_tokens", "N/A")
    output_tokens = usage.get("completion_tokens", "N/A")
    total_tokens = usage.get("total_tokens", "N/A")

    # Display
    print()
    print("RESPONSE:")
    print("=" * 60)
    print(content)
    print("=" * 60)
    print()
    print(f"Input tokens:  {input_tokens}")
    print(f"Output tokens: {output_tokens}")
    print(f"Total tokens:  {total_tokens}")


if __name__ == "__main__":
    main()
