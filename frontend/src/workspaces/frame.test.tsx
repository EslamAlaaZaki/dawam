import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { Workspace } from "../api/workspaces";
import { currentLocation } from "../test/navigation";
import {
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const ID = "11111111-1111-4111-8111-111111111111";
const OTHER_ID = "22222222-2222-4222-8222-222222222222";
const PATH = `/workspaces/${ID}`;
const API = `/api/v1/workspaces/${ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

function workspace(overrides: Partial<Workspace> = {}): Workspace {
  return {
    id: ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role: "editor",
    permissions: ["assistant.ask", "workspace.view"],
    status: "active",
    archived_at: null,
    require_distinct_approver: false,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
    ...overrides,
  };
}

function backend(current: Workspace = workspace()) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case "GET /api/v1/workspaces":
        return json({
          items: [current, workspace({ id: OTHER_ID, name: "Risk DW" })],
          next_cursor: null,
        });
      case `GET ${API}`:
        return json(current);
      case `GET ${API}/progress`:
        return json({
          source_analysis: [
            { system_id: "s1", name: "Core Banking", status: "complete" },
          ],
          kpis: { status: "in_progress" },
          dw_modeling: [{ layer: "staging", status: "not_started" }],
        });
      case `GET ${API}/jobs`:
        return json({ items: [], next_cursor: null });
      case `GET ${API}/assistant/conversations`:
        return json({ items: [] });
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Workspace frame", () => {
  it("puts the Workspace switcher and the folder tree in the sidebar", async () => {
    renderApp(backend(), `${PATH}?folder=dw`);
    // The Data Warehouse folder opens its setup dialog, which hides the page behind it.
    fireEvent.click(await screen.findByRole("button", { name: "Close" }));

    const sidebar = await screen.findByRole("complementary", {
      name: "Navigation",
    });
    const tree = await within(sidebar).findByRole("tree", {
      name: "Workspace folders",
    });
    expect(
      within(tree).getByRole("treeitem", { name: "Data Warehouse" }),
    ).toHaveAttribute("aria-selected", "true");

    fireEvent.click(
      within(sidebar).getByRole("button", { name: /Switch Workspace/ }),
    );
    const other = await screen.findByRole("link", { name: "Risk DW" });
    expect(other).toHaveAttribute("href", `/workspaces/${OTHER_ID}`);
    expect(screen.getByRole("link", { name: "Retail DW" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });

  it("selects a folder through the sidebar tree and the folder URL parameter", async () => {
    renderApp(backend(), PATH);

    const sidebar = await screen.findByRole("complementary", {
      name: "Navigation",
    });
    fireEvent.click(
      await within(sidebar).findByRole("treeitem", { name: "Systems" }),
    );

    expect(currentLocation()).toBe(`${PATH}?folder=systems`);
    expect(
      await screen.findByRole("heading", { name: "Systems" }),
    ).toBeInTheDocument();
  });

  it("shows the name, role and stage progress as a stepper in the page header", async () => {
    renderApp(backend(), PATH);

    expect(
      await screen.findByRole("heading", { name: "Retail DW" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Your role: Editor")).toBeInTheDocument();
    const stepper = await screen.findByRole("region", {
      name: "Stage progress",
    });
    const steps = within(stepper).getAllByRole("listitem");
    expect(steps.map((step) => step.textContent)).toEqual([
      "Source Analysis: Core BankingComplete",
      "KPIsIn progress",
      "DW Modeling: StagingNot started",
    ]);
    expect(steps[1]).toHaveAttribute("aria-current", "step");
  });

  it("shows the archived banner", async () => {
    renderApp(backend(workspace({ status: "archived" })), PATH);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "This Workspace is archived and read-only.",
    );
  });
});

describe("the Assistant panel", () => {
  it("opens and closes with its button and stays open across folders", async () => {
    renderApp(backend(), PATH);

    const toggle = await screen.findByRole("button", { name: "Assistant" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.queryByLabelText("Ask the assistant"),
    ).not.toBeInTheDocument();

    fireEvent.click(toggle);
    expect(
      await screen.findByLabelText("Ask the assistant"),
    ).toBeInTheDocument();
    expect(toggle).toHaveAttribute("aria-expanded", "true");

    const sidebar = screen.getByRole("complementary", { name: "Navigation" });
    fireEvent.click(
      await within(sidebar).findByRole("treeitem", { name: "Systems" }),
    );
    expect(
      await screen.findByRole("heading", { name: "Systems" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Ask the assistant")).toBeVisible();

    fireEvent.click(screen.getByRole("button", { name: "Close Assistant" }));
    expect(screen.getByLabelText("Ask the assistant")).not.toBeVisible();
    expect(toggle).toHaveAttribute("aria-expanded", "false");
  });

  it("moves focus in on open, and back to the button on Escape", async () => {
    renderApp(backend(), PATH);

    const toggle = await screen.findByRole("button", { name: "Assistant" });
    fireEvent.click(toggle);
    const input = await screen.findByLabelText("Ask the assistant");
    expect(input.closest("aside")).toHaveFocus();

    fireEvent.keyDown(input, { key: "Escape" });
    expect(input).not.toBeVisible();
    expect(toggle).toHaveFocus();
  });

  it("has no keyboard shortcut", async () => {
    renderApp(backend(), PATH);
    await screen.findByRole("button", { name: "Assistant" });

    for (const key of ["j", "k", "/"]) {
      fireEvent.keyDown(document.body, { key, ctrlKey: true });
      fireEvent.keyDown(document.body, { key, metaKey: true });
    }

    expect(
      screen.queryByLabelText("Ask the assistant"),
    ).not.toBeInTheDocument();
  });
});
