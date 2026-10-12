import { expect, test, type Page } from "@playwright/test";

// The core flows with the keyboard only (spec #231: WCAG AA, keyboard navigation):
// sign in, open a Workspace, browse its folders, review a Change Set, manage users.
// Nothing here clicks. It runs after smoke.spec.ts (alphabetical order, one worker) and
// uses the Workspace and the pending Change Set that journey leaves behind.

const ADMIN = { email: "admin@example.com", password: "e2e-admin-Passw0rd-2026" };
// A fresh address each run, so a rerun on the same stack still creates a user.
const EMAIL = `keyboard-${Date.now()}@example.com`;

/** What has keyboard focus: its tag, role, accessible-ish name and whether a ring is drawn. */
async function focused(page: Page) {
  return page.evaluate(() => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body) {
      return null;
    }
    // A tree item draws its ring on its label; everything else on itself.
    const target =
      el.getAttribute("role") === "treeitem"
        ? (el.querySelector(".folder-label") as HTMLElement)
        : el;
    const style = getComputedStyle(target);
    return {
      role: el.getAttribute("role") ?? el.tagName.toLowerCase(),
      name: (el.getAttribute("aria-label") ?? el.textContent ?? "").trim(),
      ring: style.outlineStyle !== "none" && parseFloat(style.outlineWidth) >= 2,
    };
  });
}

/** Tab until focus lands on an element whose name matches, checking every stop shows a ring. */
async function tabTo(page: Page, name: RegExp | string, role?: string, max = 60) {
  for (let i = 0; i < max; i += 1) {
    await page.keyboard.press("Tab");
    const now = await focused(page);
    if (!now) {
      continue;
    }
    // Every stop on the way shows a ring, not just the one aimed for (the skip link has a shadow).
    expect(now.ring || now.name === "Skip to the page", `focus ring on ${now.role} "${now.name.slice(0, 40)}"`).toBe(true);
    const named = typeof name === "string" ? now.name.startsWith(name) : name.test(now.name);
    if (named && (!role || now.role === role)) {
      return now;
    }
  }
  throw new Error(`Tab never reached ${name} within ${max} stops`);
}

test("the core flows by keyboard alone", async ({ page }) => {
  await test.step("sign in", async () => {
    await page.goto("/login");
    await tabTo(page, "", "input");
    await page.keyboard.type(ADMIN.email);
    // The password field follows the "Forgot your password?" link.
    await tabTo(page, "", "input");
    await page.keyboard.type(ADMIN.password);
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Workspaces" })).toBeVisible();
  });

  await test.step("the first Tab on a signed-in page is the skip link, which reaches the page", async () => {
    await page.keyboard.press("Tab");
    expect((await focused(page))?.name).toBe("Skip to the page");
    await page.keyboard.press("Enter");
    await expect(page.locator("main")).toBeFocused();
  });

  await test.step("open a Workspace", async () => {
    await tabTo(page, "Smoke Shop", "a");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "Smoke Shop" })).toBeVisible();
    await page.waitForLoadState("networkidle");
  });

  await test.step("browse folders in the tree with the arrow keys", async () => {
    await tabTo(page, "", "treeitem");
    await page.keyboard.press("ArrowDown"); // Systems
    await page.keyboard.press("ArrowDown"); // the Source System
    await page.keyboard.press("ArrowRight"); // open it
    await page.keyboard.press("ArrowDown"); // its first folder
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/folder=systems%2F/);
    // Focus stays in the tree and still shows where it is.
    const now = await focused(page);
    expect(now?.role).toBe("treeitem");
    expect(now?.ring).toBe(true);
  });

  await test.step("the Assistant opens, takes focus, and gives it back on Escape", async () => {
    await tabTo(page, "Assistant", "button");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("region", { name: "Assistant" })).toBeVisible();
    await expect(page.locator("#assistant-panel")).toBeFocused();
    await page.keyboard.press("Escape");
    await expect(page.locator("#assistant-panel")).toBeHidden();
    await expect(page.getByRole("button", { name: "Assistant", exact: true })).toBeFocused();
  });

  await test.step("review a Change Set: items and decisions are all reachable", async () => {
    const workspaceId = new URL(page.url()).pathname.split("/").pop() ?? "";
    const sets = await page.request.get(`/api/v1/workspaces/${workspaceId}/change-sets`);
    const pending = (await sets.json()).items.find((c: { status: string }) => c.status === "pending");
    expect(pending, "smoke.spec.ts leaves a pending Change Set").toBeTruthy();

    await page.goto(`/workspaces/${workspaceId}/change-sets/${pending.id}`);
    await expect(page.getByRole("button", { name: "Accept all" })).toBeVisible();
    const item = await tabTo(page, /^Select /, "input");
    await page.keyboard.press("Space");
    await expect(page.getByRole("checkbox", { name: item.name })).toBeChecked();
    await tabTo(page, "Accept selected", "button");
    await tabTo(page, "Accept all", "button");
    await tabTo(page, "Reject all", "button");
  });

  await test.step("manage users: create one, find it, reach its actions", async () => {
    await page.goto("/admin/users");
    await expect(page.getByRole("heading", { name: "Users" })).toBeVisible();
    await tabTo(page, "Create user", "a");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/admin\/users\/new$/);

    await tabTo(page, "", "input");
    await page.keyboard.type(EMAIL);
    await page.keyboard.press("Tab");
    await page.keyboard.type("Kay Board");
    await page.keyboard.press("Tab"); // role select, left on User
    await page.keyboard.press("Tab");
    await page.keyboard.type("keyboard-Passw0rd-2026");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/admin\/users$/);
    await expect(page.getByText(`${EMAIL} created.`)).toBeVisible();

    await tabTo(page, "Search", "button");
    await tabTo(page, "Make admin", "button");
    await tabTo(page, "Deactivate", "button");
  });
});
