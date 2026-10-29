## Framework & Imports

Playwright Python | pytest-playwright | sync API

## Locators

page.get_by_role() > page.get_by_test_id() > page.locator()

## Assertions

expect(locator).to_be_visible() / to_have_text() / to_have_url()

## Structure

def test_name(page: Page): ...
