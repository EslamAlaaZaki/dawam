"use client";

import { X } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent } from "react";

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
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import { isActive, useCancelJob, useJob } from "../api/jobs";
import { allows, type Workspace } from "../api/workspaces";
import { ConversationChangeSets } from "./ChangeSetReview";
import type { AssistantRequest } from "./FailedChecksPanel";
import {
  Actions,
  ErrorText,
  Hint,
  NativeSelect,
  checkClasses,
  checkLabelClasses,
  h3Classes,
  labelClasses,
  monoClasses,
} from "./parts";

const noteClasses = "text-sm text-sand-11";

/** What is being answered right now. */
interface Draft {
  question: string;
  text: string;
  tools: ToolCall[];
}

/** A long task the assistant handed to a background job: its status and progress, live. */
function JobProgress({
  workspaceId,
  jobId,
  title,
}: {
  workspaceId: string;
  jobId: string;
  title: string;
}) {
  const job = useJob(jobId);
  const cancel = useCancelJob(workspaceId);
  const current = job.data;
  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-lg border border-sand-5 bg-sand-2 p-2.5 text-sm"
      role="status"
      aria-label={`Job: ${title}`}
    >
      <strong className="font-semibold">{title}</strong>{" "}
      {current ? (
        <>
          <Badge>{current.status}</Badge>
          <progress
            max={100}
            value={current.progress}
            aria-label="Job progress"
            className="h-1.5 w-24 accent-[var(--accent-solid)]"
          />
          {isActive(current) && (
            <Button
              type="button"
              size="sm"
              onClick={() => cancel.mutate(jobId)}
            >
              Cancel job
            </Button>
          )}
          {current.error && <p className="w-full text-red-11">{current.error}</p>}
        </>
      ) : (
        <span className="text-sand-11">starting</span>
      )}
    </div>
  );
}

function jobsOf(tools: readonly ToolCall[]) {
  return tools.flatMap((tool) =>
    tool.name === "start_job" && typeof tool.result?.job_id === "string"
      ? [
          {
            id: tool.result.job_id,
            title: String(tool.result.title ?? "Assistant job"),
          },
        ]
      : [],
  );
}

/** Which tools an answer used, with their arguments and how long each took (story 144). */
function ToolList({
  tools,
  workspaceId,
}: {
  tools: readonly ToolCall[];
  workspaceId: string;
}) {
  if (tools.length === 0) {
    return null;
  }
  const canWrite = tools.some(
    (tool) =>
      tool.name === "run_source_query" && tool.result?.can_write === true,
  );
  return (
    <>
      {jobsOf(tools).map((job) => (
        <JobProgress
          key={job.id}
          workspaceId={workspaceId}
          jobId={job.id}
          title={job.title}
        />
      ))}
      {canWrite && (
        <p
          role="alert"
          className="rounded-lg border border-amber-11/40 bg-amber-3 px-3 py-2 text-sm text-sand-12"
        >
          Warning: the database user of this Connection can change data. The
          assistant only runs read-only SELECT queries, but an owner should give
          DAWAM a read-only user.
        </p>
      )}
      <details className={noteClasses}>
        <summary className="cursor-pointer rounded-sm">
          {tools.length === 1 ? "1 tool used" : `${tools.length} tools used`}
        </summary>
        <ul className="mt-1.5 flex flex-col gap-1.5">
          {tools.map((tool, index) => (
            <li key={index}>
              <strong>{tool.name}</strong> ({tool.status}, {tool.duration_ms} ms
              {typeof tool.result?.row_count === "number"
                ? `, ${tool.result.row_count} rows`
                : ""}
              )
              <code className={`block break-words ${monoClasses}`}>
                {JSON.stringify(tool.arguments)}
              </code>
            </li>
          ))}
        </ul>
      </details>
    </>
  );
}

/** Why the assistant could not answer, shown in place of the answer. */
function FailureNote({ run }: { run: Run }) {
  if (run.status !== "failed") {
    return null;
  }
  return (
    <ErrorText>
      The assistant could not answer:{" "}
      {run.error_message ?? "an unknown problem"}
    </ErrorText>
  );
}

const turnClasses = "flex flex-col gap-1.5 border-b border-sand-4 py-3";
const questionClasses = "font-semibold whitespace-pre-wrap";
const answerClasses = "whitespace-pre-wrap";

