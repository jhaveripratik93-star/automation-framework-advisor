## Framework & Imports

Playwright TypeScript | @playwright/test | async/await

## Locators

page.getByRole() > page.getByTestId() > page.locator()

## Assertions

await expect(locator).toBeVisible() / toHaveText() / toHaveURL()

## Network

await page.route() / page.waitForResponse()

## Structure

test.describe('Suite', () => { test('case', async ({ page }) => { ... }) })
