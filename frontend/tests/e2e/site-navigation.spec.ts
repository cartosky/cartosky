import { expect, test } from "@playwright/test";

test.describe("marketing navigation", () => {
  test("treats viewer, forecast, and climate as peer sections on desktop", async ({ page }) => {
    await page.goto("/forecast");

    const header = page.locator("header");
    const viewer = header.getByRole("link", { name: "Viewer" });
    const forecast = header.getByRole("link", { name: "Forecast" });

    await expect(viewer).not.toHaveClass(/text-slate-950/);
    await expect(viewer).toHaveClass(/border-transparent/);
    await page.addStyleTag({ content: "header a { transition-duration: 0s !important; }" });
    await viewer.hover();
    await expect(viewer).toHaveCSS("border-bottom-color", "rgb(103, 232, 249)");
    await expect(forecast).toHaveAttribute("aria-current", "page");
    await expect(forecast).toHaveClass(/border-cyan-300/);
  });

  test("keeps feedback and account actions in a far-right utility group", async ({ page }) => {
    await page.goto("/forecast");

    const header = page.locator("header");
    const productNavigation = header.getByRole("navigation", { name: "Product navigation" });
    const accountUtilities = header.getByRole("group", { name: "Account utilities" });

    await expect(productNavigation).toContainText("Viewer");
    await expect(productNavigation).not.toContainText("Feedback");
    await expect(productNavigation).not.toContainText("Login");
    await expect(header.locator(".nav-utility-divider")).toHaveClass(/h-7/);
    await expect(header.locator(".nav-utility-divider")).toHaveCSS("margin-left", "4px");
    await expect(header.locator(".nav-utility-divider")).toHaveCSS("margin-right", "4px");
    await expect(accountUtilities.getByRole("button", { name: "Send feedback" })).toBeVisible();
    await expect(accountUtilities.getByRole("link", { name: "Login" })).toBeVisible();
  });

  test("keeps the mobile header focused on the menu until navigation is opened", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/forecast");

    const header = page.locator("header");
    await expect(header.getByRole("link", { name: "Viewer" })).toHaveCount(0);

    await header.getByRole("button", { name: "Open menu" }).click();

    const forecast = header.getByRole("link", { name: "Forecast" });
    await expect(forecast).toHaveAttribute("aria-current", "page");
    await expect(forecast).toHaveClass(/border-cyan-300/);
  });

  test("does not select a product section on the homepage", async ({ page }) => {
    await page.goto("/");

    const header = page.locator("header");
    await expect(header.getByRole("link", { name: "Viewer" })).not.toHaveAttribute("aria-current", "page");
    await expect(header.getByRole("link", { name: "Forecast" })).not.toHaveAttribute("aria-current", "page");
    await expect(header.getByRole("link", { name: "Climate" })).not.toHaveAttribute("aria-current", "page");
  });
});
