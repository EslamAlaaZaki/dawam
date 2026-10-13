"use client";

import { ArrowUp, PanelRightClose, PanelRightOpen, Plus, Square, X } from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type ReactNode,
} from "react";

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
import { useMe } from "../api/queries";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Textarea } from "../components/ui/textarea";
import { isActive, useCancelJob, useJob } from "../api/jobs";
import { allows, type Workspace } from "../api/workspaces";
import { cn } from "../lib/utils";
import { ConversationChangeSets } from "./ChangeSetReview";
import { Avatar, Markdown, MessageTime } from "./chatParts";
import type { AssistantRequest } from "./FailedChecksPanel";
import { ErrorText, Hint, checkClasses, checkLabelClasses, h3Classes } from "./parts";

/** Wide enough to show the Conversation list beside the chat rather than as a drawer. */
const WIDE = "(min-width: 1024px)";

function isWide(): boolean {
  return typeof window !== "undefined" && window.matchMedia?.(WIDE).matches === true;
}

/** What is being answered right now. */
interface Draft {
  question: string;
  askedAt: string;
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
      className="flex flex-wrap items-center gap-2 rounded-lg border border-sand-5 bg-sand-1 p-2.5 text-sm"
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
            <Button type="button" size="sm" onClick={() => cancel.mutate(jobId)}>
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
      ? [{ id: tool.result.job_id, title: String(tool.result.title ?? "Assistant job") }]
      : [],
  );
}

function canWrite(tools: readonly ToolCall[]) {
  return tools.some(
    (tool) => tool.name === "run_source_query" && tool.result?.can_write === true,
  );
}

/**
 * What a reply's tools left for the member to see: the jobs it started and a warning when
 * a Connection's user can write. The tool calls themselves are not shown.
 */
function ToolNotes({ tools, workspaceId }: { tools: readonly ToolCall[]; workspaceId: string }) {
  return (
    <>
      {jobsOf(tools).map((job) => (
        <JobProgress key={job.id} workspaceId={workspaceId} jobId={job.id} title={job.title} />
      ))}
      {canWrite(tools) && (
        <p
          role="alert"
          className="rounded-lg border border-amber-11/40 bg-amber-3 px-3 py-2 text-sm text-sand-12"
        >
          Warning: the database user of this Connection can change data. The assistant only
          runs read-only SELECT queries, but an owner should give DAWAM a read-only user.
        </p>
      )}
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
      The assistant could not answer: {run.error_message ?? "an unknown problem"}
    </ErrorText>
  );
}

/** How a run ended, when that is worth saying. */
function RunNotes({ run, workspaceId }: { run: Run; workspaceId: string }) {
  return (
    <>
      <ToolNotes tools={run.tool_calls} workspaceId={workspaceId} />
      {run.status === "cancelled" && <Hint>Stopped.</Hint>}
      {run.status === "tool_limit" && (
        <Hint>Stopped using tools: the limit for one request was reached.</Hint>
      )}
      <FailureNote run={run} />
    </>
  );
}

function hasNotes(run: Run | null | undefined): boolean {
  return (
    !!run &&
    (["cancelled", "tool_limit", "failed"].includes(run.status) ||
      jobsOf(run.tool_calls).length > 0 ||
      canWrite(run.tool_calls))
  );
}

/**
 * One message: an avatar, who sent it and when, and its bubble. `email` is null for the
 * assistant's replies, which carry the DAWAM avatar.
 */
function Bubble({
  email,
  at,
  busy = false,
  children,
}: {
  email: string | null;
  at: string;
  busy?: boolean;
  children: ReactNode;
}) {
  const name = email ?? "DAWAM";
  return (
    <li
      aria-label={email === null ? "Reply from DAWAM" : `Message from ${email}`}
      aria-busy={busy || undefined}
      className="flex gap-2.5"
    >
      <Avatar email={email} />
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <p className="flex items-baseline gap-2">
          <span className="truncate text-sm font-semibold">{name}</span>
          <MessageTime at={at} />
        </p>
        <div
          className={cn(
            "flex flex-col gap-2 rounded-lg border px-3 py-2",
            email === null ? "border-sand-4 bg-sand-2" : "border-jade-4 bg-jade-2 whitespace-pre-wrap",
          )}
        >
          {children}
        </div>
      </div>
    </li>
  );
}

