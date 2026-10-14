import { expect, test, type Page } from "@playwright/test";

// The main path through DAWAM, in a browser, against the full Compose stack with the fake
// LLM provider and the seeded PostgreSQL source (compose.e2e.yaml):
//   sign in -> create Workspace -> add member -> extract -> generate staging ->
//   generate core -> map -> view lineage.
// Steps that have no screen yet (the admin's model setup, staging generation) go through
// the API with the browser's own session.

const ADMIN = { email: "admin@example.com", password: "e2e-admin-Passw0rd-2026" };
const MEMBER = { email: "member@example.com", password: "e2e-member-Passw0rd-2026" };
const STAGING_CUSTOMERS = "stg_shop_sales_customers";

/** A state-changing call as the signed-in browser: its cookies plus the CSRF header. */
async function api(page: Page, method: "GET" | "POST" | "PUT", path: string, data?: unknown) {
  const cookies = await page.context().cookies();
  const csrf = cookies.find((c) => c.name === "dawam_csrf")?.value ?? "";
  const response = await page.request.fetch(`/api/v1${path}`, {
    method,
    data,
    headers: { "X-CSRF-Token": csrf },
  });
  expect(response.ok(), `${method} ${path}: ${await response.text()}`).toBeTruthy();
  return response.status() === 204 ? null : response.json();
}

