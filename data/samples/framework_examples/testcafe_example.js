// TestCafe — UI E2E test example
// Install: npm install testcafe
// Run:     npx testcafe chrome testcafe_example.js

import { Selector } from "testcafe";

fixture("Login").page("https://example.com/login");

test("logs in with valid credentials", async (t) => {
  await t
    .typeText(Selector("[data-testid='username']"), "admin")
    .typeText(Selector("[data-testid='password']"), "password123")
    .click(Selector("[data-testid='login-btn']"))
    .expect(Selector("h1").innerText).eql("Dashboard");
});
