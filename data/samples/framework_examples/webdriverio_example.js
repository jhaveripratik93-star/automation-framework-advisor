// WebdriverIO — UI E2E test example
// Install: npm init wdio@latest
// Run:     npx wdio run wdio.conf.js

describe("Login", () => {
  it("logs in with valid credentials", async () => {
    await browser.url("https://example.com/login");

    await $("[data-testid='username']").setValue("admin");
    await $("[data-testid='password']").setValue("password123");
    await $("[data-testid='login-btn']").click();

    await expect(browser).toHaveUrlContaining("/dashboard");
    await expect($("h1")).toHaveText("Dashboard");
  });
});
