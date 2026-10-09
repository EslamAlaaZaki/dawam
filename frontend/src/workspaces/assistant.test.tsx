import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { splitFrames } from "../api/assistant";
import type { ConversationDetail, Run } from "../api/assistant";
import type { Me } from "../api/queries";
import type { Workspace } from "../api/workspaces";
import {
  CSRF_TOKEN,
  json,
  noContent,
  renderApp,
  setCsrfCookie,
  type ApiRequest,
} from "../test/renderApp";

const WORKSPACE_ID = "11111111-1111-4111-8111-111111111111";
const CONVERSATION_ID = "66666666-6666-4666-8666-666666666666";
const PATH = `/workspaces/${WORKSPACE_ID}`;
const API = `/api/v1/workspaces/${WORKSPACE_ID}`;
const ASSISTANT = `${API}/assistant/conversations`;

const ADA: Me = {
  id: "8d6f1c1e-1f43-4c1b-9b8f-5a1f2b3c4d5e",
  email: "ada@example.com",
  display_name: "Ada Lovelace",
  system_role: "user",
  must_change_password: false,
};

function workspace(status: Workspace["status"] = "active"): Workspace {
  return {
    id: WORKSPACE_ID,
    name: "Retail DW",
    description: "",
    domain: "",
    role: "viewer",
    permissions: ["assistant.ask", "workspace.view"],
    status,
    archived_at: null,
    version: 1,
    created_at: "2026-01-05T09:00:00Z",
    updated_at: "2026-01-05T09:00:00Z",
  };
}

const NEW_CONVERSATION = {
  id: CONVERSATION_ID,
  title: "New conversation",
  shared_with_workspace: false,
  mine: true,
  created_at: "2026-01-05T10:00:00Z",
  updated_at: "2026-01-05T10:00:00Z",
};

function run(overrides: Partial<Run> = {}): Run {
  return {
    id: "77777777-7777-4777-8777-777777777777",
    status: "completed",
    tool_calls: [],
    input_tokens: 10,
    output_tokens: 5,
    duration_ms: 120,
    error_code: null,
    error_message: null,
    ...overrides,
  };
}

function frame(event: string, data: unknown): string {
  return `event: ${event}\ndata: ${JSON.stringify(data)}\n\n`;
}

function sse(frames: string[]): Response {
  return new Response(frames.join(""), {
    headers: { "Content-Type": "text/event-stream" },
  });
}

const JOB_ID = "99999999-9999-4999-8999-999999999999";
const TOOL = {
  name: "get_object",
  arguments: { kind: "kpi" },
  status: "ok",
  duration_ms: 42,
};

interface Options {
  status?: Workspace["status"];
  conversations?: unknown[];
  detail?: ConversationDetail;
  answer?: () => Response | Promise<Response>;
  stopped?: () => void;
  job?: Record<string, unknown>;
}

function backend(options: Options = {}) {
  return (r: ApiRequest) => {
    switch (`${r.method} ${r.path}`) {
      case "GET /api/v1/me":
        return json(ADA);
      case `GET ${API}`:
        return json(workspace(options.status));
      case `GET ${API}/progress`:
        return json({
          source_analysis: [],
          kpis: { status: "not_started" },
          dw_modeling: [],
        });
      case `GET ${API}/members`:
        return json({ items: [] });
      case `GET ${API}/jobs`:
        return json({ items: [], next_cursor: null });
      case `GET ${ASSISTANT}`:
        return json({ items: options.conversations ?? [] });
      case `POST ${ASSISTANT}`:
        return json(NEW_CONVERSATION, 201);
      case `GET ${ASSISTANT}/${CONVERSATION_ID}`:
        return json(
          options.detail ?? { conversation: NEW_CONVERSATION, messages: [] },
        );
      case `POST ${ASSISTANT}/${CONVERSATION_ID}/messages`:
        return options.answer?.();
      case `GET /api/v1/jobs/${JOB_ID}`:
        return json(options.job);
      case `POST ${ASSISTANT}/${CONVERSATION_ID}/stop`:
        options.stopped?.();
        return noContent();
    }
    return undefined;
  };
}

