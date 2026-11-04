## Framework & Imports

Selenium Python | pytest | WebDriver

## Locators

By.CSS_SELECTOR > By.ID > By.XPATH

## Waits

WebDriverWait(driver, 10).until(EC.visibility_of_element_located(...))

## Structure

class TestSuite: def test_name(self, driver): ...
