"""Self-learning template store — persists and manages learned patterns.

When the LLM generates code for steps that templates couldn't handle,
this store captures the mapping as a new reusable pattern. Over time,
the template engine handles more steps directly without LLM calls.
"""
from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from src.codegen.models import ActionType, TargetFramework, TemplatePattern

logger = logging.getLogger(__name__)

_DEFAULT_STORE_PATH = Path("data/codegen/learned_templates.json")
_DEFAULT_STATS_PATH = Path("data/codegen/template_stats.json")

# Minimum pattern quality thresholds
_MIN_SUCCESS_RATE = 0.6
_MIN_USES_BEFORE_RETIRE = 5
_MAX_SIMILARITY_FOR_NEW = 0.85


class TemplateStore:
    """Manages learned template patterns with persistence and quality tracking.

    Features:
    - Learns new patterns from successful LLM generations
    - Tracks usage and success rates per pattern
    - Auto-disables patterns that fall below quality thresholds
    - Deduplicates patterns using similarity checking
    - Persists to JSON files for cross-session reuse
    """

    def __init__(
        self,
        store_path: Path | str | None = None,
        stats_path: Path | str | None = None,
        auto_learn: bool = True,
    ) -> None:
        self._store_path = Path(store_path) if store_path else _DEFAULT_STORE_PATH
        self._stats_path = Path(stats_path) if stats_path else _DEFAULT_STATS_PATH
        self._auto_learn = auto_learn
        self._patterns: list[TemplatePattern] = []
        self._stats: dict[str, dict[str, Any]] = {}
        self._load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def pattern_count(self) -> int:
        """Number of learned patterns currently stored."""
        return len(self._patterns)

    @property
    def active_pattern_count(self) -> int:
        """Number of enabled patterns."""
        return sum(1 for p in self._patterns if p.enabled)

    def get_patterns(self) -> list[TemplatePattern]:
        """Return all learned patterns (both enabled and disabled)."""
        return list(self._patterns)

    def get_active_patterns(self) -> list[TemplatePattern]:
        """Return only enabled patterns for use by the template engine."""
        return [p for p in self._patterns if p.enabled]

    def learn(
        self,
        step_action: str,
        generated_code: str,
        action_type: ActionType,
        framework: TargetFramework,
        verified: bool = True,
    ) -> TemplatePattern | None:
        """Learn a new pattern from a successful LLM generation.

        Args:
            step_action: Original natural language step text.
            generated_code: The LLM-generated code that passed validation.
            action_type: Classified action type.
            framework: Framework the code was generated for.
            verified: Whether the code passed verification.

        Returns:
            The new TemplatePattern if learned, None if rejected.
        """
        if not self._auto_learn:
            logger.debug("TemplateStore: learning disabled, skipping")
            return None

        if not verified:
            logger.debug("TemplateStore: skipping unverified pattern")
            return None

        # Generalize the step into a regex pattern
        generalized_pattern = self._generalize_step(step_action)
        if not generalized_pattern:
            logger.debug("TemplateStore: could not generalize step")
            return None

        # Check for duplicates
        if self._is_duplicate(generalized_pattern):
            logger.debug("TemplateStore: pattern too similar to existing, skipping")
            return None

        # Create code template from the generated code
        code_template = self._templatize_code(step_action, generated_code)

        # Create new pattern
        pattern = TemplatePattern(
            id=f"learned_{uuid.uuid4().hex[:8]}",
            pattern=generalized_pattern,
            action_type=action_type,
            code_templates={framework.value: code_template},
            source="learned",
            usage_count=0,
            success_count=0,
            created_at=datetime.now().isoformat(),
            enabled=True,
        )

        self._patterns.append(pattern)
        self._save()
        logger.info(
            "TemplateStore: learned new pattern '%s' for action_type=%s",
            pattern.id, action_type.value,
        )
        return pattern

    def record_usage(self, pattern_id: str, success: bool) -> None:
        """Record a pattern usage outcome for quality tracking.

        Args:
            pattern_id: The pattern that was used.
            success: Whether the generated code passed validation.
        """
        # Update pattern counts
        for pattern in self._patterns:
            if pattern.id == pattern_id:
                pattern.usage_count += 1
                if success:
                    pattern.success_count += 1
                pattern.last_used_at = datetime.now().isoformat()

                # Check retirement threshold
                if (pattern.usage_count >= _MIN_USES_BEFORE_RETIRE
                        and self._success_rate(pattern) < _MIN_SUCCESS_RATE):
                    pattern.enabled = False
                    logger.warning(
                        "TemplateStore: retired pattern '%s' (success_rate=%.2f)",
                        pattern_id, self._success_rate(pattern),
                    )
                break

        # Update stats
        if pattern_id not in self._stats:
            self._stats[pattern_id] = {"uses": 0, "successes": 0, "history": []}
        self._stats[pattern_id]["uses"] += 1
        if success:
            self._stats[pattern_id]["successes"] += 1
        self._stats[pattern_id]["history"].append({
            "timestamp": datetime.now().isoformat(),
            "success": success,
        })

        self._save()

    def get_stats_summary(self) -> dict[str, Any]:
        """Return a summary of template store statistics."""
        total = len(self._patterns)
        active = self.active_pattern_count
        total_uses = sum(p.usage_count for p in self._patterns)
        total_successes = sum(p.success_count for p in self._patterns)

        return {
            "total_patterns": total,
            "active_patterns": active,
            "retired_patterns": total - active,
            "total_uses": total_uses,
            "total_successes": total_successes,
            "overall_success_rate": (total_successes / max(total_uses, 1)),
            "patterns_by_action": self._count_by_action(),
        }

    def reset(self) -> None:
        """Clear all learned patterns (for testing/reset purposes)."""
        self._patterns = []
        self._stats = {}
        self._save()
        logger.info("TemplateStore: reset — all patterns cleared")

    # ------------------------------------------------------------------
    # Pattern Generalization
    # ------------------------------------------------------------------

    def _generalize_step(self, step_action: str) -> str:
        """Convert a specific step into a generalized regex pattern.

        Example:
          "Enter 'admin123' into the email field"
          → r"(?:enter|type|input|fill)\\s+['\"]?(?P<value>[^'\"]+?)['\"]?\\s+(?:into|in)\\s+(?:the\\s+)?(?P<field>[^'\"]+?)\\s*(?:field|input)?\\s*$"
        """
        action = step_action.strip().lower()

        # Identify quoted values and replace with capture groups
        # Find all quoted strings
        quoted_values = re.findall(r"['\"]([^'\"]+)['\"]", step_action)

        # Find potential element references (words after "the" or at end)
        # This is a heuristic — not perfect but covers common cases

        # Strategy: replace specific values with named groups
        pattern = re.escape(action)

        # Replace quoted values with generic capture groups
        for i, val in enumerate(quoted_values):
            escaped_val = re.escape(val.lower())
            group_name = f"value{i}" if i > 0 else "value"
            pattern = pattern.replace(
                re.escape(f"'{val.lower()}'"), f"['\"]?(?P<{group_name}>[^'\"]+?)['\"]?"
            )
            pattern = pattern.replace(
                re.escape(f'"{val.lower()}"'), f"['\"]?(?P<{group_name}>[^'\"]+?)['\"]?"
            )

        # Replace common action verbs with alternatives
        verb_groups = {
            r"\\b(?:enter|type|input|fill|put)\\b": r"(?:enter|type|input|fill|put)",
            r"\\b(?:click|tap|press|hit)\\b": r"(?:click|tap|press|hit)",
            r"\\b(?:verify|check|assert|confirm)\\b": r"(?:verify|check|assert|confirm)",
            r"\\b(?:navigate|go|open|visit)\\b": r"(?:navigate|go|open|visit)",
            r"\\b(?:select|choose|pick)\\b": r"(?:select|choose|pick)",
            r"\\b(?:wait|pause)\\b": r"(?:wait|pause)",
        }

        for verb_pattern, replacement in verb_groups.items():
            for verb in re.findall(r"(?:enter|type|input|fill|put|click|tap|press|hit|verify|check|assert|confirm|navigate|go|open|visit|select|choose|pick|wait|pause)", action):
                escaped_verb = re.escape(verb)
                if escaped_verb in pattern:
                    # Find which group this verb belongs to
                    for vp, rep in verb_groups.items():
                        if verb in vp or verb in rep:
                            pattern = pattern.replace(escaped_verb, rep, 1)
                            break
                    break

        # Basic validation — make sure it's a valid regex
        try:
            re.compile(pattern, re.IGNORECASE)
        except re.error:
            return ""

        return pattern

    def _templatize_code(self, step_action: str, generated_code: str) -> str:
        """Convert specific generated code into a reusable template.

        Replaces specific values with template variables.
        """
        template = generated_code

        # Find quoted values in the original step
        quoted_values = re.findall(r"['\"]([^'\"]+)['\"]", step_action)

        # Replace those values in the generated code with template vars
        for i, val in enumerate(quoted_values):
            var_name = f"value{i}" if i > 0 else "value"
            # Replace in single-quoted strings
            template = template.replace(f"'{val}'", f"'{{{var_name}}}'")
            # Replace in double-quoted strings
            template = template.replace(f'"{val}"', f'"{{{var_name}}}"')
            # Replace bare (no quotes around template var itself in code)
            template = template.replace(val, f"{{{var_name}}}")

        return template

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    def _is_duplicate(self, new_pattern: str) -> bool:
        """Check if a pattern is too similar to existing patterns."""
        for existing in self._patterns:
            similarity = self._pattern_similarity(existing.pattern, new_pattern)
            if similarity >= _MAX_SIMILARITY_FOR_NEW:
                return True
        return False

    @staticmethod
    def _pattern_similarity(pattern_a: str, pattern_b: str) -> float:
        """Calculate Jaccard similarity between two pattern strings.

        Uses character trigrams for comparison.
        """
        def trigrams(s: str) -> set[str]:
            s = s.lower()
            return {s[i:i+3] for i in range(max(len(s) - 2, 1))}

        tri_a = trigrams(pattern_a)
        tri_b = trigrams(pattern_b)

        if not tri_a and not tri_b:
            return 1.0
        if not tri_a or not tri_b:
            return 0.0

        intersection = tri_a & tri_b
        union = tri_a | tri_b
        return len(intersection) / len(union)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load patterns and stats from disk."""
        # Load patterns
        if self._store_path.exists():
            try:
                data = json.loads(self._store_path.read_text(encoding="utf-8"))
                self._patterns = [TemplatePattern(**p) for p in data.get("patterns", [])]
                logger.info("TemplateStore: loaded %d patterns from %s", len(self._patterns), self._store_path)
            except Exception as exc:
                logger.warning("TemplateStore: failed to load patterns — %s", exc)
                self._patterns = []
        else:
            self._patterns = []

        # Load stats
        if self._stats_path.exists():
            try:
                self._stats = json.loads(self._stats_path.read_text(encoding="utf-8"))
            except Exception:
                self._stats = {}
        else:
            self._stats = {}

    def _save(self) -> None:
        """Persist patterns and stats to disk."""
        # Ensure directory exists
        self._store_path.parent.mkdir(parents=True, exist_ok=True)

        # Save patterns
        try:
            data = {"patterns": [p.model_dump() for p in self._patterns]}
            self._store_path.write_text(
                json.dumps(data, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("TemplateStore: failed to save patterns — %s", exc)

        # Save stats
        try:
            self._stats_path.write_text(
                json.dumps(self._stats, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.error("TemplateStore: failed to save stats — %s", exc)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _success_rate(pattern: TemplatePattern) -> float:
        """Calculate success rate for a pattern."""
        if pattern.usage_count == 0:
            return 1.0
        return pattern.success_count / pattern.usage_count

    def _count_by_action(self) -> dict[str, int]:
        """Count patterns grouped by action type."""
        counts: dict[str, int] = {}
        for p in self._patterns:
            key = p.action_type.value if isinstance(p.action_type, ActionType) else str(p.action_type)
            counts[key] = counts.get(key, 0) + 1
        return counts
