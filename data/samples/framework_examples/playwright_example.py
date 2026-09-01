# Playwright (Python) — UI E2E test example
# Install: pip install playwright && playwright install
# Run:     pytest test_login.py

import pytest
from playwright.sync_api import Page, expect


def test_login_valid_credentials(page: Page):
    page.goto("https://example.com/login")

    page.fill("[data-testid='username']", "admin")
    page.fill("[data-testid='password']", "password123")
    page.click("[data-testid='login-btn']")

    expect(page.locator("h1")).to_have_text("Dashboard")
    expect(page).to_have_url("https://example.com/dashboard")
