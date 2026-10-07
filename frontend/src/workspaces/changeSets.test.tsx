import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ApplyResult, ChangeSetDetail } from "../api/changeSets";
import type { Me } from "../api/queries";
import type { Workspace } from "../api/workspaces";
import {
  CSRF_TOKEN,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const CHANGE_SET_ID = "99999999-9999-4999-8999-999999999999";
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;
const PATH = `/workspaces/${WORKSPACE_ID}/change-sets/${CHANGE_SET_ID}`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

function workspace(permissions: string[]): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role: "editor",
    permissions,
    status: "active",
    archived_at: null,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  } as Workspace;
}

const ITEM_BASE = {
  object_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  operation: "update" as const,
  depends_on: [],
  required_role: "editor" as const,
  is_conflict: false,
  status_reason: null,
};

function detail(): ChangeSetDetail {
  return {
    change_set: {
      id: CHANGE_SET_ID,
      workspace_id: WORKSPACE_ID,
      origin: "ai",
      scope: {},
      title: "Describe customers",
      conversation_id: null,
      created_by: ADA.id,
      status: "pending",
      created_at: "2026-01-05T10:00:00Z",
      applied_by: null,
      applied_at: null,
      item_counts: { pending: 2 },
    },
    items: [
      {
        ...ITEM_BASE,
        id: "i1",
        position: 0,
        object_type: "source_table",
        label: "core.customers",
        base_values: { description: null },
        payload: { description: "Bank customers" },
        status: "pending",
      },
      {
        ...ITEM_BASE,
        id: "i2",
        position: 1,
        object_type: "source_table",
        label: "core.accounts",
        base_values: { classification: null },
        payload: { classification: "master" },
        required_role: "owner",
        status: "pending",
      },
    ],
  };
}

function backend(
  permissions: string[],
  onDecide?: (r: ApiRequest) => Response,
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(workspace(permissions));
      case `GET ${API}/change-sets/${CHANGE_SET_ID}`:
        return json(detail());
      case `POST ${API}/change-sets/${CHANGE_SET_ID}/accept`:
      case `POST ${API}/change-sets/${CHANGE_SET_ID}/reject`:
        return onDecide?.(r);
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Change Set page", () => {
  it("shows each item as a diff with its role and status", async () => {
    renderApp(backend(["workspace.view"]), PATH);

    expect(await screen.findByText("core.customers")).toBeInTheDocument();
    expect(screen.getByText("Bank customers")).toBeInTheDocument();
    expect(screen.getByText("owner only")).toBeInTheDocument();
    // Viewers see the diff but get no way to decide.
    expect(
      screen.queryByRole("button", { name: "Accept all" }),
    ).not.toBeInTheDocument();
  });

  it("accepts the selected items and reports what was skipped or waits for an owner", async () => {
    const result: ApplyResult = {
      change_set: detail(),
      accepted: ["i1"],
      skipped: [
        {
          item_id: "i2",
          reason: "stale",
          detail: "Changed since it was proposed: classification.",
        },
      ],
      needs_owner: [],
    };
    const requests = renderApp(
      backend(["workspace.view", "change_set.review"], () => json(result)),
      PATH,
    );

    fireEvent.click(await screen.findByLabelText("Select core.customers"));
    fireEvent.click(screen.getByRole("button", { name: "Accept selected" }));

    expect(await screen.findByText("1 item applied.")).toBeInTheDocument();
    expect(
      screen.getByText(/core.accounts: Changed since it was proposed/),
    ).toBeInTheDocument();
    const post = requests.find((r) => r.method === "POST");
    expect(post?.path).toBe(`${API}/change-sets/${CHANGE_SET_ID}/accept`);
    expect(post?.body).toEqual({ item_ids: ["i1"] });
    expect(post?.csrf).toBe(CSRF_TOKEN);
  });

  it("rejects everything that is open", async () => {
    const requests = renderApp(
      backend(["workspace.view", "change_set.review"], () => json(detail())),
      PATH,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Reject all" }));

    await waitFor(() =>
      expect(requests.some((r) => r.path.endsWith("/reject"))).toBe(true),
    );
    expect(requests.find((r) => r.path.endsWith("/reject"))?.body).toEqual({
      item_ids: null,
    });
  });
});
