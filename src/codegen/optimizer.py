"""Multi-test optimizer — detects shared patterns across test cases.

When multiple test cases are generated, this module analyzes them to:
  1. Extract repeated step sequences → helper functions / page objects
  2. Detect data-only differences → parameterized tests
  3. Identify common setup/teardown → fixtures / beforeEach
  4. Group selectors by page → page object classes
"""
from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter, defaultdict
from typing import Any

from src.codegen.models import (
    FixtureSpec,
    HelperSpec,
    OptimizationResult,
    PageMethod,
    PageObjectSpec,
    ParameterizedGroup,
    StepGenerationResult,
    TargetFramework,
    TestCaseGenerationResult,
)

logger = logging.getLogger(__name__)

_MIN_SHARED_STEPS_FOR_HELPER = 2   # Steps appearing in 2+ tests → helper
_MIN_STEPS_FOR_PAGE_OBJECT = 3     # 3+ shared selectors on same page → page object
_MIN_COMMON_PREFIX_FOR_FIXTURE = 2 # 2+ common leading steps → fixture


class TestOptimizer:
    """Analyzes multiple generated test cases and extracts shared patterns.

    Produces page objects, fixtures, parameterized groups, and helpers
    that reduce duplication and produce professional-quality test suites.
    """

    def __init__(self, framework: TargetFramework) -> None:
        self._framework = framework

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def optimize(
        self,
        test_results: list[TestCaseGenerationResult],
    ) -> OptimizationResult:
        """Run all optimization passes on the generated test cases.

        Args:
            test_results: Generated code for each test case.

        Returns:
            OptimizationResult with extracted page objects, fixtures, etc.
        """
        if len(test_results) <= 1:
            # Nothing to optimize with a single test
            return OptimizationResult()

        logger.info("TestOptimizer: analyzing %d test cases for optimization", len(test_results))

        # Pass 1: Find common setup (fixture candidates)
        fixtures = self._find_common_setup(test_results)

        # Pass 2: Find repeated step sequences (helper candidates)
        helpers = self._find_repeated_sequences(test_results)

        # Pass 3: Group selectors by page (page object candidates)
        page_objects = self._find_page_objects(test_results)

        # Pass 4: Find data-only differences (parameterized test candidates)
        parameterized = self._find_parameterized_groups(test_results)

        result = OptimizationResult(
            page_objects=page_objects,
            fixtures=fixtures,
            parameterized_groups=parameterized,
            shared_helpers=helpers,
        )

        logger.info(
            "TestOptimizer: found %d page objects, %d fixtures, %d param groups, %d helpers",
            len(page_objects), len(fixtures), len(parameterized), len(helpers),
        )
        return result

    # ------------------------------------------------------------------
    # Pass 1: Common Setup → Fixtures
    # ------------------------------------------------------------------

    def _find_common_setup(
        self,
        test_results: list[TestCaseGenerationResult],
    ) -> list[FixtureSpec]:
        """Find steps that appear at the beginning of multiple tests."""
        if len(test_results) < 2:
            return []

        fixtures: list[FixtureSpec] = []

        # Get the step codes for each test
        all_step_lists = []
        for tr in test_results:
            steps = [sr.generated_code.strip() for sr in tr.step_results if sr.generated_code.strip()]
            all_step_lists.append(steps)

        # Find longest common prefix
        common_prefix = self._longest_common_prefix(all_step_lists)

        if len(common_prefix) >= _MIN_COMMON_PREFIX_FOR_FIXTURE:
            setup_code = "\n".join(common_prefix)
            fixture_name = self._generate_fixture_name(common_prefix)

            fixtures.append(FixtureSpec(
                name=fixture_name,
                file_path=self._fixture_file_path(fixture_name),
                setup_code=setup_code,
                teardown_code="",
                scope="test",
            ))
            logger.debug(
                "TestOptimizer: found common setup (%d steps) → fixture '%s'",
                len(common_prefix), fixture_name,
            )

        return fixtures

    # ------------------------------------------------------------------
    # Pass 2: Repeated Sequences → Helpers
    # ------------------------------------------------------------------

    def _find_repeated_sequences(
        self,
        test_results: list[TestCaseGenerationResult],
    ) -> list[HelperSpec]:
        """Find step sequences that appear in multiple test cases."""
        helpers: list[HelperSpec] = []

        # Hash consecutive step pairs and triples
        sequence_occurrences: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for tr in test_results:
            steps = tr.step_results
            # Check pairs (2 consecutive steps)
            for i in range(len(steps) - 1):
                pair_code = f"{steps[i].generated_code.strip()}\n{steps[i+1].generated_code.strip()}"
                pair_hash = self._code_hash(pair_code)
                sequence_occurrences[pair_hash].append({
                    "test_id": tr.test_case_id,
                    "code": pair_code,
                    "start_step": steps[i].step_number,
                    "actions": [steps[i].original_action, steps[i+1].original_action],
                })

            # Check triples (3 consecutive steps)
            for i in range(len(steps) - 2):
                triple_code = "\n".join(
                    s.generated_code.strip() for s in steps[i:i+3]
                )
                triple_hash = self._code_hash(triple_code)
                sequence_occurrences[triple_hash].append({
                    "test_id": tr.test_case_id,
                    "code": triple_code,
                    "start_step": steps[i].step_number,
                    "actions": [s.original_action for s in steps[i:i+3]],
                })

        # Extract sequences that appear in 2+ test cases
        seen_test_ids: set[str] = set()
        for seq_hash, occurrences in sequence_occurrences.items():
            # Only count unique test cases
            unique_tests = {occ["test_id"] for occ in occurrences}
            if len(unique_tests) >= _MIN_SHARED_STEPS_FOR_HELPER:
                # Avoid duplicates (if a pair is part of a triple already extracted)
                test_combo = frozenset(unique_tests)
                combo_key = f"{seq_hash}_{test_combo}"
                if combo_key in seen_test_ids:
                    continue
                seen_test_ids.add(combo_key)

                first = occurrences[0]
                helper_name = self._generate_helper_name(first["actions"])
                params = self._extract_helper_params(first["code"])

                helpers.append(HelperSpec(
                    name=helper_name,
                    params=params,
                    body=first["code"],
                    used_by=list(unique_tests),
                ))

        return helpers

    # ------------------------------------------------------------------
    # Pass 3: Selector Grouping → Page Objects
    # ------------------------------------------------------------------

    def _find_page_objects(
        self,
        test_results: list[TestCaseGenerationResult],
    ) -> list[PageObjectSpec]:
        """Group selectors by logical page to create page objects."""
        page_objects: list[PageObjectSpec] = []

        # Collect all selectors used across all tests
        selector_usage: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for tr in test_results:
            for sr in tr.step_results:
                selectors = self._extract_selectors(sr.generated_code)
                for selector in selectors:
                    selector_usage[selector].append({
                        "test_id": tr.test_case_id,
                        "action": sr.original_action,
                        "action_type": sr.action_type.value,
                        "code": sr.generated_code,
                    })

        # Group selectors that co-occur in the same tests (likely same page)
        # Simple heuristic: selectors that appear together in 2+ tests
        co_occurrence: dict[str, set[str]] = defaultdict(set)
        for selector, usages in selector_usage.items():
            test_ids = {u["test_id"] for u in usages}
            for other_selector, other_usages in selector_usage.items():
                if other_selector == selector:
                    continue
                other_test_ids = {u["test_id"] for u in other_usages}
                if len(test_ids & other_test_ids) >= 2:
                    co_occurrence[selector].add(other_selector)

        # Build page object from clusters
        processed: set[str] = set()
        for selector, co_selectors in co_occurrence.items():
            if selector in processed:
                continue

            cluster = {selector} | co_selectors
            if len(cluster) < _MIN_STEPS_FOR_PAGE_OBJECT:
                continue

            # Mark as processed
            processed.update(cluster)

            # Determine page name from selector patterns
            page_name = self._infer_page_name(cluster)

            # Build selectors dict
            selectors_dict: dict[str, str] = {}
            methods: list[PageMethod] = []

            for sel in cluster:
                element_name = self._selector_to_name(sel)
                selectors_dict[element_name] = sel

                # Create methods from the actions performed on these selectors
                for usage in selector_usage.get(sel, []):
                    method = self._create_page_method(element_name, usage)
                    if method and method.name not in {m.name for m in methods}:
                        methods.append(method)

            page_objects.append(PageObjectSpec(
                name=page_name,
                file_path=self._page_object_file_path(page_name),
                selectors=selectors_dict,
                methods=methods,
            ))

        return page_objects

    # ------------------------------------------------------------------
    # Pass 4: Data-Only Differences → Parameterized Tests
    # ------------------------------------------------------------------

    def _find_parameterized_groups(
        self,
        test_results: list[TestCaseGenerationResult],
    ) -> list[ParameterizedGroup]:
        """Find test cases that differ only in data values."""
        parameterized: list[ParameterizedGroup] = []

        # Compare each pair of test cases
        n = len(test_results)
        grouped: set[str] = set()

        for i in range(n):
            if test_results[i].test_case_id in grouped:
                continue

            group_ids = [test_results[i].test_case_id]
            group_params: list[dict[str, Any]] = []

            for j in range(i + 1, n):
                if test_results[j].test_case_id in grouped:
                    continue

                diff = self._compute_data_diff(test_results[i], test_results[j])
                if diff is not None:
                    group_ids.append(test_results[j].test_case_id)
                    if not group_params:
                        # Add first test case params
                        group_params.append(diff["params_a"])
                    group_params.append(diff["params_b"])

            if len(group_ids) >= 2 and group_params:
                # Extract the base code (with placeholders)
                base_code = self._create_parameterized_base(
                    test_results[i], group_params[0] if group_params else {}
                )
                param_names = list(group_params[0].keys()) if group_params else []

                parameterized.append(ParameterizedGroup(
                    test_case_ids=group_ids,
                    base_code=base_code,
                    parameters=group_params,
                    parameter_names=param_names,
                ))
                grouped.update(group_ids)

        return parameterized

    # ------------------------------------------------------------------
    # Helper Methods
    # ------------------------------------------------------------------

    @staticmethod
    def _longest_common_prefix(step_lists: list[list[str]]) -> list[str]:
        """Find the longest common prefix across multiple step code lists."""
        if not step_lists:
            return []

        prefix: list[str] = []
        min_len = min(len(sl) for sl in step_lists)

        for i in range(min_len):
            # Normalize for comparison (ignore whitespace differences)
            reference = step_lists[0][i].strip()
            if all(sl[i].strip() == reference for sl in step_lists):
                prefix.append(reference)
            else:
                break

        return prefix

    @staticmethod
    def _code_hash(code: str) -> str:
        """Hash code for comparison (normalized — ignoring whitespace)."""
        normalized = re.sub(r"\s+", " ", code.strip())
        return hashlib.md5(normalized.encode()).hexdigest()[:12]

    @staticmethod
    def _extract_selectors(code: str) -> list[str]:
        """Extract CSS selectors / locators from generated code."""
        selectors: list[str] = []

        # Match common selector patterns
        patterns = [
            r"locator\(['\"]([^'\"]+)['\"]\)",
            r"getByRole\(['\"]([^'\"]+)['\"]",
            r"get\(['\"]([^'\"]+)['\"]\)",
            r"find_element\([^,]+,\s*['\"]([^'\"]+)['\"]\)",
            r"findElement\([^,]+['\"]([^'\"]+)['\"]\)",
            r"By\.cssSelector\(['\"]([^'\"]+)['\"]\)",
            r"By\.CSS_SELECTOR,\s*['\"]([^'\"]+)['\"]\)",
            r"contains\(['\"]([^'\"]+)['\"]\)",
        ]

        for pattern in patterns:
            matches = re.findall(pattern, code)
            selectors.extend(matches)

        return selectors

    def _compute_data_diff(
        self,
        test_a: TestCaseGenerationResult,
        test_b: TestCaseGenerationResult,
    ) -> dict[str, Any] | None:
        """Check if two tests differ only in data values.

        Returns diff info if parameterizable, None otherwise.
        """
        steps_a = test_a.step_results
        steps_b = test_b.step_results

        # Must have same number of steps
        if len(steps_a) != len(steps_b):
            return None

        # Must have same action types
        if [s.action_type for s in steps_a] != [s.action_type for s in steps_b]:
            return None

        params_a: dict[str, Any] = {}
        params_b: dict[str, Any] = {}
        diff_count = 0

        for sa, sb in zip(steps_a, steps_b):
            code_a = sa.generated_code.strip()
            code_b = sb.generated_code.strip()

            if code_a == code_b:
                continue

            # Find what's different — extract literal values
            values_a = re.findall(r"['\"]([^'\"]+)['\"]", code_a)
            values_b = re.findall(r"['\"]([^'\"]+)['\"]", code_b)

            if len(values_a) != len(values_b):
                return None  # Structural difference, not just data

            for i, (va, vb) in enumerate(zip(values_a, values_b)):
                if va != vb:
                    param_name = f"param_{diff_count}_{i}"
                    params_a[param_name] = va
                    params_b[param_name] = vb
                    diff_count += 1

        # Only parameterize if differences are small relative to total steps
        if diff_count == 0 or diff_count > len(steps_a) * 0.5:
            return None

        return {"params_a": params_a, "params_b": params_b}

    def _create_parameterized_base(
        self,
        test_result: TestCaseGenerationResult,
        params: dict[str, Any],
    ) -> str:
        """Create base code template with parameter placeholders."""
        code = test_result.assembled_code or "\n".join(
            sr.generated_code for sr in test_result.step_results
        )

        # Replace specific values with parameter references
        for param_name, value in params.items():
            code = code.replace(f"'{value}'", f"${{{param_name}}}")
            code = code.replace(f'"{value}"', f"${{{param_name}}}")

        return code

    def _generate_fixture_name(self, common_steps: list[str]) -> str:
        """Generate a descriptive fixture name from common steps."""
        # Look for navigation or setup indicators
        first_step = common_steps[0].lower() if common_steps else ""
        if "goto" in first_step or "visit" in first_step or "navigate" in first_step:
            # Try to extract the page name
            url_match = re.search(r"['\"/](\w+)['\"]", first_step)
            if url_match:
                return f"navigate_to_{url_match.group(1)}"
        if "login" in first_step or "auth" in first_step:
            return "authenticated_user"
        return "common_setup"

    def _generate_helper_name(self, actions: list[str]) -> str:
        """Generate a helper function name from the actions it performs."""
        if not actions:
            return "shared_action"

        # Use first action as base
        first = actions[0].lower()
        if "login" in first:
            return "perform_login"
        if "search" in first:
            return "perform_search"
        if "fill" in first or "enter" in first:
            return "fill_form"
        if "navigate" in first or "go" in first:
            return "navigate_to_page"

        # Generic: slugify first action
        slug = re.sub(r"[^a-z0-9]+", "_", first)[:30]
        return f"do_{slug.strip('_')}"

    def _extract_helper_params(self, code: str) -> list[str]:
        """Extract parameterizable values from helper code."""
        # Find all string literals that look like test data
        values = re.findall(r"['\"]([^'\"]{1,50})['\"]", code)
        params: list[str] = []
        for v in values:
            # Skip selectors and URLs
            if v.startswith(("[", "#", ".", "/", "http", "data-")):
                continue
            if len(v) > 2:
                param_name = re.sub(r"[^a-z0-9]+", "_", v.lower())[:20]
                params.append(param_name)

        return params[:5]  # Limit to 5 params max

    def _fixture_file_path(self, name: str) -> str:
        """Generate file path for a fixture."""
        ext = self._get_file_extension()
        return f"fixtures/{name}.fixture{ext}"

    def _page_object_file_path(self, name: str) -> str:
        """Generate file path for a page object."""
        ext = self._get_file_extension()
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("Page", ""))
        return f"pages/{slug.strip('-')}.page{ext}"

    def _get_file_extension(self) -> str:
        """Get appropriate file extension for the framework."""
        ext_map = {
            TargetFramework.PLAYWRIGHT_TS: ".ts",
            TargetFramework.PLAYWRIGHT_PY: ".py",
            TargetFramework.SELENIUM_PY: ".py",
            TargetFramework.SELENIUM_JAVA: ".java",
            TargetFramework.CYPRESS_JS: ".js",
            TargetFramework.REST_ASSURED: ".java",
            TargetFramework.ROBOT_FRAMEWORK: ".robot",
        }
        return ext_map.get(self._framework, ".ts")

    @staticmethod
    def _infer_page_name(selectors: set[str]) -> str:
        """Infer a page name from a cluster of selectors."""
        # Look for common patterns in selector names
        all_text = " ".join(selectors).lower()
        if "login" in all_text or "auth" in all_text:
            return "LoginPage"
        if "register" in all_text or "signup" in all_text:
            return "RegisterPage"
        if "search" in all_text:
            return "SearchPage"
        if "cart" in all_text or "checkout" in all_text:
            return "CartPage"
        if "profile" in all_text or "account" in all_text:
            return "ProfilePage"
        if "dashboard" in all_text:
            return "DashboardPage"
        if "nav" in all_text or "menu" in all_text or "header" in all_text:
            return "NavigationPage"
        return "SharedPage"

    @staticmethod
    def _selector_to_name(selector: str) -> str:
        """Convert a selector to a readable element name."""
        # data-testid="username" → username
        match = re.search(r"data-testid=['\"]([^'\"]+)['\"]", selector)
        if match:
            return match.group(1).replace("-", "_")
        # #username → username
        if selector.startswith("#"):
            return selector[1:].replace("-", "_")
        # .login-btn → login_btn
        if selector.startswith("."):
            return selector[1:].replace("-", "_")
        # Generic
        slug = re.sub(r"[^a-z0-9]+", "_", selector.lower())
        return slug[:30].strip("_") or "element"

    def _create_page_method(
        self,
        element_name: str,
        usage: dict[str, Any],
    ) -> PageMethod | None:
        """Create a page object method from an element usage."""
        action_type = usage.get("action_type", "")
        code = usage.get("code", "")

        if action_type == "click":
            return PageMethod(
                name=f"click_{element_name}",
                params=[],
                body=code,
                doc=f"Click the {element_name} element",
            )
        elif action_type == "fill":
            return PageMethod(
                name=f"fill_{element_name}",
                params=["value"],
                body=code,
                doc=f"Fill the {element_name} field with a value",
            )
        elif action_type in ("assert_visible", "assert_text"):
            return PageMethod(
                name=f"verify_{element_name}",
                params=[],
                body=code,
                doc=f"Verify the {element_name} element",
            )

        return None
