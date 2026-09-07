"""Framework example loader.

Loads canonical, correctly-formatted example files from
``data/samples/framework_examples/`` and exposes them as a reference block
that can be injected into code-generation prompts.

Why this exists
---------------
The code generator previously described each framework's conventions only in
prose (``FRAMEWORK_CONTEXT`` / ``_FRAMEWORK_CONTEXT``). LLMs follow a concrete,
correctly-formatted example far more reliably than prose rules, so generated
output — especially for structural frameworks like Robot Framework — tended to
drift from the expected format. Feeding the real example file into the prompt
grounds the model in the exact structure, sections, indentation and idioms it
should reproduce.

The framework keys are the ``TargetFramework`` enum *values* used throughout
codegen (e.g. ``robot_framework``, ``playwright_py``). Not every framework has
an example on disk; missing files are handled gracefully by returning an empty
string, so callers can always append the result unconditionally.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


# Directory holding the canonical example files.
_EXAMPLES_DIR = Path("data/samples/framework_examples")

# Map each codegen framework key -> its example filename.
# Only frameworks with a genuinely matching example are mapped; keys omitted
# here (or whose file is missing) simply get no reference block.
_FRAMEWORK_EXAMPLE_FILES: dict[str, str] = {
    "playwright_py": "playwright_example.py",
    "selenium_py": "selenium_example.py",
    "cypress_js": "cypress_example.js",
    "rest_assured": "rest_assured_example.java",
    "robot_framework": "robot_framework_example.robot",
    # No dedicated example on disk for these; intentionally unmapped:
    #   playwright_ts  (only a Python Playwright example exists)
    #   selenium_java  (only a Python Selenium example exists)
}

# Safety cap so a large example never blows the prompt token budget.
_MAX_EXAMPLE_CHARS = 8000


@lru_cache(maxsize=32)
def load_example(framework: str) -> str:
    """Return the raw text of the example file for ``framework``.

    Returns an empty string when the framework has no mapped example, the file
    is missing, or it cannot be read. Result is cached per framework key.
    """
    filename = _FRAMEWORK_EXAMPLE_FILES.get(framework)
    if not filename:
        return ""

    path = _EXAMPLES_DIR / filename
    try:
        text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("example_loader: no example file at %s", path)
        return ""
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("example_loader: failed to read %s (%s)", path, exc)
        return ""

    if len(text) > _MAX_EXAMPLE_CHARS:
        text = text[:_MAX_EXAMPLE_CHARS].rstrip() + "\n# … (example truncated)"
    return text


def build_example_reference(framework: str) -> str:
    """Return a labelled reference block for injection into a system prompt.

    The block instructs the model to match the example's structure, sections,
    indentation and idioms. Returns an empty string when no example is
    available, so callers can append it unconditionally without a guard.
    """
    example = load_example(framework)
    if not example:
        return ""

    return (
        "Below is a canonical, correctly-formatted example for this framework. "
        "Match its structure, section layout, indentation, naming and idioms "
        "exactly. Do NOT copy its literal test data or selectors — only mirror "
        "its format and conventions.\n\n"
        "===== CANONICAL FORMAT EXAMPLE =====\n"
        f"{example}\n"
        "===== END EXAMPLE ====="
    )
