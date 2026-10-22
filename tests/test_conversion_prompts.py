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
    assert "ORPHAN KEYWORDS" in rendered  # from syntax_rules


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
