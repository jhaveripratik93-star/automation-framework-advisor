"""Template Engine — pattern matching and confidence-based code generation.

Matches manual test steps against known patterns (static + learned) and
produces framework-specific code when confidence is above threshold.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from src.codegen.models import (
    ActionType,
    MatchResult,
    TargetFramework,
    TemplatePattern,
    TestStep,
)

logger = logging.getLogger(__name__)


# ── Static Template Patterns ──────────────────────────────────────────

_STATIC_PATTERNS: list[dict[str, Any]] = [
    # Navigation
    {
        "id": "nav_goto",
        "pattern": r"(?:navigate|go|open|visit|browse|launch)\s+(?:to\s+)?(?:the\s+)?(?:url\s+)?['\"]?(?P<url>[^'\"]+?)['\"]?\s*$",
        "action_type": ActionType.NAVIGATE,
        "code_templates": {
            "playwright_ts": "await page.goto('{url}');",
            "playwright_py": "page.goto('{url}')",
            "selenium_py": "driver.get('{url}')",
            "selenium_java": "driver.get(\"{url}\");",
            "cypress_js": "cy.visit('{url}');",
        },
    },
    {
        "id": "nav_page",
        "pattern": r"(?:navigate|go|open|visit)\s+(?:to\s+)?(?:the\s+)?(?P<page_name>\w[\w\s]*?)\s+page\s*$",
        "action_type": ActionType.NAVIGATE,
        "code_templates": {
            "playwright_ts": "await page.goto('/{page_name_slug}');",
            "playwright_py": "page.goto('/{page_name_slug}')",
            "selenium_py": "driver.get(base_url + '/{page_name_slug}')",
            "selenium_java": "driver.get(baseUrl + \"/{page_name_slug}\");",
            "cypress_js": "cy.visit('/{page_name_slug}');",
        },
    },
    # Click actions
    {
        "id": "click_element",
        "pattern": r"(?:click|tap|press|hit)\s+(?:on\s+)?(?:the\s+)?['\"]?(?P<element>[^'\"]+?)['\"]?\s*(?:button|link|tab|icon|element|menu|option)?\s*$",
        "action_type": ActionType.CLICK,
        "code_templates": {
            "playwright_ts": "await page.locator('{selector}').click();",
            "playwright_py": "page.locator('{selector}').click()",
            "selenium_py": "driver.find_element(By.CSS_SELECTOR, '{selector}').click()",
            "selenium_java": "driver.findElement(By.cssSelector(\"{selector}\")).click();",
            "cypress_js": "cy.get('{selector}').click();",
        },
    },
    {
        "id": "click_button_named",
        "pattern": r"(?:click|tap|press)\s+(?:on\s+)?(?:the\s+)?['\"]?(?P<button_name>[^'\"]+?)['\"]?\s+button\s*$",
        "action_type": ActionType.CLICK,
        "code_templates": {
            "playwright_ts": "await page.getByRole('button', {{ name: '{button_name}' }}).click();",
            "playwright_py": "page.get_by_role('button', name='{button_name}').click()",
            "selenium_py": "driver.find_element(By.XPATH, \"//button[contains(text(), '{button_name}')]\").click()",
            "selenium_java": "driver.findElement(By.xpath(\"//button[contains(text(), '{button_name}')]\")).click();",
            "cypress_js": "cy.contains('button', '{button_name}').click();",
        },
    },
    {
        "id": "click_link_named",
        "pattern": r"(?:click|tap)\s+(?:on\s+)?(?:the\s+)?['\"]?(?P<link_name>[^'\"]+?)['\"]?\s+link\s*$",
        "action_type": ActionType.CLICK,
        "code_templates": {
            "playwright_ts": "await page.getByRole('link', {{ name: '{link_name}' }}).click();",
            "playwright_py": "page.get_by_role('link', name='{link_name}').click()",
            "selenium_py": "driver.find_element(By.LINK_TEXT, '{link_name}').click()",
            "selenium_java": "driver.findElement(By.linkText(\"{link_name}\")).click();",
            "cypress_js": "cy.contains('a', '{link_name}').click();",
        },
    },
    # Fill / Input actions
    {
        "id": "fill_field",
        "pattern": r"(?:enter|type|input|fill|put)\s+['\"]?(?P<value>[^'\"]+?)['\"]?\s+(?:in|into|in the|into the)\s+(?:the\s+)?['\"]?(?P<field>[^'\"]+?)['\"]?\s*(?:field|input|textbox|text box|text area|textarea)?\s*$",
        "action_type": ActionType.FILL,
        "code_templates": {
            "playwright_ts": "await page.locator('{selector}').fill('{value}');",
            "playwright_py": "page.locator('{selector}').fill('{value}')",
            "selenium_py": "driver.find_element(By.CSS_SELECTOR, '{selector}').send_keys('{value}')",
            "selenium_java": "driver.findElement(By.cssSelector(\"{selector}\")).sendKeys(\"{value}\");",
            "cypress_js": "cy.get('{selector}').type('{value}');",
        },
    },
    {
        "id": "fill_field_reverse",
        "pattern": r"(?:in|into)\s+(?:the\s+)?['\"]?(?P<field>[^'\"]+?)['\"]?\s*(?:field|input)?\s*,?\s*(?:enter|type|input|fill|put)\s+['\"]?(?P<value>[^'\"]+?)['\"]?\s*$",
        "action_type": ActionType.FILL,
        "code_templates": {
            "playwright_ts": "await page.locator('{selector}').fill('{value}');",
            "playwright_py": "page.locator('{selector}').fill('{value}')",
            "selenium_py": "driver.find_element(By.CSS_SELECTOR, '{selector}').send_keys('{value}')",
            "selenium_java": "driver.findElement(By.cssSelector(\"{selector}\")).sendKeys(\"{value}\");",
            "cypress_js": "cy.get('{selector}').type('{value}');",
        },
    },
    # Select / Dropdown actions
    {
        "id": "select_option",
        "pattern": r"(?:select|choose|pick)\s+['\"]?(?P<option>[^'\"]+?)['\"]?\s+(?:from|in)\s+(?:the\s+)?['\"]?(?P<dropdown>[^'\"]+?)['\"]?\s*(?:dropdown|select|menu|list)?\s*$",
        "action_type": ActionType.SELECT,
        "code_templates": {
            "playwright_ts": "await page.locator('{selector}').selectOption('{option}');",
            "playwright_py": "page.locator('{selector}').select_option('{option}')",
            "selenium_py": "Select(driver.find_element(By.CSS_SELECTOR, '{selector}')).select_by_visible_text('{option}')",
            "selenium_java": "new Select(driver.findElement(By.cssSelector(\"{selector}\"))).selectByVisibleText(\"{option}\");",
            "cypress_js": "cy.get('{selector}').select('{option}');",
        },
    },
    # Assertion: element visible
    {
        "id": "assert_visible",
        "pattern": r"(?:verify|check|assert|confirm|ensure|validate)\s+(?:that\s+)?(?:the\s+)?['\"]?(?P<element>[^'\"]+?)['\"]?\s+(?:is\s+)?(?:visible|shown|displayed|present|appears)\s*$",
        "action_type": ActionType.ASSERT_VISIBLE,
        "code_templates": {
            "playwright_ts": "await expect(page.locator('{selector}')).toBeVisible();",
            "playwright_py": "expect(page.locator('{selector}')).to_be_visible()",
            "selenium_py": "assert driver.find_element(By.CSS_SELECTOR, '{selector}').is_displayed()",
            "selenium_java": "assertTrue(driver.findElement(By.cssSelector(\"{selector}\")).isDisplayed());",
            "cypress_js": "cy.get('{selector}').should('be.visible');",
        },
    },
    # Assertion: text content
    {
        "id": "assert_text_contains",
        "pattern": r"(?:verify|check|assert|confirm|ensure)\s+(?:that\s+)?(?:the\s+)?(?:page|screen)?\s*(?:shows|contains|displays|has)\s+(?:the\s+)?(?:text\s+)?['\"](?P<text>[^'\"]+)['\"]",
        "action_type": ActionType.ASSERT_TEXT,
        "code_templates": {
            "playwright_ts": "await expect(page.locator('body')).toContainText('{text}');",
            "playwright_py": "expect(page.locator('body')).to_contain_text('{text}')",
            "selenium_py": "assert '{text}' in driver.page_source",
            "selenium_java": "assertTrue(driver.getPageSource().contains(\"{text}\"));",
            "cypress_js": "cy.contains('{text}').should('exist');",
        },
    },
    {
        "id": "assert_element_text",
        "pattern": r"(?:verify|check|assert|confirm)\s+(?:that\s+)?(?:the\s+)?['\"]?(?P<element>[^'\"]+?)['\"]?\s+(?:shows|contains|displays|has text)\s+['\"](?P<text>[^'\"]+)['\"]",
        "action_type": ActionType.ASSERT_TEXT,
        "code_templates": {
            "playwright_ts": "await expect(page.locator('{selector}')).toContainText('{text}');",
            "playwright_py": "expect(page.locator('{selector}')).to_contain_text('{text}')",
            "selenium_py": "assert '{text}' in driver.find_element(By.CSS_SELECTOR, '{selector}').text",
            "selenium_java": "assertTrue(driver.findElement(By.cssSelector(\"{selector}\")).getText().contains(\"{text}\"));",
            "cypress_js": "cy.get('{selector}').should('contain.text', '{text}');",
        },
    },
    # Assertion: URL
    {
        "id": "assert_url",
        "pattern": r"(?:verify|check|assert|confirm)\s+(?:that\s+)?(?:the\s+)?(?:url|URL|address)\s+(?:is|equals|contains)\s+['\"]?(?P<url>[^'\"]+?)['\"]?\s*$",
        "action_type": ActionType.ASSERT_URL,
        "code_templates": {
            "playwright_ts": "await expect(page).toHaveURL('{url}');",
            "playwright_py": "expect(page).to_have_url('{url}')",
            "selenium_py": "assert driver.current_url == '{url}'",
            "selenium_java": "assertEquals(\"{url}\", driver.getCurrentUrl());",
            "cypress_js": "cy.url().should('include', '{url}');",
        },
    },
    # Wait actions
    {
        "id": "wait_element",
        "pattern": r"(?:wait|wait for)\s+(?:the\s+)?['\"]?(?P<element>[^'\"]+?)['\"]?\s+(?:to\s+)?(?:be\s+)?(?:visible|appear|load|show)\s*$",
        "action_type": ActionType.WAIT,
        "code_templates": {
            "playwright_ts": "await page.locator('{selector}').waitFor({{ state: 'visible' }});",
            "playwright_py": "page.locator('{selector}').wait_for(state='visible')",
            "selenium_py": "WebDriverWait(driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, '{selector}')))",
            "selenium_java": "new WebDriverWait(driver, Duration.ofSeconds(10)).until(ExpectedConditions.visibilityOfElementLocated(By.cssSelector(\"{selector}\")));",
            "cypress_js": "cy.get('{selector}').should('be.visible');",
        },
    },
    {
        "id": "wait_seconds",
        "pattern": r"(?:wait|pause|sleep)\s+(?:for\s+)?(?P<seconds>\d+)\s*(?:seconds?|secs?|s)\s*$",
        "action_type": ActionType.WAIT,
        "code_templates": {
            "playwright_ts": "await page.waitForTimeout({seconds}000);",
            "playwright_py": "page.wait_for_timeout({seconds}000)",
            "selenium_py": "import time; time.sleep({seconds})",
            "selenium_java": "Thread.sleep({seconds}000);",
            "cypress_js": "cy.wait({seconds}000);",
        },
    },
    # Hover
    {
        "id": "hover_element",
        "pattern": r"(?:hover|mouse\s*over)\s+(?:on|over)?\s*(?:the\s+)?['\"]?(?P<element>[^'\"]+?)['\"]?\s*$",
        "action_type": ActionType.HOVER,
        "code_templates": {
            "playwright_ts": "await page.locator('{selector}').hover();",
            "playwright_py": "page.locator('{selector}').hover()",
            "selenium_py": "ActionChains(driver).move_to_element(driver.find_element(By.CSS_SELECTOR, '{selector}')).perform()",
            "selenium_java": "new Actions(driver).moveToElement(driver.findElement(By.cssSelector(\"{selector}\"))).perform();",
            "cypress_js": "cy.get('{selector}').trigger('mouseover');",
        },
    },
    # Upload
    {
        "id": "upload_file",
        "pattern": r"(?:upload|attach|choose file)\s+['\"]?(?P<file_path>[^'\"]+?)['\"]?\s+(?:to|in|into)\s+(?:the\s+)?['\"]?(?P<element>[^'\"]+?)['\"]?\s*$",
        "action_type": ActionType.UPLOAD,
        "code_templates": {
            "playwright_ts": "await page.locator('{selector}').setInputFiles('{file_path}');",
            "playwright_py": "page.locator('{selector}').set_input_files('{file_path}')",
            "selenium_py": "driver.find_element(By.CSS_SELECTOR, '{selector}').send_keys('{file_path}')",
            "selenium_java": "driver.findElement(By.cssSelector(\"{selector}\")).sendKeys(\"{file_path}\");",
            "cypress_js": "cy.get('{selector}').attachFile('{file_path}');",
        },
    },
    # Scroll
    {
        "id": "scroll_to_element",
        "pattern": r"(?:scroll)\s+(?:to|down to|up to)\s+(?:the\s+)?['\"]?(?P<element>[^'\"]+?)['\"]?\s*$",
        "action_type": ActionType.SCROLL,
        "code_templates": {
            "playwright_ts": "await page.locator('{selector}').scrollIntoViewIfNeeded();",
            "playwright_py": "page.locator('{selector}').scroll_into_view_if_needed()",
            "selenium_py": "driver.execute_script('arguments[0].scrollIntoView(true);', driver.find_element(By.CSS_SELECTOR, '{selector}'))",
            "selenium_java": "((JavascriptExecutor) driver).executeScript(\"arguments[0].scrollIntoView(true);\", driver.findElement(By.cssSelector(\"{selector}\")));",
            "cypress_js": "cy.get('{selector}').scrollIntoView();",
        },
    },
    # Keyboard actions
    {
        "id": "press_key",
        "pattern": r"(?:press|hit)\s+(?:the\s+)?(?P<key>enter|tab|escape|backspace|delete|space|arrow\s*\w+)\s*(?:key)?\s*$",
        "action_type": ActionType.KEYBOARD,
        "code_templates": {
            "playwright_ts": "await page.keyboard.press('{key_normalized}');",
            "playwright_py": "page.keyboard.press('{key_normalized}')",
            "selenium_py": "from selenium.webdriver.common.keys import Keys; driver.switch_to.active_element.send_keys(Keys.{key_upper})",
            "selenium_java": "driver.switchTo().activeElement().sendKeys(Keys.{key_upper});",
            "cypress_js": "cy.focused().type('{{{}}}');".format("{key_cypress}"),
        },
    },
]


# ── Template Engine ───────────────────────────────────────────────────


class TemplateEngine:
    """Matches test steps against patterns and generates framework-specific code.

    Uses regex-based pattern matching with confidence scoring.
    Falls through to LLM when confidence is below threshold.
    """

    def __init__(
        self,
        learned_patterns: list[TemplatePattern] | None = None,
        selector_map: dict[str, str] | None = None,
    ) -> None:
        self._static_patterns = self._build_static_patterns()
        self._learned_patterns = learned_patterns or []
        self._selector_map = selector_map or {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def match(
        self,
        step: TestStep,
        framework: TargetFramework,
    ) -> MatchResult:
        """Attempt to match a test step against known patterns.

        Returns a MatchResult with confidence score. If confidence >= threshold,
        the generated_code field contains usable code.
        """
        action_text = self._normalize_step(step.action)

        # Try learned patterns first (higher priority — user-validated)
        for pattern in self._learned_patterns:
            if not pattern.enabled:
                continue
            result = self._try_pattern(pattern, action_text, framework)
            if result.matched:
                logger.debug(
                    "TemplateEngine: learned pattern '%s' matched (conf=%.2f)",
                    pattern.id, result.confidence,
                )
                return result

        # Then try static patterns
        for pattern in self._static_patterns:
            result = self._try_pattern(pattern, action_text, framework)
            if result.matched:
                logger.debug(
                    "TemplateEngine: static pattern '%s' matched (conf=%.2f)",
                    pattern.id, result.confidence,
                )
                return result

        # No match
        return MatchResult(matched=False, confidence=0.0)

    def get_all_patterns(self) -> list[TemplatePattern]:
        """Return all patterns (static + learned)."""
        return self._static_patterns + self._learned_patterns

    def add_learned_pattern(self, pattern: TemplatePattern) -> None:
        """Add a newly learned pattern to the engine."""
        self._learned_patterns.append(pattern)

    def update_selector_map(self, selector_map: dict[str, str]) -> None:
        """Update the element-to-selector mapping."""
        self._selector_map.update(selector_map)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_static_patterns(self) -> list[TemplatePattern]:
        """Convert raw pattern dicts to TemplatePattern objects."""
        patterns = []
        for p in _STATIC_PATTERNS:
            patterns.append(TemplatePattern(
                id=p["id"],
                pattern=p["pattern"],
                action_type=p["action_type"],
                code_templates=p["code_templates"],
                source="static",
            ))
        return patterns

    def _try_pattern(
        self,
        pattern: TemplatePattern,
        action_text: str,
        framework: TargetFramework,
    ) -> MatchResult:
        """Try to match action_text against a single pattern."""
        framework_key = framework.value

        # Check if this pattern supports the target framework
        if framework_key not in pattern.code_templates:
            return MatchResult(matched=False, confidence=0.0)

        try:
            match = re.search(pattern.pattern, action_text, re.IGNORECASE)
        except re.error:
            logger.warning("TemplateEngine: invalid regex in pattern '%s'", pattern.id)
            return MatchResult(matched=False, confidence=0.0)

        if not match:
            return MatchResult(matched=False, confidence=0.0)

        # Extract named groups
        groups = match.groupdict()
        confidence = self._calculate_confidence(match, groups, action_text)

        # Generate code from template
        code_template = pattern.code_templates[framework_key]
        generated_code = self._render_template(code_template, groups, pattern.action_type)

        return MatchResult(
            matched=True,
            pattern_id=pattern.id,
            confidence=confidence,
            generated_code=generated_code,
            action_type=pattern.action_type,
            captured_groups=groups,
        )

    def _calculate_confidence(
        self,
        match: re.Match,
        groups: dict[str, str],
        original_text: str,
    ) -> float:
        """Calculate confidence score for a match.

        Factors:
        - How much of the original text was consumed by the match
        - Whether all expected named groups were filled
        - Whether a selector mapping exists for referenced elements
        """
        # Base: proportion of text matched
        matched_span = match.end() - match.start()
        coverage = matched_span / max(len(original_text), 1)
        base_score = min(0.95, 0.6 + (coverage * 0.35))

        # Modifier: all named groups filled?
        filled_groups = sum(1 for v in groups.values() if v and v.strip())
        total_groups = max(len(groups), 1)
        group_fill_ratio = filled_groups / total_groups
        base_score *= (0.85 + 0.15 * group_fill_ratio)

        # Bonus: selector available for referenced element
        for key in ("element", "field", "dropdown", "button_name", "link_name"):
            if key in groups and groups[key]:
                element_name = groups[key].strip().lower()
                if element_name in self._selector_map:
                    base_score = min(1.0, base_score + 0.05)

        return round(base_score, 3)

    def _render_template(
        self,
        template: str,
        groups: dict[str, str],
        action_type: ActionType,
    ) -> str:
        """Fill in a code template with captured values and selectors."""
        render_vars: dict[str, str] = {}

        for key, value in groups.items():
            if value:
                render_vars[key] = value.strip()

        # Resolve selector for element-based actions
        selector = self._resolve_selector(groups, action_type)
        render_vars["selector"] = selector

        # Special transformations
        if "page_name" in groups and groups["page_name"]:
            render_vars["page_name_slug"] = self._slugify(groups["page_name"])

        if "key" in groups and groups["key"]:
            key_val = groups["key"].strip().lower().replace(" ", "")
            render_vars["key_normalized"] = key_val.capitalize()
            render_vars["key_upper"] = key_val.upper().replace("ARROWDOWN", "ARROW_DOWN").replace("ARROWUP", "ARROW_UP")
            render_vars["key_cypress"] = key_val

        if "seconds" in groups and groups["seconds"]:
            render_vars["seconds"] = groups["seconds"]

        # Apply substitutions
        code = template
        for key, value in render_vars.items():
            code = code.replace(f"{{{key}}}", value)

        return code

    def _resolve_selector(self, groups: dict[str, str], action_type: ActionType) -> str:
        """Resolve an element description to a CSS selector.

        Priority:
        1. User-provided selector_map
        2. data-testid convention
        3. Generic placeholder with TODO comment
        """
        # Identify the element reference from captured groups
        element_name = ""
        for key in ("element", "field", "dropdown", "button_name", "link_name"):
            if key in groups and groups[key]:
                element_name = groups[key].strip().lower()
                break

        if not element_name:
            return "[data-testid='unknown']"

        # 1. Check user-provided mapping
        if element_name in self._selector_map:
            return self._selector_map[element_name]

        # 2. Normalized variants
        normalized = element_name.replace(" ", "_").replace("-", "_")
        if normalized in self._selector_map:
            return self._selector_map[normalized]

        # 3. Convention-based best guess
        slug = self._slugify(element_name)
        return f"[data-testid='{slug}']"

    @staticmethod
    def _normalize_step(action: str) -> str:
        """Normalize step text for matching."""
        text = action.strip()
        # Remove leading step numbers like "1." or "Step 1:"
        text = re.sub(r"^(?:step\s*)?\d+[\.\):\-]\s*", "", text, flags=re.IGNORECASE)
        # Remove trailing punctuation
        text = text.rstrip(".")
        return text

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text to a slug suitable for test IDs."""
        slug = text.strip().lower()
        slug = re.sub(r"[^a-z0-9]+", "-", slug)
        slug = slug.strip("-")
        return slug
