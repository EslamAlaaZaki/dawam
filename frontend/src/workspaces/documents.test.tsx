import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { WorkspaceFile } from "../api/files";
import type { Me } from "../api/queries";
import type { SourceSystem } from "../api/systems";
import type { Workspace, WorkspaceRole } from "../api/workspaces";
import {
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const SYSTEM_ID = "33333333-3333-4333-8333-333333333333";
const FOLDER = `/workspaces/${WORKSPACE_ID}?folder=systems/${SYSTEM_ID}/documents`;
const FILES = `/api/v1/workspaces/${WORKSPACE_ID}/systems/${SYSTEM_ID}/files`;

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

function workspace(role: WorkspaceRole): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role,
    permissions: PERMISSIONS[role],
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const CBS: SourceSystem = {
  id: SYSTEM_ID,
  workspace_id: WORKSPACE_ID,
  name: "Core Banking",
  code: "cbs",
  description: "",
  business_owner: "",
  technical_owner: "",
  version: 1,
  created_at: "2026-01-05T09:00:00Z",
  updated_at: "2026-01-05T09:00:00Z",
};

const SAD: WorkspaceFile = {
  id: "44444444-4444-4444-8444-444444444444",
  workspace_id: WORKSPACE_ID,
  owner_kind: "source_system",
  owner_id: SYSTEM_ID,
  name: "sad.pdf",
  kind: "uploaded",
  mime: "application/pdf",
  size: 2048,
  text_status: "no_text_found",
  updated_by: ADA.id,
  updated_at: "2026-01-05T09:00:00Z",
};

function backend(
  role: WorkspaceRole,
  files: WorkspaceFile[],
  handle: (r: ApiRequest) => Response | undefined = () => undefined,
) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET /api/v1/workspaces/${WORKSPACE_ID}`:
        return json(workspace(role));
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/progress`:
        return json({ source_analysis: [], kpis: { status: "not_started" }, dw_modeling: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/members`:
        return json({ items: [] });
      case `GET /api/v1/workspaces/${WORKSPACE_ID}/systems`:
        return json({ items: [CBS], next_cursor: null });
      case `GET ${FILES}`:
        return json({ items: files, next_cursor: null });
      default:
        return handle(r);
    }
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("a Source System's Documents folder", () => {
  it("lists documents with a download link; a viewer cannot upload", async () => {
    renderApp(backend("viewer", [SAD]), FOLDER);

    const link = await screen.findByRole("link", { name: "sad.pdf" });
    expect(link).toHaveAttribute(
      "href",
      `/api/v1/workspaces/${WORKSPACE_ID}/files/${SAD.id}/download`,
    );
    const row = screen.getByRole("row", { name: /sad\.pdf/ });
    expect(row).toHaveTextContent("2.0 KB");
    expect(row).toHaveTextContent("No text found");
    expect(screen.queryByRole("form", { name: "Upload document" })).not.toBeInTheDocument();
    expect(screen.getByText("Only owners and editors can upload documents.")).toBeInTheDocument();
  });

  it("lets an editor upload a document", async () => {
    const files: WorkspaceFile[] = [];
    const requests = renderApp(
      backend("editor", files, (r) => {
        if (r.method === "POST" && r.path === FILES) {
          files.push(SAD);
          return json(SAD, 201);
        }
      }),
      FOLDER,
    );

    const form = await screen.findByRole("form", { name: "Upload document" });
    const input = within(form).getByLabelText("File");
    fireEvent.change(input, {
      target: { files: [new File(["%PDF-1.4"], "sad.pdf", { type: "application/pdf" })] },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Upload" }));

    expect(await screen.findByRole("link", { name: "sad.pdf" })).toBeInTheDocument();
    const post = requests.find((r) => r.method === "POST" && r.path === FILES);
    expect(post?.csrf).toBeTruthy();
    expect(String(post?.body)).toContain("sad.pdf");
  });

  it("shows why an upload is refused", async () => {
    renderApp(
      backend("editor", [], (r) =>
        r.method === "POST" && r.path === FILES
          ? apiError(415, "unsupported_file_type", "That file type is not allowed.")
          : undefined,
      ),
      FOLDER,
    );

    const form = await screen.findByRole("form", { name: "Upload document" });
    fireEvent.change(within(form).getByLabelText("File"), {
      target: { files: [new File(["x"], "run.exe")] },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Upload" }));

    await waitFor(() =>
      expect(within(form).getByRole("alert")).toHaveTextContent("That file type is not allowed."),
    );
  });
});
