"use client";

import type { PageContext } from "../api/assistant";
import { useFailedChecks, type FailedCheck } from "../api/score";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Loading } from "../shell/Loading";
import { FixImpacts } from "./FixImpacts";
import {
  EmptyState,
  ErrorText,
  Panel,
  h3Classes,
  listCardClasses,
  monoClasses,
} from "./parts";

const SEVERITY_VARIANTS = {
  error: "danger",
  warning: "warning",
  info: "neutral",
} as const;

/** Checks a propagation fixes: a mapping reading an input renamed, deleted or removed. */
export const IMPACT_CHECKS = new Set([
  "inputs_exist",
  "no_source_removed_inputs",
]);

/** A question for the assistant, with the object it is about. */
export interface AssistantRequest {
  /** A new value each time, so the same question can be asked again. */
  nonce: number;
  prompt: string;
  context: PageContext;
}

export function explainPrompt(check: FailedCheck): string {
  return `Explain the failed check "${check.title}" on ${check.object_name}: what it measures and why it failed.`;
}

export function fixPrompt(check: FailedCheck): string {
  return `Propose a fix for the failed check "${check.title}" on ${check.object_name} as a Change Set.`;
}

/**
 * The failed score checks, each with an Explain action (every member) and a Propose fix
 * action (editors), which open the assistant with the check as context. When a check
 * fails because of a change's impacts, editors are offered "Fix impacts".
 */
export function FailedChecksPanel({
  workspace,
  onAsk,
}: {
  workspace: Workspace;
  onAsk: (request: Omit<AssistantRequest, "nonce">) => void;
}) {
  const checks = useFailedChecks(workspace.id);
  const canAsk =
    allows(workspace, "assistant.ask") && workspace.status !== "archived";
  const canReview =
    allows(workspace, "change_set.review") && workspace.status !== "archived";
  const canPropose = canAsk && canReview;

  if (checks.isPending) {
    return <Loading />;
  }
  if (checks.isError) {
    return <ErrorText>Could not load the failed checks.</ErrorText>;
  }
  const context = (check: FailedCheck): PageContext => ({
    type: "failed_check",
    id: check.object_id,
    label: `${check.check_code}: ${check.object_name}`,
  });

  return (
    <Panel aria-labelledby="checks-title">
      <h3 id="checks-title" className={h3Classes}>
        Failed checks
      </h3>
      {canReview &&
        checks.data.some((check) => IMPACT_CHECKS.has(check.check_code)) && (
          <FixImpacts workspace={workspace} />
        )}
      {checks.data.length === 0 ? (
        <EmptyState>No check fails.</EmptyState>
      ) : (
        <ul className={listCardClasses}>
          {checks.data.map((check) => (
            <li
              key={`${check.check_code}:${check.object_id}`}
              className="flex flex-wrap items-center gap-x-2 gap-y-1.5 border-l-2 border-warning bg-warning-subtle px-3.5 py-2.5"
            >
              <strong className="text-warning-strong">{check.title}</strong>
              {check.severity && (
                <Badge variant={SEVERITY_VARIANTS[check.severity]}>
                  {check.severity}
                </Badge>
              )}{" "}
              on <span className={monoClasses}>{check.object_name}</span>:{" "}
              <span className="min-w-0 flex-1 text-muted-foreground">
                {check.message}
              </span>
              {canAsk && (
                <>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() =>
                      onAsk({
                        prompt: explainPrompt(check),
                        context: context(check),
                      })
                    }
                  >
                    Explain
                  </Button>
                </>
              )}
              {canPropose && (
                <>
                  <Button
                    type="button"
                    size="sm"
                    onClick={() =>
                      onAsk({
                        prompt: fixPrompt(check),
                        context: context(check),
                      })
                    }
                  >
                    Propose fix
                  </Button>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
