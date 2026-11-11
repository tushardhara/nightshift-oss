import { defineConfig } from "cypress";

// Dogfood B fixture (#443): a deliberately boring Cypress e2e config sitting
// at repo root next to a Maven/Spring Boot service. This file, on its own,
// is what nightshift_project_contract.py's e2e detector reads as
// source_kind "config_file" -- observed evidence, no CI step required.
export default defineConfig({
  e2e: {
    baseUrl: "http://localhost:8080",
    setupNodeEvents(on, config) {
      return config;
    },
  },
});
