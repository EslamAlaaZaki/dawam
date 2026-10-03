import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";
import { createApiClient } from "./api/client";
import { ApiClientContext } from "./api/context";

function renderApp(respond: (request: Request) => Response) {
  const requests: Request[] = [];
  const fetchStub = async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = new Request(input, init);
    requests.push(request);
    return respond(request);
  };
  const client = createApiClient({ baseUrl: "http://dawam.test", fetch: fetchStub });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <ApiClientContext.Provider value={client}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ApiClientContext.Provider>,
  );
  return requests;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("App shell", () => {
  it("shows the product name", () => {
    renderApp(() => json({ name: "DAWAM", version: "1.2.3" }));

    expect(screen.getByRole("heading", { name: "DAWAM" })).toBeInTheDocument();
  });

  it("shows the API version fetched through the generated client", async () => {
    const requests = renderApp(() => json({ name: "DAWAM", version: "1.2.3" }));

    expect(await screen.findByText("API version 1.2.3")).toBeInTheDocument();
    expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
      "GET http://dawam.test/api/v1/version",
    ]);
  });

  it("shows the API's error message when the API fails", async () => {
    renderApp(() =>
      json(
        { error: { code: "not_ready", message: "DAWAM is not ready.", details: {} } },
        503,
      ),
    );

    expect(await screen.findByText("API unavailable: DAWAM is not ready.")).toBeInTheDocument();
  });
});
