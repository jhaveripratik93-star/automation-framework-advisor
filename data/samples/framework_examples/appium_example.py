# Appium (Python) — Mobile UI test example
# Install: pip install Appium-Python-Client pytest
# Run:     pytest appium_example.py  (requires Appium server + device/emulator)

import pytest
from appium import webdriver
from appium.webdriver.common.appiumby import AppiumBy


@pytest.fixture
def driver():
    caps = {
        "platformName": "Android",
        "deviceName": "emulator-5554",
        "appPackage": "com.example.app",
        "appActivity": ".LoginActivity",
        "automationName": "UiAutomator2",
    }
    d = webdriver.Remote("http://localhost:4723/wd/hub", caps)
    yield d
    d.quit()


def test_login_valid_credentials(driver):
    driver.find_element(AppiumBy.ACCESSIBILITY_ID, "username_field").send_keys("admin")
    driver.find_element(AppiumBy.ACCESSIBILITY_ID, "password_field").send_keys("password123")
    driver.find_element(AppiumBy.ACCESSIBILITY_ID, "login_button").click()

    dashboard = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "dashboard_title")
    assert dashboard.text == "Dashboard"
