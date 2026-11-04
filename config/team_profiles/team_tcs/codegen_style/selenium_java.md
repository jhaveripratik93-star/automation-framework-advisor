## Framework & Imports

Selenium Java | TestNG | WebDriver

## Locators

By.cssSelector() > By.id() > By.xpath()

## Waits

new WebDriverWait(driver, Duration.ofSeconds(10)).until(ExpectedConditions...)

## Structure

public class TestName extends BaseTest { @Test public void testMethod() {} }
