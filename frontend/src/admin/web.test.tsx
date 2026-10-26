import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Me } from "../api/queries";
import type { WebProvider, WebSettings } from "../api/web";
import {
  CSRF_TOKEN,
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

const SEARXNG: WebProvider = {
  kind: "searxng",
  label: "SearXNG",
  internal: true,
  base_url: "http://searxng:8080",
  has_api_key: false,
  test_ok: null,
  test_error: null,
  last_tested_at: null,
  updated_at: "2026-01-05T09:00:00Z",
};

const LANGSEARCH: WebProvider = {
  kind: "langsearch",
  label: "LangSearch",
  internal: false,
  base_url: "https://api.langsearch.com/v1/web-search",
  has_api_key: true,
  test_ok: null,
  test_error: null,
  last_tested_at: null,
  updated_at: "2026-01-05T09:00:00Z",
};

const OFF: WebSettings = {
  enabled: false,
  normal_provider: null,
  internal_provider: null,
  providers: [],
  updated_at: null,
};

function as(handle: (r: ApiRequest) => Response | undefined) {
  return (request: ApiRequest) =>
    request.path === "/api/v1/me" ? json(ROOT) : handle(request);
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

describe("web access admin page", () => {
  it("is linked from the admin navigation and says web access is off", async () => {
    renderApp(
      as((r) => (r.path === "/api/v1/admin/web" ? json(OFF) : undefined)),
      "/admin/web",
    );

    expect(await screen.findByText("Off: the assistant has no web access.")).toBeInTheDocument();
    const nav = screen.getByRole("navigation", { name: "Administration" });
    expect(
      within(nav).getByRole("link", { name: "Web access" }),
    ).toHaveAttribute("href", "/admin/web");
  });

  it("registers SearXNG and LangSearch", async () => {
    const requests = renderApp(
      as((r) => {
        if (r.path === "/api/v1/admin/web") return json(OFF);
        if (r.path === "/api/v1/admin/web/providers/searxng")
          return json({ ...OFF, providers: [SEARXNG] });
        if (r.path === "/api/v1/admin/web/providers/langsearch")
          return json({ ...OFF, providers: [LANGSEARCH, SEARXNG] });
        return undefined;
      }),
      "/admin/web",
    );

    const searx = await screen.findByRole("form", { name: "SearXNG" });
    fireEvent.change(within(searx).getByLabelText("SearXNG URL"), {
      target: { value: "http://searxng:8080" },
    });
    fireEvent.click(within(searx).getByRole("button", { name: "Register" }));
    await waitFor(() =>
      expect(requests).toContainEqual({
        method: "PUT",
        path: "/api/v1/admin/web/providers/searxng",
        query: {},
        csrf: CSRF_TOKEN,
        body: { base_url: "http://searxng:8080" },
      }),
    );

    const lang = screen.getByRole("form", { name: "LangSearch" });
    fireEvent.change(within(lang).getByLabelText("API key"), {
      target: { value: "sk-lang" },
    });
    fireEvent.click(within(lang).getByRole("button", { name: "Register" }));
    await waitFor(() =>
      expect(requests).toContainEqual({
        method: "PUT",
        path: "/api/v1/admin/web/providers/langsearch",
        query: {},
        csrf: CSRF_TOKEN,
        body: { api_key: "sk-lang" },
      }),
    );
  });

  it("tests a provider and shows why it failed", async () => {
    let tested = false;
    renderApp(
      as((r) => {
        if (r.path === "/api/v1/admin/web")
          return json({
            ...OFF,
            providers: [
              tested
                ? {
                    ...LANGSEARCH,
                    test_ok: false,
                    test_error: "LangSearch rejected the API key.",
                  }
                : LANGSEARCH,
            ],
          });
        if (r.path === "/api/v1/admin/web/providers/langsearch/test") {
          tested = true;
          return json({ ...LANGSEARCH, test_ok: false });
        }
        return undefined;
      }),
      "/admin/web",
    );

    const lang = await screen.findByRole("form", { name: "LangSearch" });
    fireEvent.click(within(lang).getByRole("button", { name: "Test" }));

    expect(
      await screen.findByText("LangSearch rejected the API key."),
    ).toBeInTheDocument();
    expect(screen.getByText("Test failed")).toBeInTheDocument();
  });

  it("turns web access on and picks the provider of each kind of Workspace", async () => {
    const both = { ...OFF, providers: [LANGSEARCH, SEARXNG] };
    const requests = renderApp(
      as((r) => {
        if (r.path !== "/api/v1/admin/web") return undefined;
        return r.method === "PUT"
          ? json({ ...both, ...(r.body as object), updated_at: "2026-01-06T09:00:00Z" })
          : json(both);
      }),
      "/admin/web",
    );

    const form = await screen.findByRole("form", { name: "Web access" });
    const internalOnly = within(form).getByLabelText(
      /provider for internal-only Workspaces/,
    );
    // Internal-only Workspaces are offered internal providers only.
    expect(
      within(internalOnly).queryByRole("option", { name: /LangSearch/ }),
    ).toBeNull();
    fireEvent.click(within(form).getByRole("switch"));
    fireEvent.change(
      within(form).getByLabelText(/^Search provider for Workspaces/),
      { target: { value: "langsearch" } },
    );
    fireEvent.change(internalOnly, { target: { value: "searxng" } });
    fireEvent.click(within(form).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(requests).toContainEqual({
        method: "PUT",
        path: "/api/v1/admin/web",
        query: {},
        csrf: CSRF_TOKEN,
        body: {
          enabled: true,
          normal_provider: "langsearch",
          internal_provider: "searxng",
        },
      }),
    );
    expect(
      await screen.findByText(/may search the web/),
    ).toBeInTheDocument();
  });
});
