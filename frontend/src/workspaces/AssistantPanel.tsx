"use client";

import {
  ArrowUp,
  ChevronDown,
  ChevronRight,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Square,
  ThumbsDown,
  Trash2,
  ThumbsUp,
  X,
} from "lucide-react";
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
  useDeleteConversation,
  useRateAnswer,
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
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "../components/ui/dropdown-menu";
import { Switch } from "../components/ui/switch";
import { Textarea } from "../components/ui/textarea";
import {
  isActive,
  useAnswerJob,
  useCancelJob,
  useJob,
  type Job,
} from "../api/jobs";
import {
  useWorkspaceAiSettings,
  type WorkspaceModel,
} from "../api/workspaceAi";
import { allows, type Workspace } from "../api/workspaces";
import { cn } from "../lib/utils";
import { ConversationChangeSets } from "./ChangeSetReview";
import { Avatar, Markdown, MessageTime } from "./chatParts";
import {
  ConversationSearchBox,
  ConversationSearchResults,
  useSearchTerm,
} from "./ConversationSearch";
import type { AssistantRequest } from "./FailedChecksPanel";
import {
  ErrorText,
  Hint,
  checkLabelClasses,
  h3Classes,
  monoClasses,
} from "./parts";

/** Wide enough to show the Conversation list beside the chat rather than as a drawer. */
const WIDE = "(min-width: 1024px)";

function isWide(): boolean {
  return (
    typeof window !== "undefined" && window.matchMedia?.(WIDE).matches === true
  );
}

/** What is being answered right now. */
interface Draft {
  question: string;
  askedAt: string;
  text: string;
  /** The model's reasoning, streamed only when its model is set to show it. */
  reasoning: string;
  tools: ToolCall[];
}

const PLAN_MARKS = {
  pending: { mark: "○", label: "to do", tone: "text-muted-foreground" },
  in_progress: { mark: "◐", label: "in progress", tone: "text-primary" },
  done: { mark: "●", label: "done", tone: "text-success-strong" },
  failed: { mark: "✕", label: "failed", tone: "text-danger-strong" },
  skipped: { mark: "–", label: "skipped", tone: "text-muted-foreground" },
} as const;

