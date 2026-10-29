"""Tests for src/conversion/prompts.py — editable prompt-file loading."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.conversion.prompts import load_prompt_profile, set_prompts_root, slugify


def test_slugify():
    assert slugify("Robot Framework") == "robot_framework"
    assert slugify("K6") == "k6"
    assert slugify("REST Assured") == "rest_assured"


def test_loads_real_shipped_prompts():
    """Exercises the actual files shipped under config/conversion_prompts —
    catches typos/missing files without mocking anything."""
    set_prompts_root(None)  # ensure default root
    profile = load_prompt_profile("K6", "Robot Framework")
    assert profile.base_system_prompt
    assert "CI/CD" in profile.base_system_prompt or "ci/cd" in profile.base_system_prompt.lower()
    assert profile.syntax_rules  # robot_framework.md should exist
    assert profile.pair_rules    # k6__to__robot_framework.md should exist
    assert len(profile.sources) == 3


def test_render_system_prompt_includes_all_sections():
    set_prompts_root(None)
    profile = load_prompt_profile("K6", "Robot Framework")
    rendered = profile.render_system_prompt("K6", "Robot Framework")
    assert "K6" in rendered
    assert "Robot Framework" in rendered
    # syntax + pair rules both present
    assert "SHAPE A" in rendered  # from pair_rules
    assert "orphan keywords" in rendered.lower()  # from syntax_rules (now a readable `## ` section heading)


def test_robot_to_playwright_profile_has_browser_free_api_rule():
    """The Robot->Playwright pair file and the Playwright syntax file carry the
    browser-free API-test rule, and the pair file is one of the loaded sources."""
    set_prompts_root(None)
    profile = load_prompt_profile("Robot Framework", "Playwright")
    assert profile.pair_rules
    assert any("robot_framework__to__playwright.md" in s for s in profile.sources)
    assert len(profile.sources) == 3  # base + playwright syntax + pair rules

    rendered = profile.render_system_prompt("Robot Framework", "Playwright").lower()
    # the conditional-browser rule must be present
    assert "page: page" in rendered
    assert "api" in rendered
    # browser-free guidance mentions not injecting a page for non-UI tests
    assert "must not" in rendered and "browser" in rendered


def test_robot_to_playwright_profile_has_native_api_strategy():
    """The Robot->Playwright rules now carry the ORDERED strategy (native-API
    translation preferred, bundling fallback), the APIRequestContext +
    skip-on-unreachable guidance, and the worked Nifi.get_flow_status example."""
    set_prompts_root(None)
    profile = load_prompt_profile("Robot Framework", "Playwright")
    rendered = profile.render_system_prompt("Robot Framework", "Playwright")
    low = rendered.lower()

    # Ordered strategy: step 1 native-first, step 2 bundle fallback.
    assert "step 1" in low and "step 2" in low
    assert "native" in low
    assert "fallback" in low or "fall back" in low
    assert "prefer" in low  # native translation preferred

    # APIRequestContext + skip-on-unreachable semantics.
    assert "apirequestcontext" in low
    assert "new_context" in low
    assert "skip" in low and "unreachable" in low
    assert "backendunavailable" in low
    assert "resolve_setting" in low

    # Self-contained requirement (no legacy client import / no bundling).
    assert "self-contained" in low
    assert "must not import" in low

    # Worked example mirrors test_nifi_flow_status.py.
    assert "nifi.get_flow_status" in low
    assert "/nifi-api/flow/status" in low
    assert "controllerstatus" in low
    assert "nifi_base_url" in low

    # Bundling fallback path is still described (kept working).
    assert "bundl" in low


def test_missing_pair_rules_degrades_gracefully():
    set_prompts_root(None)
    profile = load_prompt_profile("Selenium", "K6")  # no pair file for this combo
    assert profile.pair_rules == ""
    rendered = profile.render_system_prompt("Selenium", "K6")
    assert "Selenium" in rendered  # still renders fine without pair rules


def test_custom_prompts_root_used_when_set():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "syntax_rules").mkdir()
        (root / "pair_rules").mkdir()
        (root / "base_system_prompt.md").write_text(
            "Custom base prompt converting {from_name} to {to_name}.", encoding="utf-8"
        )
        try:
            set_prompts_root(root)
            profile = load_prompt_profile("A", "B")
            assert "Custom base prompt" in profile.base_system_prompt
            rendered = profile.render_system_prompt("A", "B")
            assert "converting A to B" in rendered
        finally:
            set_prompts_root(None)


def test_missing_base_prompt_falls_back_without_crashing():
    with tempfile.TemporaryDirectory() as tmp:
        try:
            set_prompts_root(Path(tmp))
            profile = load_prompt_profile("X", "Y")
            assert profile.base_system_prompt  # fallback text, not empty
            rendered = profile.render_system_prompt("X", "Y")
            assert "X" in rendered and "Y" in rendered
        finally:
            set_prompts_root(None)
