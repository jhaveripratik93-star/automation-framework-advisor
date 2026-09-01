// Puppeteer — UI E2E test example
// Install: npm install puppeteer jest
// Run:     npx jest puppeteer_example.test.js

const puppeteer = require("puppeteer");

test("logs in with valid credentials", async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();

  await page.goto("https://example.com/login");
  await page.type("[data-testid='username']", "admin");
  await page.type("[data-testid='password']", "password123");
  await page.click("[data-testid='login-btn']");

  await page.waitForURL("**/dashboard");
  const heading = await page.$eval("h1", (el) => el.textContent);
  expect(heading).toBe("Dashboard");

  await browser.close();
});