let clearCookie: () => void;
beforeEach(() => {
  clearCookie = setCsrfCookie();
});
afterEach(() => clearCookie());

async function ask(question: string) {
  fireEvent.change(await screen.findByLabelText("Ask the assistant"), {
    target: { value: question },
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
}

describe("the assistant's chat panel", () => {
  it("streams the answer, shows the tools it used and sends the page as context", async () => {
    const requests = renderApp(
      backend({
        answer: () =>
          sse([
            frame("started", { run_id: "r", message_id: "m" }),
            frame("tool", TOOL),
            frame("text", { text: "Net Revenue " }),
            frame("text", { text: "is a KPI." }),
            frame("done", {
              run: run({ tool_calls: [TOOL] }),
              message_id: "a",
            }),
          ]),
        detail: {
          conversation: NEW_CONVERSATION,
          messages: [
            {
              id: "m1",
              role: "user",
              content: "What is Net Revenue?",
              created_at: "2026-01-05T10:00:00Z",
              run: run({ tool_calls: [TOOL] }),
            },
            {
              id: "m2",
              role: "assistant",
              content: "Net Revenue is a KPI.",
              created_at: "2026-01-05T10:00:01Z",
              run: null,
            },
          ],
        },
      }),
      PATH,
    );

    await ask("What is Net Revenue?");

    expect(
      await screen.findByText("Net Revenue is a KPI."),
    ).toBeInTheDocument();
    const tools = screen
      .getByText("1 tool used")
      .closest("details") as HTMLElement;
    expect(within(tools).getByText("get_object")).toBeInTheDocument();
    expect(within(tools).getByText(/42 ms/)).toBeInTheDocument();
    const sent = requests.find(
      (r) => r.method === "POST" && r.path.endsWith("/messages"),
    );
    expect(sent).toMatchObject({
      csrf: CSRF_TOKEN,
      body: {
        content: "What is Net Revenue?",
        context: { type: "folder", id: "workspace", label: "Retail DW" },
      },
    });
  });

  it("shows a source query's row count and warns when the connection user can write", async () => {
    const query = {
      name: "run_source_query",
      arguments: { sql: "SELECT COUNT(*) FROM s.t" },
      status: "ok",
      duration_ms: 12,
      result: {
        columns: ["n"],
        row_count: 1,
        duration_ms: 12,
        can_write: true,
      },
    };
    renderApp(
      backend({
        answer: () =>
          sse([
            frame("started", { run_id: "r", message_id: "m" }),
            frame("tool", query),
            frame("text", { text: "Counted." }),
            frame("done", {
              run: run({ tool_calls: [query] }),
              message_id: "a",
            }),
          ]),
        detail: {
          conversation: NEW_CONVERSATION,
          messages: [
            {
              id: "m1",
              role: "user",
              content: "Count them",
              created_at: "2026-01-05T10:00:00Z",
              run: run({ tool_calls: [query] }),
            },
            {
              id: "m2",
              role: "assistant",
              content: "Counted.",
              created_at: "2026-01-05T10:00:01Z",
              run: null,
            },
          ],
        },
      }),
      PATH,
    );

    await ask("Count them");

    expect(await screen.findByText(/can change data/)).toBeInTheDocument();
    expect(screen.getAllByText(/1 rows/).length).toBeGreaterThan(0);
  });

  it("shows the progress of a job the assistant started", async () => {
    const start = {
      name: "start_job",
      arguments: { title: "Explore tables", task: "x" },
      status: "ok",
      duration_ms: 5,
      result: { job_id: JOB_ID, title: "Explore tables" },
    };
    renderApp(
      backend({
        job: {
          id: JOB_ID,
          status: "running",
          progress: 40,
          error: null,
          log: "",
        },
        detail: {
          conversation: NEW_CONVERSATION,
          messages: [
            {
              id: "m1",
              role: "user",
              content: "Explore everything",
              created_at: "2026-01-05T10:00:00Z",
              run: run({ tool_calls: [start] }),
            },
          ],
        },
        answer: () =>
          sse([
            frame("started", { run_id: "r", message_id: "m" }),
            frame("tool", start),
            frame("text", { text: "Started." }),
            frame("done", {
              run: run({ tool_calls: [start] }),
              message_id: "a",
            }),
          ]),
      }),
      PATH,
    );

    await ask("Explore everything");

    const job = await screen.findByRole("status", {
      name: "Job: Explore tables",
    });
    expect(await within(job).findByText("running")).toBeInTheDocument();
    expect(within(job).getByRole("progressbar")).toHaveAttribute("value", "40");
    expect(
      within(job).getByRole("button", { name: "Cancel job" }),
    ).toBeInTheDocument();
  });

  it("stops the running answer with the Stop button", async () => {
    let finish: () => void = () => {};
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode(frame("text", { text: "Looking" })));
        finish = () => {
          controller.enqueue(
            encoder.encode(
              frame("done", {
                run: run({ status: "cancelled" }),
                message_id: null,
              }),
            ),
          );
          controller.close();
        };
      },
    });
    const requests = renderApp(
      backend({
        answer: () =>
          new Response(stream, {
            headers: { "Content-Type": "text/event-stream" },
          }),
        stopped: () => finish(),
      }),
      PATH,
    );

    await ask("Dig into it");
    expect(await screen.findByText("Looking")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));

    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Stop" }),
      ).not.toBeInTheDocument(),
    );
    expect(
      requests.find(
        (r) =>
          r.method === "POST" &&
          r.path === `${ASSISTANT}/${CONVERSATION_ID}/stop`,
      ),
    ).toMatchObject({ csrf: CSRF_TOKEN });
  });

  it("shows why the assistant could not answer", async () => {
    renderApp(
      backend({
        answer: () =>
          sse([
            frame("started", { run_id: "r", message_id: "m" }),
            frame("done", {
              run: run({
                status: "failed",
                error_code: "token_budget_exhausted",
                error_message:
                  "The Workspace's monthly AI token budget is used up.",
              }),
              message_id: null,
            }),
          ]),
      }),
      PATH,
    );

    await ask("Hello?");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The assistant could not answer");
    expect(alert).toHaveTextContent("monthly AI token budget is used up");
  });

  it("is read-only in an archived Workspace", async () => {
    renderApp(
      backend({
        status: "archived",
        conversations: [NEW_CONVERSATION],
        detail: {
          conversation: NEW_CONVERSATION,
          messages: [
            {
              id: "m1",
              role: "user",
              content: "An old question",
              created_at: "2026-01-05T10:00:00Z",
              run: run(),
            },
          ],
        },
      }),
      PATH,
    );

    expect(await screen.findByText("An old question")).toBeInTheDocument();
    expect(screen.getByText(/conversations are read-only/)).toBeInTheDocument();
    expect(
      screen.queryByLabelText("Ask the assistant"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "New conversation" }),
    ).not.toBeInTheDocument();
  });

  it("lets the owner share a conversation, and only read a colleague's", async () => {
    const requests = renderApp(
      backend({
        conversations: [
          NEW_CONVERSATION,
          {
            ...NEW_CONVERSATION,
            id: "88888888-8888-4888-8888-888888888888",
            mine: false,
          },
        ],
      }),
      PATH,
    );

    fireEvent.click(await screen.findByLabelText(/Share with the Workspace/));

    await waitFor(() =>
      expect(requests.find((r) => r.method === "PATCH")).toMatchObject({
        path: `${ASSISTANT}/${CONVERSATION_ID}`,
        body: { shared_with_workspace: true },
        csrf: CSRF_TOKEN,
      }),
    );
  });
});

describe("splitFrames", () => {
  it("keeps an unfinished frame for the next chunk", () => {
    const first = splitFrames('event: text\ndata: {"text":"a"}\n\nevent: te');
    expect(first.frames).toEqual([{ event: "text", data: '{"text":"a"}' }]);
    expect(first.rest).toBe("event: te");
  });
});
