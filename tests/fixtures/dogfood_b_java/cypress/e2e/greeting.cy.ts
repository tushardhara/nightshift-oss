describe("greeting endpoint", () => {
  it("renders the greeting", () => {
    cy.request("/greeting").its("body").should("include", "hello from dogfood b");
  });
});
