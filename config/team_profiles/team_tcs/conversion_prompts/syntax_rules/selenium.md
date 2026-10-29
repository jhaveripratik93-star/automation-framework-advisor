## Output Rules (Strict)

1. Output ONLY valid Python code using pytest and Selenium WebDriver.
2. Do NOT wrap the output in markdown code fences.
3. Do NOT include explanatory text outside the code.

## Technical Requirements

- Imports:
    import pytest
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
- Test structure: plain pytest functions (e.g., `def test_<name>(driver):`).
- Element selection: ALWAYS use the `By` enum with explicit locators
  (e.g., `By.CSS_SELECTOR`, `By.ID`, `By.XPATH`).
- Explicit waits: favor `WebDriverWait(driver, timeout).until(...)` over
  raw `time.sleep()`.
- Do NOT output Playwright, K6, or Robot Framework syntax.
