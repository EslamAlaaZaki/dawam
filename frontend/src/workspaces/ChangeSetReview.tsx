"use client";

import Link from "next/link";
import { useState, type ComponentProps } from "react";

import {
  ITEM_STATUS_LABELS,
  useAcceptItems,
  useChangeSet,
  useConversationChangeSets,
  useRejectItems,
  type ApplyResult,
  type ChangeSetItem,
  type ItemStatus,
} from "../api/changeSets";
import { ApiError } from "../api/client";
import { allows, useWorkspace, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { Loading } from "../shell/Loading";
import {
  Actions,
  ErrorText,
  Hint,
  PageHead,
  checkClasses,
  h4Classes,
  linkClasses,
  Panel,
} from "./parts";
import { WorkspaceNotFound } from "./WorkspaceNotFound";

const OPEN = new Set<string>(["pending", "needs_owner"]);

const ITEM_VARIANTS: Record<ItemStatus, ComponentProps<typeof Badge>["variant"]> = {
  pending: "neutral",
  needs_owner: "warning",
  accepted: "success",
  rejected: "danger",
  stale: "warning",
  expired: "neutral",
};

const SET_VARIANTS = {
  pending: "accent",
  applied: "success",
  partially_applied: "warning",
  rejected: "danger",
  superseded: "neutral",
} as const;

function show(value: unknown): string {
  return value === null || value === undefined
    ? "(none)"
    : typeof value === "string"
      ? value
      : JSON.stringify(value);
}

/** What an item changes, field by field: the value when it was proposed, then the new one. */
function Diff({ item }: { item: ChangeSetItem }) {
  const fields = Object.keys(item.payload);
  return (
    <ul className="mt-1.5 ml-6 flex flex-col gap-0.5 text-mono [overflow-wrap:anywhere]">
      {fields.map((field) => (
        <li key={field}>
          <code className="font-mono">{field}</code>:{" "}
          {item.base_values && field in item.base_values && (
            <del className="text-red-11">{show(item.base_values[field])}</del>
          )}{" "}
          <ins className="text-grass-11 no-underline">{show(item.payload[field])}</ins>
        </li>
      ))}
    </ul>
  );
}

function Report({ result }: { result: ApplyResult }) {
  const names = new Map(
    result.change_set.items.map((i) => [i.id, i.label || i.object_type]),
  );
  return (
    <div
      role="status"
      className="flex flex-col gap-1.5 rounded-lg border border-sand-5 bg-sand-2 p-3 text-sm"
    >
      {result.skipped.length > 0 && (
        <>
          <p>Not applied, because something changed since the proposal:</p>
          <ul>
            {result.skipped.map((s) => (
              <li key={s.item_id}>
                {names.get(s.item_id)}: {s.detail}
              </li>
            ))}
          </ul>
        </>
      )}
      {result.needs_owner.length > 0 && (
        <p>
          {result.needs_owner.length === 1
            ? "1 item needs an owner to accept it."
            : `${result.needs_owner.length} items need an owner to accept them.`}{" "}
          The owners were notified.
        </p>
      )}
    </div>
  );
}

/**
 * One Change Set as a diff the editor reviews (spec stories 147, 148): tick the items,
 * accept or reject them, or decide on all that are open. Accepting an item accepts what it
 * depends on; the report says what was skipped as stale and what waits for an owner.
 */
export function ChangeSetReview({
  workspace,
  changeSetId,
  link = false,
}: {
  workspace: Workspace;
  changeSetId: string;
  /** Show a link to the Change Set's own page (in chat). */
  link?: boolean;
}) {
  const detail = useChangeSet(workspace.id, changeSetId);
  const accept = useAcceptItems(workspace.id, changeSetId);
  const reject = useRejectItems(workspace.id, changeSetId);
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<ApplyResult | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  if (detail.isPending) {
    return <Loading />;
  }
  if (detail.isError) {
    return (
      <ErrorText>Could not load the Change Set: {detail.error.message}</ErrorText>
    );
  }
  const { change_set: changeSet, items } = detail.data;
  const canDecide =
    allows(workspace, "change_set.review") && workspace.status === "active";
  const open = items.filter((i) => OPEN.has(i.status));
  const chosen = [...ticked].filter((id) => open.some((i) => i.id === id));

  function fail(error: unknown) {
    setProblem(
      error instanceof ApiError ? error.message : "That did not work.",
    );
  }
  async function decide(verb: "accept" | "reject", ids?: string[]) {
    setProblem(null);
    setResult(null);
    try {
      if (verb === "accept") {
        const applied = await accept.mutateAsync(ids);
        const count = applied.accepted.length;
        toast.success(count === 1 ? "1 item applied." : `${count} items applied.`);
        // Only what needs the reviewer's attention stays on the page.
        setResult(
          applied.skipped.length > 0 || applied.needs_owner.length > 0
            ? applied
            : null,
        );
      } else {
        await reject.mutateAsync(ids);
      }
      setTicked(new Set());
    } catch (error) {
      fail(error);
    }
  }

  return (
    <Panel aria-label={changeSet.title} className="p-4">
      <h4 className={`${h4Classes} flex flex-wrap items-center gap-2`}>
        {link ? (
          <Link
            href={`/workspaces/${workspace.id}/change-sets/${changeSet.id}`}
            className={linkClasses}
          >
            {changeSet.title}
          </Link>
        ) : (
          changeSet.title
        )}{" "}
        <Badge variant={SET_VARIANTS[changeSet.status]} dot>
          {changeSet.status.replace("_", " ")}
        </Badge>
      </h4>
      <ul className="flex flex-col divide-y divide-sand-4 rounded-lg border border-sand-5">
        {items.map((item) => (
          <li key={item.id} className="px-3.5 py-2.5">
            <label className="inline-flex items-center gap-2">
              {canDecide && OPEN.has(item.status) && (
                <input
                  type="checkbox"
                  className={checkClasses}
                  checked={ticked.has(item.id)}
                  aria-label={`Select ${item.label || item.object_type}`}
                  onChange={(event) => {
                    const next = new Set(ticked);
                    if (event.target.checked) {
                      next.add(item.id);
                    } else {
                      next.delete(item.id);
                    }
                    setTicked(next);
                  }}
                />
              )}{" "}
              <strong>{item.label || item.object_type}</strong>
            </label>{" "}
            <span className="inline-flex flex-wrap gap-1.5 align-middle">
              <Badge variant={ITEM_VARIANTS[item.status]} dot>
                {ITEM_STATUS_LABELS[item.status]}
              </Badge>
              {item.required_role === "owner" && item.status !== "accepted" && (
                <Badge variant="accent">owner only</Badge>
              )}
              {item.is_conflict && <Badge variant="danger">conflict</Badge>}
            </span>
            <Diff item={item} />
            {item.status_reason && (
              <Hint className="mt-1">{item.status_reason}</Hint>
            )}
          </li>
        ))}
      </ul>
      {canDecide && open.length > 0 && (
        <Actions>
          <Button
            type="button"
            size="sm"
            disabled={chosen.length === 0 || accept.isPending}
            onClick={() => decide("accept", chosen)}
          >
            Accept selected
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={accept.isPending}
            onClick={() => decide("accept")}
          >
            Accept all
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={chosen.length === 0 || reject.isPending}
            onClick={() => decide("reject", chosen)}
          >
            Reject selected
          </Button>
          <Button
            type="button"
            size="sm"
            disabled={reject.isPending}
            onClick={() => decide("reject")}
          >
            Reject all
          </Button>
        </Actions>
      )}
      {problem && <ErrorText>{problem}</ErrorText>}
      {result && <Report result={result} />}
    </Panel>
  );
}

/** The Change Sets proposed in a chat, reviewable right there. */
export function ConversationChangeSets({
  workspace,
  conversationId,
  messageCount,
}: {
  workspace: Workspace;
  conversationId: string;
  /** Changes whenever the conversation does, so new proposals are fetched. */
  messageCount: number;
}) {
  const sets = useConversationChangeSets(
    workspace.id,
    conversationId,
    messageCount,
  );
  if (!sets.isSuccess || sets.data.length === 0) {
    return null;
  }
  return (
    <section aria-label="Proposed changes" className="flex flex-col gap-3">
      <h4 className={h4Classes}>Proposed changes</h4>
      {sets.data.map((set) => (
        <ChangeSetReview
          key={set.id}
          workspace={workspace}
          changeSetId={set.id}
          link
        />
      ))}
    </section>
  );
}

/** A Change Set's own page. */
export function ChangeSetPage({
  workspaceId,
  changeSetId,
}: {
  workspaceId: string;
  changeSetId: string;
}) {
  const workspace = useWorkspace(workspaceId);
  if (workspace.isPending) {
    return <Loading />;
  }
  if (workspace.isError) {
    return <WorkspaceNotFound />;
  }
  return (
    <div className="flex max-w-3xl flex-col gap-3">
      <PageHead
        title="Change Set"
        description={
          <Link href={`/workspaces/${workspaceId}`} className={linkClasses}>
            {workspace.data.name}
          </Link>
        }
      />
      <ChangeSetReview workspace={workspace.data} changeSetId={changeSetId} />
    </div>
  );
}
