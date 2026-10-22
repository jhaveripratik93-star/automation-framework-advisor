OUTPUT RULES (STRICT):
1. Output ONLY valid executable Python code using pytest-playwright idioms.
2. Do NOT wrap the code in markdown code fences.
3. Do NOT output any explanations or conversational text.

TECHNICAL REQUIREMENTS:
- Standard imports:
    from playwright.sync_api import Page, expect
- Structure tests as standalone pytest functions: `def test_<name>(page: Page):`
- Preferred locator strategy: use user-facing locators (`page.get_by_role`,
  `page.get_by_label`, `page.get_by_test_id`) over raw CSS/XPath when
  possible.
- Assertions: ALWAYS use auto-retrying web-first assertions via `expect()`
  (e.g., `expect(page.get_by_role("heading")).to_be_visible()`).
- Do NOT explicitly instantiate browsers or drivers inside tests; rely on
  the injected `page` fixture.
- Do NOT output Robot Framework, Selenium, or raw JavaScript syntax.