/** A member's message and the assistant's reply to it. */
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
    <>
      <Bubble email={message.sender?.email ?? "former member"} at={message.created_at}>
        {message.content}
      </Bubble>
      {(answer || hasNotes(run)) && (
        <Bubble email={null} at={answer?.created_at ?? message.created_at}>
          {answer && <Markdown>{answer.content}</Markdown>}
          {run && <RunNotes run={run} workspaceId={workspaceId} />}
        </Bubble>
      )}
    </>
  );
}

const conversationClasses =
  "flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-left text-sm text-sand-12 hover:bg-sand-3 aria-[current=true]:bg-sand-3 aria-[current=true]:font-semibold disabled:opacity-60";

/**
 * The chat of a Workspace (spec §6.16): the thread with the input pinned at the bottom, and
 * the member's Conversations in a collapsible list on the right (a drawer on narrow
 * screens). The answer streams in and Stop cancels it. Conversations are private unless the
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
  const me = useMe();
  const conversations = useConversations(workspace.id);
  const create = useCreateConversation(workspace.id);
  const [chosen, setChosen] = useState<string | null>(null);
  const selected = chosen ?? conversations.data?.[0]?.id ?? null;
  const detail = useConversation(workspace.id, selected);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [question, setQuestion] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [failed, setFailed] = useState<Run | null>(null);
  const [listOpen, setListOpen] = useState(isWide);
  const send = useSendMessage(workspace.id);
  const stop = useStopResponse(workspace.id);
  const share = useShareConversation(workspace.id, selected ?? "");
  const canAsk = allows(workspace, "assistant.ask");
  const archived = workspace.status === "archived";
  const current = conversations.data?.find((c) => c.id === selected);
  // A conversation not in the list yet was just started by this member.
  const mine = current?.mine ?? true;
  const running = draft !== null;
  const input = useRef<HTMLTextAreaElement>(null);
  const thread = useRef<HTMLDivElement>(null);

  async function ask(conversationId: string, content: string, asking: PageContext | null = context) {
    setRefusal(null);
    setFailed(null);
    setDraft({ question: content, askedAt: new Date().toISOString(), text: "", tools: [] });
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
      setRefusal(error instanceof ApiError ? error.message : "The assistant did not answer.");
    } finally {
      setDraft(null);
    }
  }

  async function submit() {
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

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit();
  }

  function choose(id: string) {
    setChosen(id);
    setFailed(null);
    setRefusal(null);
    if (!isWide()) {
      setListOpen(false);
    }
  }

  // Ask a page action's question once, in the current conversation or a new one.
  const asked = useRef(0);
  useEffect(() => {
    if (!request || request.nonce === asked.current || !canAsk || archived || running) {
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

  // The input grows with what is typed, up to its maximum height.
  useLayoutEffect(() => {
    const el = input.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    }
  }, [question]);

  const messages = detail.data?.messages ?? [];
  const turns = messages
    .map((message, index) => ({ message, answer: messages[index + 1] }))
    .filter(({ message }) => message.role === "user");

  // Keep the newest message in view.
  useEffect(() => {
    const el = thread.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, [messages.length, draft?.text]);

  return (
    <section className="relative flex h-full" aria-labelledby="assistant-title">
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b border-line px-4 py-2.5">
          <h3 id="assistant-title" className={h3Classes}>
            Assistant
          </h3>
          {current && (
            <span className="min-w-0 truncate text-sm text-sand-11">{current.title}</span>
          )}
          <span className="ml-auto" />
          <Button
            variant="ghost"
            size="icon"
            aria-label={listOpen ? "Hide conversations" : "Show conversations"}
            aria-expanded={listOpen}
            aria-controls="assistant-conversations"
            onClick={() => setListOpen((open) => !open)}
          >
            {listOpen ? <PanelRightClose aria-hidden /> : <PanelRightOpen aria-hidden />}
          </Button>
          {onClose && (
            <Button variant="ghost" size="icon" aria-label="Close Assistant" onClick={onClose}>
              <X aria-hidden />
            </Button>
          )}
        </div>
        <div ref={thread} className="flex flex-1 flex-col gap-3 overflow-y-auto px-4 py-3">
          {archived && <Hint>This Workspace is archived: conversations are read-only.</Hint>}
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
          {current && !mine && <Hint>Shared by a colleague: you can read it, not reply.</Hint>}
          <ol className="flex flex-col gap-4" aria-label="Conversation">
            {turns.map(({ message, answer }) => (
              <Turn
                key={message.id}
                message={message}
                workspaceId={workspace.id}
                answer={answer?.role === "assistant" ? answer : undefined}
              />
            ))}
            {draft && (
              <>
                <Bubble email={me.data?.email ?? "you"} at={draft.askedAt}>
                  {draft.question}
                </Bubble>
                <Bubble email={null} at={draft.askedAt} busy>
                  {draft.text ? <Markdown>{draft.text}</Markdown> : <Hint>Thinking…</Hint>}
                  <ToolNotes tools={draft.tools} workspaceId={workspace.id} />
                </Bubble>
              </>
            )}
          </ol>
          {selected &&
            messages.some((m) =>
              m.run?.tool_calls.some(
                (t) => t.name === "propose_changes" || t.name === "propose_check_fix",
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
          {refusal && <ErrorText>{refusal}</ErrorText>}
        </div>
        {canAsk && !archived && (selected === null || mine) && (
          <form onSubmit={onSubmit} className="border-t border-line px-4 py-3">
            <div className="relative">
              <Textarea
                ref={input}
                aria-label="Ask the assistant"
                placeholder="Ask the assistant…"
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                    event.preventDefault();
                    void submit();
                  }
                }}
                rows={1}
                maxLength={8000}
                className="max-h-48 min-h-10 resize-none pr-11"
              />
              {running ? (
                <Button
                  type="button"
                  size="icon"
                  aria-label="Stop"
                  className="absolute right-1.5 bottom-1.5"
                  onClick={() => selected && stop.mutate(selected)}
                >
                  <Square aria-hidden />
                </Button>
              ) : (
                <Button
                  type="submit"
                  variant="primary"
                  size="icon"
                  aria-label="Send"
                  className="absolute right-1.5 bottom-1.5"
                  disabled={question.trim() === ""}
                >
                  <ArrowUp aria-hidden />
                </Button>
              )}
            </div>
            <p className="mt-1 text-xs text-sand-11">Enter to send, Shift+Enter for a new line.</p>
          </form>
        )}
      </div>
      {listOpen && (
        <>
          {/* Below 1024px the list is a drawer over the chat; clicking beside it closes it. */}
          <div
            aria-hidden
            className="absolute inset-0 z-10 bg-overlay lg:hidden"
            onClick={() => setListOpen(false)}
          />
          <nav
            id="assistant-conversations"
            aria-label="Conversations"
            className="absolute inset-y-0 right-0 z-20 flex w-60 max-w-[85%] flex-col border-l border-line bg-sand-1 shadow-lg lg:static lg:z-auto lg:w-56 lg:shadow-none"
          >
            {canAsk && !archived && (
              <div className="p-3">
                <Button
                  type="button"
                  className="w-full"
                  disabled={running || create.isPending}
                  onClick={async () => {
                    const created = await create.mutateAsync();
                    choose(created.id);
                  }}
                >
                  <Plus aria-hidden />
                  New conversation
                </Button>
              </div>
            )}
            {conversations.isError && (
              <div className="px-3">
                <ErrorText>Could not load the conversations.</ErrorText>
              </div>
            )}
            {conversations.isSuccess && conversations.data.length === 0 && (
              <Hint className="px-3">No conversations yet.</Hint>
            )}
            {/* Room around the items so their focus ring is not cut off by the scroll box. */}
            <ul className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 pt-1 pb-3">
              {conversations.data?.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    className={conversationClasses}
                    aria-current={c.id === selected ? "true" : undefined}
                    disabled={running}
                    onClick={() => choose(c.id)}
                  >
                    <span className="min-w-0 truncate">{c.title}</span>
                    {!c.mine && <span className="shrink-0 text-xs text-sand-11">(shared)</span>}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
        </>
      )}
    </section>
  );
}
