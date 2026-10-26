import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { FileIndexStatus } from "../api/files";
import type { Me } from "../api/queries";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import {
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=document-index`;
const BASE = `/api/v1/workspaces/${WORKSPACE_ID}/documents`;
const OK_ID = "44444444-4444-4444-8444-444444444444";
const BAD_ID = "55555555-5555-4555-8555-555555555555";

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

const PERMISSIONS: Record<WorkspaceRole, Workspace["permissions"]> = {
  owner: ["file.upload", "workspace.view"],
  editor: ["file.upload", "workspace.view"],
  viewer: ["workspace.view"],
};

const ROWS: FileIndexStatus[] = [
  {
    file_id: OK_ID,
    name: "sad.md",
    index_status: "indexed",
    index_error: null,
    index_note: null,
    chunk_count: 4,
    embedded_count: 3,
    stale_count: 2,
    updated_at: "2026-01-05T09:00:00Z",
  },
  {
    file_id: BAD_ID,
    name: "broken.pdf",
    index_status: "failed",
    index_error: "parser exploded",
    index_note: null,
    chunk_count: 0,
    embedded_count: 0,
    stale_count: 0,
    updated_at: "2026-01-05T09:00:00Z",
  },
];

function backend(role: WorkspaceRole) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json({
          id: WORKSPACE_ID,
          name: "Retail DW",
          description: "",
          domain: "",
          role,
          permissions: PERMISSIONS[role],
          status: "active",
          archived_at: null,
          require_distinct_approver: false,
          version: 1,
          created_at: "2026-01-05T09:00:00Z",
          updated_at: "2026-01-05T09:00:00Z",
        });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [], next_cursor: null });
      case `GET ${BASE}/index-status`:
        return json({ items: ROWS });
      case `POST ${BASE}/${BAD_ID}/reindex`:
      case `POST ${BASE}/reindex`:
        return json({}, 202);
    }
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the document index page", () => {
  it("shows each file's status, passages, coverage, stale vectors and error", async () => {
    renderApp(backend("viewer"), FOLDER);

    const ok = await screen.findByRole("row", { name: /sad\.md/ });
    expect(ok).toHaveTextContent("Indexed");
    expect(ok).toHaveTextContent("3 of 4 (75%)");
    expect(ok).toHaveTextContent("2 stale");
    const failed = screen.getByRole("row", { name: /broken\.pdf/ });
    expect(failed).toHaveTextContent("Failed");
    expect(failed).toHaveTextContent("parser exploded");
  });

  it("gives a viewer no re-index buttons", async () => {
    renderApp(backend("viewer"), FOLDER);

    await screen.findByRole("row", { name: /sad\.md/ });
    expect(
      screen.queryByRole("button", { name: /Re-index/ }),
    ).not.toBeInTheDocument();
  });

  it("lets an editor re-index one file or all", async () => {
    const requests = renderApp(backend("editor"), FOLDER);

    fireEvent.click(
      await screen.findByRole("button", { name: "Re-index broken.pdf" }),
    );
    await screen.findByRole("button", { name: "Re-index all" });
    fireEvent.click(screen.getByRole("button", { name: "Re-index all" }));

    await waitFor(() => {
      const posts = requests.filter((r) => r.method === "POST");
      expect(posts.map((r) => r.path)).toEqual([
        `${BASE}/${BAD_ID}/reindex`,
        `${BASE}/reindex`,
      ]);
      expect(posts.every((r) => r.csrf)).toBe(true);
    });
  });
});
