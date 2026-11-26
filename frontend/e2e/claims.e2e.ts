import { expect, test, type Page } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const PASSWORD = process.env.DEMO_PASSWORD ?? "claimpilot-demo";
const HERE = path.dirname(fileURLToPath(import.meta.url));
const PHOTO = path.resolve(HERE, "../../data/photos/collision-rear-corner-minor.jpg");

async function login(page: Page, email: string) {
  await page.goto("/");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("button", { name: "Sign out" })).toBeVisible();
}

test("claimant files a claim, watches progress and gets the letter", async ({ page }) => {
  await login(page, "casey.fofana@example.com");
  await expect(page.getByText("Synthetic demo. Recommendations only")).toBeVisible();
  await page.getByRole("link", { name: "New claim" }).first().click();
  await page.getByLabel("Date of incident").fill("2026-03-04");
  await page.getByLabel("Where did it happen?").fill("Oak St and 5th Ave, Austin, TX");
  await page.getByLabel("What happened?").fill("Rear-ended at a red light; small dent in the rear bumper.");
  await page.getByLabel(/Photos of the damage/).setInputFiles(PHOTO);
  await page.getByRole("button", { name: "Submit claim" }).click();

  await expect(page).toHaveURL(/\/claims\/clm_/);
  await expect(page.getByText("Decision letter")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("closed", { exact: true })).toBeVisible();
  await expect(page.getByRole("img", { name: "Damage photo" })).toBeVisible();
  // claimant view never shows internal fraud details
  await expect(page.getByText("Fraud checks")).toHaveCount(0);
});

test("adjuster sees the claim detail, trace and impact dashboard", async ({ page }) => {
  await login(page, "adjuster@claimpilot.example");
  await page.getByRole("button", { name: "all", exact: true }).click();
  await page.getByRole("link", { name: /^clm_/ }).first().click();
  await expect(page.getByRole("heading", { name: "Coverage", exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Fraud checks" })).toBeVisible();
  await expect(page.getByRole("heading", { name: /Payout \(deterministic\)/ })).toBeVisible();

  await page.getByRole("button", { name: "trace" }).click();
  await expect(page.getByText("Active time")).toBeVisible();
  await expect(page.getByText(/LLM coverage_assess/)).toBeVisible();

  await page.getByRole("link", { name: "Impact" }).click();
  await expect(page.getByText("Straight-through rate")).toBeVisible();
  await expect(page.getByText(/stated assumption/)).toBeVisible();
});

test("claimant cannot open staff pages", async ({ page }) => {
  await login(page, "casey.fofana@example.com");
  await page.goto("/dashboard");
  await expect(page.getByRole("heading", { name: "My claims" })).toBeVisible();
});

test("wrong password is rejected", async ({ page }) => {
  await page.goto("/");
  await page.getByLabel("Email").fill("adjuster@claimpilot.example");
  await page.getByLabel("Password").fill("not-the-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByText("Wrong email or password")).toBeVisible();
});
