"use client";

import Link from "next/link";
import { useState } from "react";

import {
  ITEM_STATUS_LABELS,
  useAcceptItems,
  useChangeSet,
  useConversationChangeSets,
  useRejectItems,
  type ApplyResult,
  type ChangeSetItem,
} from "../api/changeSets";
import { ApiError } from "../api/client";
import { allows, useWorkspace, type Workspace } from "../api/workspaces";
import { Loading } from "../shell/Loading";
import { WorkspaceNotFound } from "./WorkspaceNotFound";

const OPEN = new Set<string>(["pending", "needs_owner"]);

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
    <ul className="change-diff">
      {fields.map((field) => (
        <li key={field}>
          <code>{field}</code>:{" "}
          {item.base_values && field in item.base_values && (
            <del>{show(item.base_values[field])}</del>
          )}{" "}
          <ins>{show(item.payload[field])}</ins>
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
    <div role="status" className="change-report">
      <p>
        {result.accepted.length === 1
          ? "1 item applied."
          : `${result.accepted.length} items applied.`}
      </p>
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
      <p role="alert">Could not load the Change Set: {detail.error.message}</p>
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
        setResult(await accept.mutateAsync(ids));
      } else {
        await reject.mutateAsync(ids);
      }
      setTicked(new Set());
    } catch (error) {
      fail(error);
    }
  }

  return (
    <article className="change-set" aria-label={changeSet.title}>
      <h4>
        {link ? (
          <Link
            href={`/workspaces/${workspace.id}/change-sets/${changeSet.id}`}
          >
            {changeSet.title}
          </Link>
        ) : (
          changeSet.title
        )}{" "}
        <span className={`status-badge change-set-${changeSet.status}`}>
          {changeSet.status.replace("_", " ")}
        </span>
      </h4>
      <ul className="change-items">
        {items.map((item) => (
          <li key={item.id} className={`change-item change-${item.status}`}>
            <label>
              {canDecide && OPEN.has(item.status) && (
                <input
                  type="checkbox"
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
            <span className="status-badge">
              {ITEM_STATUS_LABELS[item.status]}
            </span>
            {item.required_role === "owner" && item.status !== "accepted" && (
              <span className="status-badge"> owner only</span>
            )}
            {item.is_conflict && (
              <span className="status-badge"> conflict</span>
            )}
            <Diff item={item} />
            {item.status_reason && (
              <p className="assistant-note">{item.status_reason}</p>
            )}
          </li>
        ))}
      </ul>
      {canDecide && open.length > 0 && (
        <div className="form-actions">
          <button
            type="button"
            disabled={chosen.length === 0 || accept.isPending}
            onClick={() => decide("accept", chosen)}
          >
            Accept selected
          </button>
          <button
            type="button"
            disabled={accept.isPending}
            onClick={() => decide("accept")}
          >
            Accept all
          </button>
          <button
            type="button"
            disabled={chosen.length === 0 || reject.isPending}
            onClick={() => decide("reject", chosen)}
          >
            Reject selected
          </button>
          <button
            type="button"
            disabled={reject.isPending}
            onClick={() => decide("reject")}
          >
            Reject all
          </button>
        </div>
      )}
      {problem && (
        <p role="alert" className="form-error">
          {problem}
        </p>
      )}
      {result && <Report result={result} />}
    </article>
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
    <section aria-label="Proposed changes" className="change-sets">
      <h4>Proposed changes</h4>
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
    <div className="page">
      <p>
        <Link href={`/workspaces/${workspaceId}`}>{workspace.data.name}</Link>
      </p>
      <h2>Change Set</h2>
      <ChangeSetReview workspace={workspace.data} changeSetId={changeSetId} />
    </div>
  );
}
