"""language.py — Target-language/ecosystem resolution for conversion.

Small, self-contained replacement for the language-resolution logic that
used to live inside ToolExecutor (`_resolve_target_language`,
`_LANG_ECOSYSTEM`). Kept separate from the knowledge-base/scoring/
migration-planning code so the conversion feature doesn't depend on
ToolExecutor at all.
"""
from __future__ import annotations

from typing import Any

__all__ = ["LANG_ECOSYSTEM", "resolve_target_language"]

# language -> ecosystem details (extension, test runner, package manifest,
# markdown fence language for stripping LLM-added fences)
LANG_ECOSYSTEM: dict[str, dict[str, str]] = {
    "python":     {"ext": ".py",    "runner": "pytest",                                           "manifest": "requirements.txt", "fence": "python"},
    "robot":      {"ext": ".robot", "runner": "robot (e.g. robot tests/)",                         "manifest": "requirements.txt", "fence": "robot"},
    "javascript": {"ext": ".js",    "runner": "the framework CLI (e.g. k6 run, npx cypress run)",  "manifest": "package.json",     "fence": "javascript"},
    "typescript": {"ext": ".ts",    "runner": "the framework CLI",                                 "manifest": "package.json",     "fence": "typescript"},
    "java":       {"ext": ".java",  "runner": "Maven/Gradle (JUnit/TestNG)",                        "manifest": "pom.xml",          "fence": "java"},
    "c#":         {"ext": ".cs",    "runner": "dotnet test",                                        "manifest": "packages.config",  "fence": "csharp"},
    "ruby":       {"ext": ".rb",    "runner": "rspec",                                              "manifest": "Gemfile",          "fence": "ruby"},
    "go":         {"ext": ".go",    "runner": "go test",                                            "manifest": "go.mod",           "fence": "go"},
}


def resolve_target_language(to_fw: Any, to_name: str) -> tuple[str, dict[str, str]]:
    """Determine the target language + ecosystem.

    `to_fw` is an optional FrameworkData-like object with a
    `languages_supported` list (from the knowledge base). Falls back to
    Python if nothing matches. Robot Framework is always its own
    language/format, never resolved to "python".
    """
    if "robot" in to_name.lower():
        return "robot", LANG_ECOSYSTEM["robot"]

    default = LANG_ECOSYSTEM["python"]
    langs = [str(l).lower() for l in getattr(to_fw, "languages_supported", None) or []]
    for lang in langs:
        for key in LANG_ECOSYSTEM:
            if key in lang:  # handles "TypeScript (via bundler)" etc.
                return key, LANG_ECOSYSTEM[key]
    return "python", default
