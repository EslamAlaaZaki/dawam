"use client";

import { useState, type FormEvent } from "react";

import {
  useConversation,
  useConversations,
  useCreateConversation,
  useSendMessage,
  useShareConversation,
  useStopResponse,
  type AnswerEvent,
  type ChatMessage,
  type PageContext,
  type Run,
  type ToolCall,
} from "../api/assistant";
import { ApiError } from "../api/client";
import { allows, type Workspace } from "../api/workspaces";

/** What is being answered right now. */
interface Draft {
  question: string;
  text: string;
  tools: ToolCall[];
}

/** Which tools an answer used, with their arguments and how long each took (story 144). */
function ToolList({ tools }: { tools: readonly ToolCall[] }) {
  if (tools.length === 0) {
    return null;
  }
  return (
    <details className="assistant-tools">
      <summary>
        {tools.length === 1 ? "1 tool used" : `${tools.length} tools used`}
      </summary>
      <ul>
        {tools.map((tool, index) => (
          <li key={index}>
            <strong>{tool.name}</strong> ({tool.status}, {tool.duration_ms} ms)
            <code>{JSON.stringify(tool.arguments)}</code>
          </li>
        ))}
      </ul>
    </details>
  );
}

/** Why the assistant could not answer, shown in place of the answer. */
function FailureNote({ run }: { run: Run }) {
  if (run.status !== "failed") {
    return null;
  }
  return (
    <p role="alert" className="assistant-failure">
      The assistant could not answer: {run.error_message ?? "an unknown problem"}
    </p>
  );
}

function Turn({ message, answer }: { message: ChatMessage; answer: ChatMessage | undefined }) {
  const run = message.run;
  return (
    <li className="assistant-turn">
      <p className="assistant-question">{message.content}</p>
      {run && <ToolList tools={run.tool_calls} />}
      {answer && <p className="assistant-answer">{answer.content}</p>}
      {run?.status === "cancelled" && <p className="assistant-note">Stopped.</p>}
      {run?.status === "tool_limit" && (
        <p className="assistant-note">Stopped using tools: the limit for one request was reached.</p>
      )}
      {run && <FailureNote run={run} />}
    </li>
  );
}

/**
 * The chat panel of a Workspace (spec §6.16): the member asks, the answer streams in with
 * the tools it used, and a Stop button cancels it. Conversations are private unless the
 * member shares them with the Workspace; an archived Workspace's chat is read-only.
 */
export function AssistantPanel({
  workspace,
  context,
}: {
  workspace: Workspace;
  context: PageContext | null;
}) {
  const conversations = useConversations(workspace.id);
  const create = useCreateConversation(workspace.id);
  const [chosen, setChosen] = useState<string | null>(null);
  const selected = chosen ?? conversations.data?.[0]?.id ?? null;
  const detail = useConversation(workspace.id, selected);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [question, setQuestion] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [failed, setFailed] = useState<Run | null>(null);
  const send = useSendMessage(workspace.id);
  const stop = useStopResponse(workspace.id);
  const share = useShareConversation(workspace.id, selected ?? "");
  const canAsk = allows(workspace, "assistant.ask");
  const archived = workspace.status === "archived";
  const current = conversations.data?.find((c) => c.id === selected);
  // A conversation not in the list yet was just started by this member.
  const mine = current?.mine ?? true;
  const running = draft !== null;

  async function ask(conversationId: string, content: string) {
    setRefusal(null);
    setFailed(null);
    setDraft({ question: content, text: "", tools: [] });
    try {
      await send.mutateAsync({
        conversationId,
        content,
        context: context ?? undefined,
        onEvent: (event: AnswerEvent) => {
          if (event.type === "text") {
            setDraft((d) => d && { ...d, text: d.text + event.text });
          } else if (event.type === "tool") {
            setDraft((d) => d && { ...d, tools: [...d.tools, event.call] });
          } else if (event.type === "done" && event.run.status === "failed") {
            setFailed(event.run);
          }
        },
      });
    } catch (error) {
      setRefusal(error instanceof ApiError ? error.message : "The assistant did not answer.");
    } finally {
      setDraft(null);
    }
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const content = question.trim();
    if (!content || running) {
      return;
    }
    setQuestion("");
    let id = selected;
    if (id === null) {
      const created = await create.mutateAsync();
      id = created.id;
      setChosen(id);
    }
    await ask(id, content);
  }

  const messages = detail.data?.messages ?? [];
  const turns = messages
    .map((message, index) => ({ message, answer: messages[index + 1] }))
    .filter(({ message }) => message.role === "user");

  return (
    <section className="assistant" aria-labelledby="assistant-title">
      <h3 id="assistant-title">Assistant</h3>
      {archived && (
        <p className="assistant-note">
          This Workspace is archived: conversations are read-only.
        </p>
      )}
      <div className="assistant-bar">
        {conversations.isSuccess && conversations.data.length > 0 && (
          <label>
            Conversation{" "}
            <select
              value={selected ?? ""}
              onChange={(event) => {
                setChosen(event.target.value);
                setFailed(null);
                setRefusal(null);
              }}
              disabled={running}
            >
              {conversations.data.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.title}
                  {c.mine ? "" : " (shared)"}
                </option>
              ))}
            </select>
          </label>
        )}
        {canAsk && !archived && (
          <button
            type="button"
            disabled={running || create.isPending}
            onClick={async () => {
              const created = await create.mutateAsync();
              setChosen(created.id);
            }}
          >
            New conversation
          </button>
        )}
        {current && mine && !archived && (
          <label>
            <input
              type="checkbox"
              checked={current.shared_with_workspace}
              disabled={share.isPending}
              onChange={(event) => share.mutate(event.target.checked)}
            />{" "}
            Share with the Workspace&apos;s members
          </label>
        )}
      </div>
      {conversations.isError && (
        <p className="assistant-note">Could not load the conversations.</p>
      )}
      {current && !mine && (
        <p className="assistant-note">Shared by a colleague: you can read it, not reply.</p>
      )}
      <ol className="assistant-turns" aria-label="Conversation">
        {turns.map(({ message, answer }) => (
          <Turn
            key={message.id}
            message={message}
            answer={answer?.role === "assistant" ? answer : undefined}
          />
        ))}
        {draft && (
          <li className="assistant-turn" aria-busy="true">
            <p className="assistant-question">{draft.question}</p>
            <ToolList tools={draft.tools} />
            <p className="assistant-answer">{draft.text || "Thinking…"}</p>
          </li>
        )}
      </ol>
      {failed && !draft && turns.every(({ message }) => message.run?.id !== failed.id) && (
        <FailureNote run={failed} />
      )}
      {refusal && (
        <p role="alert" className="assistant-failure">
          {refusal}
        </p>
      )}
      {canAsk && !archived && (selected === null || mine) && (
        <form onSubmit={onSubmit} className="assistant-form">
          <label>
            Ask the assistant
            <textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={2}
              maxLength={8000}
            />
          </label>
          <div className="form-actions">
            <button type="submit" disabled={running || question.trim() === ""}>
              Send
            </button>
            {running && selected && (
              <button type="button" onClick={() => stop.mutate(selected)}>
                Stop
              </button>
            )}
          </div>
        </form>
      )}
    </section>
  );
}
