import { expect, test, type Page } from "@playwright/test";

const email = `e2e-${Date.now()}@example.com`;
const password = "e2e correct horse battery";

async function register(page: Page) {
  await page.goto("/register");
  await page.getByLabel("Display name").fill("E2E Tester");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test.describe.configure({ mode: "serial" });

test("register, see honest capabilities, switch to Arabic RTL", async ({ page }) => {
  await register(page);
  await expect(page.getByRole("heading", { name: /Welcome back, E2E Tester/ })).toBeVisible();
  await expect(page.getByText("Feature availability")).toBeVisible();
  await expect(page.getByText("not implemented").first()).toBeVisible();
  await page.getByRole("button", { name: "Language" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
  await expect(page.getByRole("link", { name: "محادثة مايند" })).toBeVisible();
  await page.getByRole("button", { name: "اللغة" }).click();
  await expect(page.locator("html")).toHaveAttribute("dir", "ltr");
});

test("login, create a project, chat says no model is configured", async ({ page }) => {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
  await page.goto("/projects");
  await page.getByRole("button", { name: "New project" }).click();
  await page.getByLabel("Project name").fill("Robot arm");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page.getByRole("heading", { name: "Robot arm" })).toBeVisible();
  await page.goto("/chat");
  await expect(page.getByText("No chat model is enabled yet")).toBeVisible();
});

async function login(page: Page) {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).toHaveURL(/\/dashboard/);
}

test("generate a validated Arduino UNO holder STL and view it in 3D", async ({ page }) => {
  await login(page);
  await page.goto("/3d");
  await page.getByRole("button", { name: "Generate part" }).click();
  await expect(page.getByText("print ready checks passed").first()).toBeVisible({ timeout: 120_000 });
  await expect(page.locator("canvas")).toBeVisible();
  const stlLink = page.getByRole("link", { name: "Download" }).first();
  const href = await stlLink.getAttribute("href");
  const res = await page.request.get(href!);
  expect(res.status()).toBe(200);
  const body = await res.body();
  expect(body.length).toBeGreaterThan(1000);
  await expect(page.getByText("Bill of materials")).toBeVisible();
  await page.screenshot({ path: "screenshots/3d-studio.png", fullPage: true });
});

test("generate a validated DOCX from the block editor", async ({ page }) => {
  await login(page);
  await page.goto("/documents");
  await page.getByRole("tab", { name: "Block editor" }).click();
  await page.getByRole("button", { name: "Generate file" }).click();
  await expect(page.getByText("passed", { exact: true }).first()).toBeVisible({ timeout: 90_000 });
  const link = page.locator("a[download]").first();
  const res = await page.request.get((await link.getAttribute("href"))!);
  expect(res.status()).toBe(200);
  expect((await res.body()).subarray(0, 2).toString()).toBe("PK");
});

test("builder: create an app, run tests in the sandbox and open the live preview", async ({ page }) => {
  await login(page);
  await page.goto("/builder");
  await page.getByRole("button", { name: "New app" }).click();
  await page.getByLabel("Name").fill("E2E Static App");
  await page.getByRole("button", { name: "Create" }).click();
  await expect(page).toHaveURL(/\/builder\/[0-9a-f-]+/);
  await page.getByRole("button", { name: "Run tests" }).click();
  await expect(page.getByText(/PASSED/)).toBeVisible({ timeout: 120_000 });
  await page.getByRole("button", { name: "Preview" }).click();
  const frame = page.frameLocator('iframe[title="Live preview"]');
  await expect(frame.getByRole("heading", { name: "My MIND App" })).toBeVisible({ timeout: 60_000 });
  await page.screenshot({ path: "screenshots/builder.png", fullPage: true });
  await page.getByRole("button", { name: "Stop" }).click();
});