function Turn({
  message,
  answer,
  workspaceId,
}: {
  message: ChatMessage;
  answer: ChatMessage | undefined;
  workspaceId: string;
}) {
  const run = message.run;
  return (
    <li className={turnClasses}>
      <p className={questionClasses}>{message.content}</p>
      {run && <ToolList tools={run.tool_calls} workspaceId={workspaceId} />}
      {answer && <p className={answerClasses}>{answer.content}</p>}
      {run?.status === "cancelled" && (
        <Hint>Stopped.</Hint>
      )}
      {run?.status === "tool_limit" && (
        <Hint>Stopped using tools: the limit for one request was reached.</Hint>
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
  request = null,
  onClose,
}: {
  workspace: Workspace;
  context: PageContext | null;
  /** A question to ask on the member's behalf (a page action), once per `nonce`. */
  request?: AssistantRequest | null;
  /** Closes the panel (the overlay below 1024px has no other way out). */
  onClose?: () => void;
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

  async function ask(
    conversationId: string,
    content: string,
    asking: PageContext | null = context,
  ) {
    setRefusal(null);
    setFailed(null);
    setDraft({ question: content, text: "", tools: [] });
    try {
      await send.mutateAsync({
        conversationId,
        content,
        context: asking ?? undefined,
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
      setRefusal(
        error instanceof ApiError
          ? error.message
          : "The assistant did not answer.",
      );
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

  // Ask a page action's question once, in the current conversation or a new one.
  const asked = useRef(0);
  useEffect(() => {
    if (
      !request ||
      request.nonce === asked.current ||
      !canAsk ||
      archived ||
      running
    ) {
      return;
    }
    asked.current = request.nonce;
    void (async () => {
      let id = selected;
      if (id === null) {
        const created = await create.mutateAsync();
        id = created.id;
        setChosen(id);
      }
      await ask(id, request.prompt, request.context);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request]);

  const messages = detail.data?.messages ?? [];
  const turns = messages
    .map((message, index) => ({ message, answer: messages[index + 1] }))
    .filter(({ message }) => message.role === "user");

  return (
    <section
      className="flex flex-col gap-3"
      aria-labelledby="assistant-title"
    >
      <div className="flex items-center justify-between gap-2">
        <h3 id="assistant-title" className={h3Classes}>
          Assistant
        </h3>
        {onClose && (
          <Button
            variant="ghost"
            size="icon"
            aria-label="Close Assistant"
            onClick={onClose}
          >
            <X aria-hidden />
          </Button>
        )}
      </div>
      {archived && (
        <Hint>This Workspace is archived: conversations are read-only.</Hint>
      )}
      <div className="flex flex-wrap items-end gap-2.5">
        {conversations.isSuccess && conversations.data.length > 0 && (
          <label className={labelClasses}>
            Conversation
            <NativeSelect
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
            </NativeSelect>
          </label>
        )}
        {canAsk && !archived && (
          <Button
            type="button"
            disabled={running || create.isPending}
            onClick={async () => {
              const created = await create.mutateAsync();
              setChosen(created.id);
            }}
          >
            New conversation
          </Button>
        )}
        {current && mine && !archived && (
          <label className={checkLabelClasses}>
            <input
              type="checkbox"
              className={checkClasses}
              checked={current.shared_with_workspace}
              disabled={share.isPending}
              onChange={(event) => share.mutate(event.target.checked)}
            />
            Share with the Workspace&apos;s members
          </label>
        )}
      </div>
      {conversations.isError && (
        <ErrorText>Could not load the conversations.</ErrorText>
      )}
      {current && !mine && (
        <Hint>Shared by a colleague: you can read it, not reply.</Hint>
      )}
      <ol className="flex flex-col" aria-label="Conversation">
        {turns.map(({ message, answer }) => (
          <Turn
            key={message.id}
            message={message}
            workspaceId={workspace.id}
            answer={answer?.role === "assistant" ? answer : undefined}
          />
        ))}
        {draft && (
          <li className={turnClasses} aria-busy="true">
            <p className={questionClasses}>{draft.question}</p>
            <ToolList tools={draft.tools} workspaceId={workspace.id} />
            <p className={answerClasses}>{draft.text || "Thinking…"}</p>
          </li>
        )}
      </ol>
      {selected &&
        messages.some((m) =>
          m.run?.tool_calls.some(
            (t) =>
              t.name === "propose_changes" || t.name === "propose_check_fix",
          ),
        ) && (
          <ConversationChangeSets
            workspace={workspace}
            conversationId={selected}
            messageCount={messages.length}
          />
        )}
      {failed &&
        !draft &&
        turns.every(({ message }) => message.run?.id !== failed.id) && (
          <FailureNote run={failed} />
        )}
      {refusal && (
        <ErrorText>{refusal}</ErrorText>
      )}
      {canAsk && !archived && (selected === null || mine) && (
        <form onSubmit={onSubmit} className="flex flex-col gap-2.5">
          <label className={labelClasses}>
            Ask the assistant
            <Textarea
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              rows={2}
              maxLength={8000}
            />
          </label>
          <Actions>
            <Button
              type="submit"
              variant="primary"
              disabled={running || question.trim() === ""}
            >
              Send
            </Button>
            {running && selected && (
              <Button type="button" onClick={() => stop.mutate(selected)}>
                Stop
              </Button>
            )}
          </Actions>
        </form>
      )}
    </section>
  );
}