test("the main path: sign in to lineage", async ({ page }) => {
  let workspaceId = "";

  await test.step("sign in as the first admin", async () => {
    await page.goto("/login");
    await page.getByLabel("Email").fill(ADMIN.email);
    await page.getByLabel("Password").fill(ADMIN.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page.getByRole("heading", { name: "Workspaces" })).toBeVisible();
  });

  await test.step("set up the fake model and a colleague (admin tasks with no screen here)", async () => {
    const provider = await api(page, "POST", "/admin/llm/providers", {
      name: "Fake",
      base_url: "http://llm.test/v1",
      internal: true,
    });
    const model = await api(page, "POST", `/admin/llm/providers/${provider.id}/models`, {
      name: "fake-model",
    });
    await api(page, "PUT", `/admin/llm/models/${model.id}/roles`, { roles: ["agent"] });
    const tested = await api(page, "POST", `/admin/llm/models/${model.id}/test`);
    expect(tested.test_ok).toBe(true);
    await api(page, "POST", "/admin/users", {
      email: MEMBER.email,
      display_name: "Mona Member",
      temporary_password: MEMBER.password,
    });
  });

  await test.step("create a Workspace", async () => {
    await page.goto("/");
    await page.getByRole("button", { name: "New workspace" }).first().click();
    const dialog = page.getByRole("dialog", { name: "New workspace" });
    await dialog.getByLabel("Name").fill("Smoke Shop");
    await dialog.getByLabel("Business domain").fill("Retail");
    // The only agent model is pre-selected as the Workspace's default.
    await expect(dialog.getByLabel("Default agent model")).toHaveValue(/[0-9a-f-]{36}/);
    await dialog.getByRole("button", { name: "Create Workspace" }).click();
    await page.waitForURL(/\/workspaces\/[0-9a-f-]{36}$/);
    workspaceId = page.url().split("/").pop() ?? "";
    await expect(page.getByRole("heading", { name: "Smoke Shop" })).toBeVisible();
    await expect(page.getByText("Your role: Owner")).toBeVisible();
  });

  await test.step("add a member", async () => {
    await page.getByRole("button", { name: "Add member" }).click();
    const dialog = page.getByRole("dialog", { name: "Add member" });
    await dialog.getByLabel("Email").fill(MEMBER.email);
    await dialog.getByRole("button", { name: "Add member" }).click();
    await expect(page.getByText("Mona Member is now")).toBeVisible();
    await expect(page.getByRole("row", { name: "Mona Member" })).toBeVisible();
  });

  await test.step("set up the Data Warehouse", async () => {
    await page.goto(`/workspaces/${workspaceId}?folder=dw/setup`);
    await page.getByRole("button", { name: "Set up Data Warehouse" }).click();
    await expect(page.getByRole("heading", { name: "Data Warehouse setup" })).toBeVisible();
  });

  await test.step("add the Source System and its Connection to the seeded database", async () => {
    await page.goto(`/workspaces/${workspaceId}?folder=systems`);
    await page.getByRole("button", { name: "Add system" }).click();
    const dialog = page.getByRole("dialog", { name: "Add Source System" });
    await dialog.getByLabel("Name").fill("Shop");
    await dialog.getByLabel("System Code").fill("shop");
    await dialog.getByRole("button", { name: "Add Source System" }).click();
    await page.waitForURL(/folder=systems%2F/);
    const systemFolder = new URL(page.url()).searchParams.get("folder");

    await page.goto(`/workspaces/${workspaceId}?folder=${systemFolder}/connection`);
    const connection = page.getByRole("form", { name: "Connection" });
    await connection.getByLabel("Host").fill("source-db");
    await connection.getByLabel("Database", { exact: true }).fill("shop");
    await connection.getByLabel("Username").fill("reader");
    await connection.getByLabel("Password").fill("reader");
    await connection.getByLabel("Allowed Database Schemas").fill("sales");
    await connection.getByRole("button", { name: "Test connection" }).click();
    await expect(page.getByText(/Connection works/)).toBeVisible();
    await connection.getByRole("button", { name: "Save Connection" }).click();
    await expect(page.getByText("Connection saved.")).toBeVisible();

    await test.step("extract its metadata", async () => {
      await page.goto(`/workspaces/${workspaceId}?folder=${systemFolder}/source-schema`);
      await page.getByRole("button", { name: "Extract metadata" }).click();
      // A background job on the worker; the Snapshots list refreshes when it finishes.
      await expect(page.getByRole("list", { name: "Snapshots" })).toContainText(
        "2 tables and views",
        { timeout: 90_000 },
      );
    });
  });

  await test.step("generate the staging layer", async () => {
    // No screen starts this yet: the API does, as the same signed-in owner.
    await api(page, "POST", `/workspaces/${workspaceId}/data-warehouse/staging/generate`);
    await page.goto(`/workspaces/${workspaceId}?folder=dw/staging/model`);
    await expect(page.getByRole("button", { name: STAGING_CUSTOMERS })).toBeVisible();
    await expect(page.getByRole("button", { name: "stg_shop_sales_orders" })).toBeVisible();
  });

  await test.step("generate the core layer with the assistant and accept the Change Set", async () => {
    await page.goto(`/workspaces/${workspaceId}`);
    // The Assistant panel starts closed; its header button opens it.
    await page.getByRole("button", { name: "Assistant", exact: true }).click();
    await page.getByLabel("Ask the assistant").fill("Generate the core layer");
    await page.getByRole("button", { name: "Send" }).click();

    // The chat hands over to a job; it ends in one pending Change Set.
    let changeSetId = "";
    await expect
      .poll(
        async () => {
          const sets = await api(page, "GET", `/workspaces/${workspaceId}/change-sets`);
          changeSetId =
            sets.items.find(
              (c: { status: string; scope?: { kind?: string } }) =>
                c.status === "pending" && c.scope?.kind === "dw_generation",
            )?.id ?? "";
          return changeSetId;
        },
        { timeout: 120_000, intervals: [2_000] },
      )
      .not.toBe("");

    await page.goto(`/workspaces/${workspaceId}/change-sets/${changeSetId}`);
    // Wait for the accept call itself: leaving the page would cancel it.
    const accepted = page.waitForResponse(
      (r) =>
        r.url().endsWith(`/change-sets/${changeSetId}/accept`) && r.request().method() === "POST",
    );
    await page.getByRole("button", { name: "Accept all" }).click();
    expect((await accepted).ok()).toBeTruthy();

    await page.goto(`/workspaces/${workspaceId}?folder=dw/core/model`);
    await expect(page.getByRole("button", { name: "dim_customer" })).toBeVisible();
    await expect(page.getByRole("button", { name: "fact_sales" })).toBeVisible();
  });

  await test.step("map a core column to staging", async () => {
    await page.getByRole("button", { name: "dim_customer" }).click();
    await page.getByRole("button", { name: "Mappings", exact: true }).click();
    await page.getByLabel("Type of name", { exact: true }).selectOption("direct");
    await page.getByLabel("SQL for name", { exact: true }).fill(`${STAGING_CUSTOMERS}.name`);
    await page.getByRole("button", { name: "Save name", exact: true }).click();
    // The inputs cell is derived by the server from the saved SQL.
    await expect(
      page.locator("td").filter({ hasText: `${STAGING_CUSTOMERS}.name` }),
    ).toBeVisible();
  });

  await test.step("view the lineage of the mapped column", async () => {
    const tables = await api(page, "GET", `/workspaces/${workspaceId}/data-warehouse/tables`);
    const dim = tables.items.find((t: { name: string }) => t.name === "dim_customer");
    const detail = await api(page, "GET", `/workspaces/${workspaceId}/data-warehouse/tables/${dim.id}`);
    const column = detail.columns.find((c: { name: string }) => c.name === "name");

    await page.goto(`/workspaces/${workspaceId}?folder=dw/lineage&node=${column.id}`);
    await expect(page.getByRole("heading", { name: "Lineage", exact: true })).toBeVisible();
    await page.getByText("Edges as a list").click();
    await expect(page.getByRole("list", { name: "Lineage edges" })).toContainText(
      `${STAGING_CUSTOMERS}.name`,
    );
  });
});
