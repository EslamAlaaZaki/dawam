import { fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { InstallationDocumentAi, KnobInfo } from "../api/documentAi";
import type { Me } from "../api/queries";
import {
  apiError,
  json,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const ROOT: Me = {
  id: "0b6c1f0e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "root@example.com",
  display_name: "Root Admin",
  system_role: "admin",
  must_change_password: false,
};

const PATH = "/api/v1/admin/llm/document-ai";

function knob(
  over: Partial<KnobInfo> & Pick<KnobInfo, "key" | "label">,
): KnobInfo {
  return {
    group: "retrieval",
    kind: "int",
    description: `What ${over.label} does.`,
    owner: false,
    reindex: false,
    default: 1,
    min: 1,
    max: 1000,
    choices: [],
    ...over,
  };
}

const SETTINGS: InstallationDocumentAi = {
  version: 3,
  updated_at: null,
  values: { chunking_strategy: "headings", top_k: 10, rrf_k: 60, rerank: true },
  bounds: { top_k: { min: 1, max: 50 } },
  knobs: [
    knob({
      key: "chunking_strategy",
      label: "Chunking strategy",
      group: "chunking",
      kind: "choice",
      owner: true,
      reindex: true,
      default: "headings",
      min: null,
      max: null,
      choices: ["headings", "recursive", "semantic"],
    }),
    knob({ key: "top_k", label: "Results", owner: true, default: 10, max: 50 }),
    knob({ key: "rrf_k", label: "RRF constant", default: 60 }),
    knob({
      key: "rerank",
      label: "Rerank results",
      group: "reranking",
      kind: "bool",
      owner: true,
      default: true,
      min: null,
      max: null,
    }),
  ],
};

function api(extra?: (r: ApiRequest) => Response | undefined) {
  return (r: ApiRequest) => {
    if (r.path === "/api/v1/me") return json(ROOT);
    if (r.method === "GET" && r.path === PATH) return json(SETTINGS);
    return extra?.(r);
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("the Document AI settings page", () => {
  it("is linked from the admin sidebar and groups every knob", async () => {
    renderApp(api(), "/admin/llm/settings");

    const nav = await screen.findByRole("navigation", {
      name: "Administration",
    });
    fireEvent.click(within(nav).getByRole("link", { name: "Document AI" }));

    expect(
      await screen.findByRole("heading", { name: "Document AI" }),
    ).toBeInTheDocument();
    expect(await screen.findByLabelText("Chunking strategy")).toHaveValue(
      "headings",
    );

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Retrieval" }));
    const retrieval = await screen.findByRole("region", { name: "Retrieval" });
    expect(within(retrieval).getByLabelText("Results")).toHaveValue(10);
    expect(within(retrieval).getByLabelText("RRF constant")).toHaveValue(60);
    expect(within(retrieval).getByText("Admin only")).toBeInTheDocument();
    expect(
      within(retrieval).getByLabelText("Results: owners' maximum"),
    ).toHaveValue(50);
    expect(
      within(retrieval).queryByLabelText("RRF constant: owners' maximum"),
    ).not.toBeInTheDocument();

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Reranking" }));
    expect(
      await screen.findByRole("switch", { name: "Rerank results" }),
    ).toBeChecked();
  });

  it("saves the values and bounds with the version it read", async () => {
    const requests = renderApp(
      api((r) =>
        r.method === "PUT" && r.path === PATH
          ? json({ ...SETTINGS, version: 4 })
          : undefined,
      ),
      "/admin/llm/document-ai",
    );

    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Retrieval" }));
    fireEvent.change(await screen.findByLabelText("RRF constant"), {
      target: { value: "80" },
    });
    fireEvent.change(screen.getByLabelText("Results: owners' maximum"), {
      target: { value: "20" },
    });
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Reranking" }));
    fireEvent.click(
      await screen.findByRole("switch", { name: "Rerank results" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(
      await screen.findByText("Document AI settings saved."),
    ).toBeInTheDocument();
    expect(requests.find((r) => r.method === "PUT")?.body).toEqual({
      version: 3,
      values: {
        chunking_strategy: "headings",
        top_k: 10,
        rrf_k: 80,
        rerank: false,
      },
      bounds: { top_k: { min: 1, max: 20 } },
    });
  });

  async function saveStrategy() {
    const REINDEX = `${PATH}/reindex`;
    const requests = renderApp(
      api((r) => {
        if (r.method === "PUT" && r.path === PATH) {
          return json({
            ...SETTINGS,
            version: 4,
            values: { ...SETTINGS.values, chunking_strategy: "recursive" },
          });
        }
        if (r.method === "POST" && r.path === REINDEX) {
          return json({ job_ids: ["j1"] }, 202);
        }
        return undefined;
      }),
      "/admin/llm/document-ai",
    );
    fireEvent.change(await screen.findByLabelText("Chunking strategy"), {
      target: { value: "recursive" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await screen.findByText(/Re-index every Workspace's documents/);
    return { requests, REINDEX };
  }

  it("queues a re-index of every Workspace when the admin accepts", async () => {
    const { requests, REINDEX } = await saveStrategy();
    fireEvent.click(screen.getByRole("button", { name: "Re-index now" }));
    expect(
      await screen.findByText("Re-indexing every Workspace's documents."),
    ).toBeInTheDocument();
    expect(requests.filter((r) => r.path === REINDEX)).toHaveLength(1);
  });

  it("leaves the index as is when the admin declines", async () => {
    const { requests, REINDEX } = await saveStrategy();
    fireEvent.click(screen.getByRole("button", { name: "Not now" }));
    expect(
      screen.queryByText(/Re-index every Workspace's documents/),
    ).not.toBeInTheDocument();
    expect(requests.some((r) => r.path === REINDEX)).toBe(false);
  });

  it("does not ask when no indexing knob changed", async () => {
    renderApp(
      api((r) =>
        r.method === "PUT" && r.path === PATH
          ? json({ ...SETTINGS, version: 4 })
          : undefined,
      ),
      "/admin/llm/document-ai",
    );
    fireEvent.mouseDown(await screen.findByRole("tab", { name: "Reranking" }));
    fireEvent.click(
      await screen.findByRole("switch", { name: "Rerank results" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Save settings" }));
    await screen.findByText("Document AI settings saved.");
    expect(
      screen.queryByRole("button", { name: "Re-index now" }),
    ).not.toBeInTheDocument();
  });

  it("shows why a change was refused", async () => {
    renderApp(
      api((r) =>
        r.method === "PUT"
          ? apiError(
              409,
              "settings_changed",
              "Someone changed these settings meanwhile. Reload them and try again.",
            )
          : undefined,
      ),
      "/admin/llm/document-ai",
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Save settings" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Someone changed these settings meanwhile.",
    );
  });
});
