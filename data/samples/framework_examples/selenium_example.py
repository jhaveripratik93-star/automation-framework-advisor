# Selenium (Python) — UI E2E test example
# Install: pip install selenium pytest
# Run:     pytest test_login.py

import pytest
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


@pytest.fixture
def driver():
    d = webdriver.Chrome()
    yield d
    d.quit()


def test_login_valid_credentials(driver):
    driver.get("https://example.com/login")

    driver.find_element(By.CSS_SELECTOR, "[data-testid='username']").send_keys("admin")
    driver.find_element(By.CSS_SELECTOR, "[data-testid='password']").send_keys("password123")
    driver.find_element(By.CSS_SELECTOR, "[data-testid='login-btn']").click()

    WebDriverWait(driver, 10).until(EC.url_contains("/dashboard"))
    assert "Dashboard" in driver.find_element(By.TAG_NAME, "h1").text