/** The job's todo list, as the job writes and updates it. */
function JobPlan({ plan }: { plan: NonNullable<Job["plan"]> }) {
  return (
    <ol aria-label="Job plan" className="m-0 w-full list-none space-y-1 p-0">
      {plan.map((step) => {
        const state = PLAN_MARKS[step.status];
        return (
          <li key={step.id} className="flex items-baseline gap-2">
            <span aria-hidden className={state.tone}>
              {state.mark}
            </span>
            <span
              className={cn(
                (step.status === "done" || step.status === "skipped") &&
                  "text-muted-foreground",
                step.status === "skipped" && "line-through",
              )}
            >
              {step.text}
            </span>
            <span className="sr-only">{state.label}</span>
          </li>
        );
      })}
    </ol>
  );
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
  const answer = useAnswerJob(workspaceId);
  const [reply, setReply] = useState("");
  const current = job.data;
  return (
    <div
      className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface-raised px-3 py-2 text-sm"
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
            className="h-1.5 w-24 accent-primary"
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
          {current.plan && current.plan.length > 0 && (
            <JobPlan plan={current.plan} />
          )}
          {current.error && (
            <p className="w-full text-danger-strong">{current.error}</p>
          )}
          {current.status === "paused" && current.question && (
            <form
              className="flex w-full flex-col gap-2"
              aria-label="Answer the job"
              onSubmit={(event) => {
                event.preventDefault();
                const text = reply.trim();
                if (text) {
                  answer.mutate(
                    { jobId, answer: text },
                    { onSuccess: () => setReply("") },
                  );
                }
              }}
            >
              <p className="font-medium">{current.question}</p>
              <Textarea
                aria-label="Your answer"
                value={reply}
                onChange={(event) => setReply(event.target.value)}
                rows={2}
              />
              {answer.error && <ErrorText>{answer.error.message}</ErrorText>}
              <div className="flex gap-2">
                {current.question.startsWith("May I ") && (
                  <>
                    <Button
                      type="button"
                      size="sm"
                      disabled={answer.isPending}
                      onClick={() => answer.mutate({ jobId, answer: "Yes" })}
                    >
                      Approve
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      disabled={answer.isPending}
                      onClick={() => answer.mutate({ jobId, answer: "No" })}
                    >
                      Decline
                    </Button>
                  </>
                )}
                <Button
                  type="submit"
                  size="sm"
                  disabled={!reply.trim() || answer.isPending}
                >
                  Send answer
                </Button>
              </div>
            </form>
          )}
        </>
      ) : (
        <span className="text-muted-foreground">starting</span>
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

function canWrite(tools: readonly ToolCall[]) {
  return tools.some(
    (tool) =>
      tool.name === "run_source_query" && tool.result?.can_write === true,
  );
}

const SEARCH_PROVIDERS: Record<string, string> = {
  searxng: "SearXNG",
  langsearch: "LangSearch",
};

/** What a reply sent to the web: each search query (as sent, values removed) and each page read. */
function webOf(tools: readonly ToolCall[]) {
  return tools.flatMap((tool) => {
    if (tool.name === "web_search" && typeof tool.result?.query === "string") {
      const provider = String(tool.result.provider ?? "");
      return [
        {
          label: `Searched the web (${SEARCH_PROVIDERS[provider] ?? provider})`,
          detail: tool.result.query,
        },
      ];
    }
    if (tool.name === "web_fetch" && typeof tool.result?.url === "string") {
      return [{ label: "Read a web page", detail: tool.result.url }];
    }
    return [];
  });
}

/** Documents (or tools) whose text looked like a prompt injection; the model still saw it, with a warning. */
function flaggedOf(tools: readonly ToolCall[]) {
  return [...new Set(tools.flatMap((tool) => tool.flagged ?? []))];
}

/**
 * What a reply's tools left for the member to see: the jobs it started, what it sent to the
 * web, a warning when a Connection's user can write and the documents flagged as possible
 * injections. The tool
 * calls themselves are not shown.
 */
function ToolNotes({
  tools,
  workspaceId,
}: {
  tools: readonly ToolCall[];
  workspaceId: string;
}) {
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
      {webOf(tools).length > 0 && (
        <ul
          aria-label="Web access"
          className="flex flex-col gap-1 rounded-md border border-border bg-muted px-3 py-2 text-sm text-muted-foreground"
        >
          {webOf(tools).map((item, index) => (
            <li key={index}>
              {item.label}:{" "}
              <span className="break-all text-foreground">{item.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {canWrite(tools) && (
        <p
          role="alert"
          className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-foreground"
        >
          Warning: the database user of this Connection can change data. The
          assistant only runs read-only SELECT queries, but an owner should give
          DAWAM a read-only user.
        </p>
      )}
      {flaggedOf(tools).length > 0 && (
        <p
          role="alert"
          className="rounded-md border border-warning/40 bg-warning-subtle px-3 py-2 text-sm text-foreground"
        >
          Possible prompt injection in{" "}
          {flaggedOf(tools).map((name, index) => (
            <span key={name}>
              {index > 0 && ", "}
              <strong className="font-medium">{name}</strong>
            </span>
          ))}
          . The text was still used, marked as untrusted. Check the source
          before relying on this answer.
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
      The assistant could not answer:{" "}
      {run.error_message ?? "an unknown problem"}
    </ErrorText>
  );
}

/** A small card for a reply's stop and limit hints. */
const noteClasses =
  "rounded-md border border-border bg-surface-raised px-3 py-2 text-xs";

/** How a run ended, when that is worth saying. */
function RunNotes({ run, workspaceId }: { run: Run; workspaceId: string }) {
  return (
    <>
      <ToolNotes tools={run.tool_calls} workspaceId={workspaceId} />
      {run.status === "cancelled" && (
        <Hint className={noteClasses}>Stopped.</Hint>
      )}
      {run.status === "tool_limit" && (
        <Hint className={noteClasses}>
          Stopped using tools: the limit for one request was reached.
        </Hint>
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
      webOf(run.tool_calls).length > 0 ||
      canWrite(run.tool_calls) ||
      flaggedOf(run.tool_calls).length > 0)
  );
}

/**
 * One message. A member's message is a bubble on the right, under who sent it and when. The
 * assistant's reply (`email` null) flows beside the ✦ avatar, with its time, model and
 * `actions` (the rating) in a quiet row under it.
 */
function Bubble({
  email,
  at,
  model = null,
  busy = false,
  actions = null,
  children,
}: {
  email: string | null;
  at: string;
  /** On a reply: the model that answered. */
  model?: string | null;
  busy?: boolean;
  /** On a reply: what can be done with it, shown before its time. */
  actions?: ReactNode;
  children: ReactNode;
}) {
  if (email !== null) {
    return (
      <li
        aria-label={`Message from ${email}`}
        className="flex flex-row-reverse gap-3 ps-12"
      >
        <Avatar email={email} />
        <div className="flex min-w-0 flex-col items-end gap-1">
          <p className="flex max-w-full items-baseline gap-2">
            <span className="truncate text-xs font-medium text-muted-foreground">
              {email}
            </span>
            <MessageTime at={at} />
          </p>
          <div className="max-w-full rounded-xl rounded-se-sm bg-muted px-3.5 py-2 break-words whitespace-pre-wrap">
            {children}
          </div>
        </div>
      </li>
    );
  }
  return (
    <li
      aria-label="Reply from DAWAM"
      aria-busy={busy || undefined}
      className="group/reply flex gap-3"
    >
      <Avatar email={null} />
      <div className="flex min-w-0 flex-1 flex-col gap-2 pt-1">
        {children}
        <div className="flex min-h-7 flex-wrap items-center gap-x-2 gap-y-1">
          {actions}
          <MessageTime at={at} />
          {model && (
            <span
              aria-label="Answered by"
              className={cn(
                "truncate text-xs text-subtle-foreground",
                monoClasses,
              )}
            >
              {model}
            </span>
          )}
        </div>
      </div>
    </li>
  );
}

/**
 * Thumbs up/down and an optional comment on an answer. The member rates once and can
 * change the rating or the comment later; the rating is saved on the run.
 */
function AnswerFeedback({
  run,
  workspaceId,
  conversationId,
}: {
  run: Run;
  workspaceId: string;
  conversationId: string;
}) {
  const rate = useRateAnswer(workspaceId, conversationId);
  const saved = run.feedback ?? null;
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState(saved?.comment ?? "");

  const send = (rating: "up" | "down", text: string) =>
    rate.mutate(
      { runId: run.id, rating, comment: text.trim() || null },
      { onSuccess: () => setCommenting(false) },
    );

  // Laid out in the reply's action row; the comment form and errors wrap below it.
  return (
    <>
      <div
        role="group"
        aria-label="Rate this answer"
        className="-ms-1.5 flex items-center gap-0.5"
      >
        {(["up", "down"] as const).map((rating) => {
          const Icon = rating === "up" ? ThumbsUp : ThumbsDown;
          return (
            <Button
              key={rating}
              variant="ghost"
              size="icon"
              aria-label={rating === "up" ? "Good answer" : "Bad answer"}
              aria-pressed={saved?.rating === rating}
              disabled={rate.isPending}
              className={cn(
                "size-7 text-subtle-foreground [&_svg]:size-3.5",
                saved?.rating === rating && "bg-muted text-foreground",
              )}
              onClick={() => send(rating, saved?.comment ?? "")}
            >
              <Icon aria-hidden />
            </Button>
          );
        })}
        {saved && !commenting && (
          <Button
            variant="ghost"
            size="sm"
            className="text-subtle-foreground"
            onClick={() => {
              setComment(saved.comment ?? "");
              setCommenting(true);
            }}
          >
            {saved.comment ? "Edit comment" : "Add a comment"}
          </Button>
        )}
      </div>
      {saved && commenting && (
        <form
          className="order-last flex basis-full flex-col gap-2 pt-1"
          onSubmit={(event) => {
            event.preventDefault();
            send(saved.rating, comment);
          }}
        >
          <Textarea
            aria-label="Comment on this answer"
            value={comment}
            maxLength={1000}
            rows={2}
            onChange={(event) => setComment(event.target.value)}
          />
          <div className="flex gap-2">
            <Button type="submit" size="sm" disabled={rate.isPending}>
              Save comment
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => setCommenting(false)}
            >
              Cancel
            </Button>
          </div>
        </form>
      )}
      {rate.error instanceof ApiError && (
        <div className="order-last basis-full">
          <ErrorText>{rate.error.message}</ErrorText>
        </div>
      )}
    </>
  );
}

/** A member's message and the assistant's reply to it. */
function Turn({
  message,
  answer,
  workspaceId,
  conversationId,
  canRate,
}: {
  message: ChatMessage;
  answer: ChatMessage | undefined;
  workspaceId: string;
  conversationId: string;
  canRate: boolean;
}) {
  const run = message.run;
  return (
    <>
      <Bubble
        email={message.sender?.email ?? "former member"}
        at={message.created_at}
      >
        {message.content}
      </Bubble>
      {(answer || hasNotes(run)) && (
        <Bubble
          email={null}
          at={answer?.created_at ?? message.created_at}
          model={answer?.model}
          actions={
            answer &&
            run &&
            canRate && (
              <AnswerFeedback
                run={run}
                workspaceId={workspaceId}
                conversationId={conversationId}
              />
            )
          }
        >
          {answer && <Markdown>{answer.content}</Markdown>}
          {run && <RunNotes run={run} workspaceId={workspaceId} />}
        </Bubble>
      )}
    </>
  );
}

/**
 * The model the next message goes to, as a chip in the input's toolbar. `value` may be a
 * model no longer offered; the chip then names `fallback`.
 */
function ModelPicker({
  models,
  value,
  fallback,
  disabled,
  onPick,
}: {
  models: readonly WorkspaceModel[];
  value: string;
  fallback: string;
  disabled: boolean;
  onPick: (id: string) => void;
}) {
  const name = models.find((m) => m.id === value)?.name ?? fallback;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label={`Model for this message: ${name}`}
          disabled={disabled}
          className="min-w-0 max-w-56 data-[state=open]:bg-muted data-[state=open]:text-foreground"
        >
          <span className="truncate">{name}</span>
          <ChevronDown aria-hidden className="size-3.5" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent side="top" align="start" className="w-64">
        <DropdownMenuLabel>Model for this message</DropdownMenuLabel>
        <DropdownMenuRadioGroup value={value} onValueChange={onPick}>
          {models.map((m) => (
            <DropdownMenuRadioItem key={m.id} value={m.id}>
              <span className="flex min-w-0 flex-1 flex-col">
                <span className="truncate">{m.name}</span>
                <span className="truncate text-xs text-subtle-foreground">
                  {m.provider_name}
                </span>
              </span>
              {m.limited && <Badge>limited</Badge>}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

const conversationClasses =
  "flex w-full items-center gap-1 rounded-md px-2 py-1.5 text-left text-sm text-foreground transition-colors duration-120 hover:bg-muted aria-[current=true]:bg-muted aria-[current=true]:font-semibold disabled:opacity-60";

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
  const search = useSearchTerm();
  const [chosen, setChosen] = useState<string | null>(null);
  const selected = chosen ?? conversations.data?.[0]?.id ?? null;
  const detail = useConversation(workspace.id, selected);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [question, setQuestion] = useState("");
  const [refusal, setRefusal] = useState<string | null>(null);
  const [failed, setFailed] = useState<Run | null>(null);
  const [listOpen, setListOpen] = useState(isWide);
  const [picked, setPicked] = useState<string | null>(null);
  const ai = useWorkspaceAiSettings(workspace.id);
  const send = useSendMessage(workspace.id);
  const stop = useStopResponse(workspace.id);
  const share = useShareConversation(workspace.id, selected ?? "");
  const remove = useDeleteConversation(workspace.id);
  // The conversation the member asked to delete, while they confirm.
  const [deleting, setDeleting] = useState<{ id: string; title: string } | null>(
    null,
  );
  const canAsk = allows(workspace, "assistant.ask");
  const archived = workspace.status === "archived";
  const current = conversations.data?.find((c) => c.id === selected);
  // A conversation not in the list yet was just started by this member.
  const mine = current?.mine ?? true;
  // The server decides: the starter, or an Owner or Editor in a shared conversation.
  const canPost = current?.can_post ?? true;
  const running = draft !== null;
  // The picker offers the Workspace's agent models and starts on the conversation's last
  // pick, else the Workspace's default agent model.
  const models = ai.data?.agent_models ?? [];
  const fallback = models.find((m) => m.is_default);
  const remembered = models.find((m) => m.id === current?.model_id)?.id;
  const modelId = picked ?? remembered ?? fallback?.id ?? "";
  const input = useRef<HTMLTextAreaElement>(null);
  const thread = useRef<HTMLDivElement>(null);

  async function ask(
    conversationId: string,
    content: string,
    asking: PageContext | null = context,
  ) {
    setRefusal(null);
    setFailed(null);
    setDraft({
      question: content,
      askedAt: new Date().toISOString(),
      text: "",
      reasoning: "",
      tools: [],
    });
    try {
      await send.mutateAsync({
        conversationId,
        content,
        context: asking ?? undefined,
        modelId: picked ?? undefined,
        onEvent: (event: AnswerEvent) => {
          if (event.type === "text") {
            setDraft((d) => d && { ...d, text: d.text + event.text });
          } else if (event.type === "reasoning") {
            setDraft((d) => d && { ...d, reasoning: d.reasoning + event.text });
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

  // As a drawer (narrow screens), the list takes focus when it opens and gives it back to
  // its toggle when it closes.
  const list = useRef<HTMLElement>(null);
  const listToggle = useRef<HTMLButtonElement>(null);
  const focusList = useRef(false);
  useEffect(() => {
    if (listOpen && focusList.current) {
      focusList.current = false;
      list.current
        ?.querySelector<HTMLElement>("button:not(:disabled)")
        ?.focus();
    }
  }, [listOpen]);

  function openList() {
    focusList.current = !isWide();
    setListOpen(true);
  }

  function closeList() {
    setListOpen(false);
    if (!isWide()) {
      listToggle.current?.focus();
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
    search.setTyped("");
    setChosen(id);
    setPicked(null);
    setFailed(null);
    setRefusal(null);
    if (!isWide()) {
      closeList();
    }
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
        <div className="flex items-center gap-2 px-4 py-2.5">
          <span
            aria-hidden
            className="flex size-6 shrink-0 items-center justify-center rounded-md bg-ai-gradient text-xs text-white"
          >
            ✦
          </span>
          <h3 id="assistant-title" className={h3Classes}>
            Assistant
          </h3>
          {current && (
            <span className="min-w-0 truncate text-sm text-muted-foreground">
              {current.title}
            </span>
          )}
          <span className="ml-auto" />
          <Button
            ref={listToggle}
            variant="ghost"
            size="icon"
            aria-label={listOpen ? "Hide conversations" : "Show conversations"}
            aria-expanded={listOpen}
            aria-controls="assistant-conversations"
            onClick={() => (listOpen ? closeList() : openList())}
          >
            {listOpen ? (
              <PanelRightClose aria-hidden />
            ) : (
              <PanelRightOpen aria-hidden />
            )}
          </Button>
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
        {/* The assistant's signature: a 1px gradient rule under its header. */}
        <div aria-hidden className="h-px shrink-0 bg-ai-gradient" />
        <div ref={thread} className="flex-1 overflow-y-auto px-4 py-4">
          <div className="mx-auto flex min-h-full w-full max-w-3xl flex-col gap-4">
          {archived && (
            <Hint>
              This Workspace is archived: conversations are read-only.
            </Hint>
          )}
          {current && mine && !archived && (
            <label className={checkLabelClasses}>
              <Switch
                checked={current.shared_with_workspace}
                disabled={share.isPending}
                onCheckedChange={(checked) => share.mutate(checked)}
              />
              Share with the Workspace&apos;s members
            </label>
          )}
          {turns.length === 0 && !draft && canAsk && !archived && (
            <div className="m-auto flex flex-col items-center gap-3 py-8 text-center">
              <span
                aria-hidden
                className="flex size-10 items-center justify-center rounded-xl bg-ai-gradient text-lg text-white"
              >
                ✦
              </span>
              <p className="text-xl font-semibold">How can I help?</p>
            </div>
          )}
          <ol className="flex flex-col gap-6" aria-label="Conversation">
            {turns.map(({ message, answer }) => (
              <Turn
                key={message.id}
                message={message}
                workspaceId={workspace.id}
                conversationId={selected ?? ""}
                canRate={!archived}
                answer={answer?.role === "assistant" ? answer : undefined}
              />
            ))}
            {draft && (
              <>
                <Bubble email={me.data?.email ?? "you"} at={draft.askedAt}>
                  {draft.question}
                </Bubble>
                <Bubble email={null} at={draft.askedAt} busy>
                  {draft.reasoning && (
                    <details open={!draft.text} className="group/reasoning">
                      <summary className="flex w-fit cursor-pointer list-none items-center gap-1 rounded-md text-xs font-medium text-muted-foreground transition-colors duration-120 hover:text-foreground [&::-webkit-details-marker]:hidden">
                        <ChevronRight
                          aria-hidden
                          className="size-3.5 transition-transform duration-120 group-open/reasoning:rotate-90"
                        />
                        Reasoning
                      </summary>
                      <p className="mt-1.5 ms-1.5 max-h-48 overflow-y-auto border-s-2 border-border ps-3 text-sm whitespace-pre-wrap text-muted-foreground">
                        {draft.reasoning}
                      </p>
                    </details>
                  )}
                  {draft.text ? (
                    <Markdown>{draft.text}</Markdown>
                  ) : (
                    <p className="ai-shimmer w-fit text-sm font-medium">
                      Thinking…
                    </p>
                  )}
                  <ToolNotes tools={draft.tools} workspaceId={workspace.id} />
                </Bubble>
              </>
            )}
          </ol>
          {selected &&
            messages.some((m) =>
              m.run?.tool_calls.some(
                (t) =>
                  t.name === "propose_changes" ||
                  t.name === "propose_check_fix",
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
        </div>
        {canAsk && !archived && (
          <form onSubmit={onSubmit} className="px-4 pt-1 pb-3">
            {/* One box, like a message being written: the text on top, its tools below. */}
            <div className="mx-auto flex w-full max-w-3xl flex-col rounded-xl border border-border-strong bg-surface-raised transition-colors duration-120 focus-within:border-primary focus-within:ring-2 focus-within:ring-primary-subtle">
              <Textarea
                ref={input}
                aria-label="Ask the assistant"
                placeholder="Ask the assistant…"
                disabled={!canPost}
                value={question}
                onChange={(event) => setQuestion(event.target.value)}
                onKeyDown={(event) => {
                  // keyCode 229: Safari's IME composition, which isComposing misses.
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing &&
                    event.keyCode !== 229
                  ) {
                    event.preventDefault();
                    void submit();
                  }
                }}
                rows={1}
                maxLength={8000}
                className="max-h-48 min-h-11 resize-none border-0 bg-transparent px-3.5 pt-3 pb-1 focus-visible:outline-none"
              />
              <div className="flex items-center gap-1 px-2 pb-2">
                {models.length > 0 && (
                  <ModelPicker
                    models={models}
                    value={modelId}
                    fallback={fallback?.name ?? "Default"}
                    disabled={!canPost || running}
                    onPick={setPicked}
                  />
                )}
                <span className="ms-auto" />
                {running ? (
                  <Button
                    type="button"
                    size="icon"
                    aria-label="Stop"
                    className="rounded-lg"
                    onClick={() => selected && stop.mutate(selected)}
                  >
                    <Square aria-hidden className="fill-current" />
                  </Button>
                ) : (
                  <Button
                    type="submit"
                    variant="primary"
                    size="icon"
                    aria-label="Send"
                    className="rounded-lg"
                    disabled={!canPost || question.trim() === ""}
                  >
                    <ArrowUp aria-hidden />
                  </Button>
                )}
              </div>
            </div>
            <p className="mt-1.5 text-center text-xs text-subtle-foreground">
              {canPost
                ? "Enter to send, Shift+Enter for a new line."
                : "Viewers can only read this shared conversation."}
            </p>
          </form>
        )}
      </div>
      {listOpen && (
        <>
          {/* Below 1024px the list is a drawer over the chat; clicking beside it closes it. */}
          <div
            aria-hidden
            className="absolute inset-0 z-10 bg-overlay lg:hidden"
            onClick={closeList}
          />
          <nav
            ref={list}
            id="assistant-conversations"
            aria-label="Conversations"
            onKeyDown={(event) => {
              // As a drawer, Escape closes only the drawer, not the whole Assistant.
              if (event.key === "Escape" && !isWide()) {
                event.stopPropagation();
                closeList();
              }
            }}
            className="absolute inset-y-0 right-0 z-20 flex w-60 max-w-[85%] flex-col border-l border-border bg-surface shadow-lg lg:static lg:z-auto lg:w-56 lg:shadow-none"
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
            <ConversationSearchBox
              value={search.typed}
              onChange={search.setTyped}
            />
            {search.term !== "" && (
              <ConversationSearchResults
                workspaceId={workspace.id}
                term={search.term}
                disabled={running}
                onOpen={choose}
              />
            )}
            {search.term === "" && conversations.isError && (
              <div className="px-3">
                <ErrorText>Could not load the conversations.</ErrorText>
              </div>
            )}
            {search.term === "" &&
              conversations.isSuccess &&
              conversations.data.length === 0 && (
                <Hint className="px-3">No conversations yet.</Hint>
              )}
            {/* Room around the items so their focus ring is not cut off by the scroll box. */}
            {search.term === "" && (
              <ul className="flex flex-1 flex-col gap-1 overflow-y-auto px-2 pt-1 pb-3">
                {conversations.data?.map((c) => (
                  <li key={c.id} className="group/row relative">
                    <button
                      type="button"
                      className={cn(
                        conversationClasses,
                        c.mine && canAsk && !archived && "pe-8",
                      )}
                      aria-current={c.id === selected ? "true" : undefined}
                      disabled={running}
                      onClick={() => choose(c.id)}
                    >
                      <span className="min-w-0 truncate">{c.title}</span>
                      {!c.mine && (
                        <span className="shrink-0 text-xs text-subtle-foreground">
                          (shared)
                        </span>
                      )}
                    </button>
                    {/* Only who started a conversation may delete it. */}
                    {c.mine && canAsk && !archived && (
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        aria-label={`Delete ${c.title}`}
                        disabled={running}
                        className="absolute inset-y-0 end-1 my-auto size-6 opacity-0 group-hover/row:opacity-100 focus-visible:opacity-100 pointer-coarse:opacity-100 hover:text-danger-strong [&_svg]:size-3.5"
                        onClick={() => setDeleting({ id: c.id, title: c.title })}
                      >
                        <Trash2 aria-hidden />
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            )}
          </nav>
        </>
      )}
      <ConfirmDialog
        open={deleting !== null}
        onOpenChange={(open) => {
          if (!open) {
            setDeleting(null);
            remove.reset();
          }
        }}
        title="Delete conversation"
        description={
          <>
            Delete <strong className="font-medium">{deleting?.title}</strong>?
            It is removed for you and for every member it is shared with.
          </>
        }
        confirmLabel="Delete conversation"
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() => {
          if (!deleting) {
            return;
          }
          const id = deleting.id;
          remove.mutate(id, {
            onSuccess: () => {
              if (id === selected) {
                setChosen(null);
                setPicked(null);
              }
              setDeleting(null);
            },
          });
        }}
      />
    </section>
  );
}
