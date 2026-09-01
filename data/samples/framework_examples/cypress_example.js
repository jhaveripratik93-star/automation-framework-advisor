// Cypress — UI E2E test example
// Install: npm install cypress
// Run:     npx cypress run

describe("Login", () => {
  it("logs in with valid credentials", () => {
    cy.visit("https://example.com/login");

    cy.get("[data-testid='username']").type("admin");
    cy.get("[data-testid='password']").type("password123");
    cy.get("[data-testid='login-btn']").click();

    cy.url().should("include", "/dashboard");
    cy.get("h1").should("have.text", "Dashboard");
  });
});
