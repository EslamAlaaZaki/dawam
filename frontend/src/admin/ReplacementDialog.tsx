import { useState, type ReactNode } from "react";

import { ApiError } from "../api/client";
import type { LlmModel, LlmModelRemoval } from "../api/llm";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { NativeSelect, labelClasses } from "./parts";

export type Role = LlmModel["roles"][number];
export type WorkspaceReplacement = NonNullable<
  LlmModelRemoval["workspace_replacements"]
>[number];

export const ROLE_LABELS: Record<Role, string> = {
  agent: "Agent",
  light: "Light",
  embedding: "Embedding",
  vision: "Vision",
  rerank: "Rerank",
};

/** Vision and rerank are optional: a Workspace losing either model needs no replacement. */
const REPLACEMENT_KEY: Partial<
  Record<Role, "agent_model_id" | "light_model_id" | "embedding_model_id">
> = {
  agent: "agent_model_id",
  light: "light_model_id",
  embedding: "embedding_model_id",
};

/** A Workspace where the model is the default or the only one for these roles. */
export interface AffectedWorkspace {
  id: string;
  name: string;
  roles: Role[];
  internal_only?: boolean;
}

export interface Candidate {
  model: LlmModel;
  provider: { internal: boolean };
}

/** The Workspaces a 409 asked a replacement for (`replacement_required` or
 * `replacement_not_allowed`), else none. */
export function neededWorkspaces(error: Error | null): AffectedWorkspace[] {
  if (
    !(error instanceof ApiError) ||
    (error.code !== "replacement_required" &&
      error.code !== "replacement_not_allowed")
  )
    return [];
  const list = (error.details as Record<string, unknown> | null | undefined)
    ?.workspaces;
  return Array.isArray(list) ? (list as AffectedWorkspace[]) : [];
}

/** The one replacement dialog behind unticking a Model Role and removing a model: a
 * picker per affected Workspace and role, offering the tested models that hold the role
 * (internal ones only for an internal-only Workspace), the Workspace's own remaining
 * models included. */
export function ReplacementDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  workspaces,
  candidates,
  excludeId,
  blocked,
  pending,
  error,
  onConfirm,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: ReactNode;
  confirmLabel: string;
  workspaces: AffectedWorkspace[];
  candidates: Candidate[];
  /** The model going away: never offered. */
  excludeId: string;
  /** Why it cannot go ahead at all, e.g. indexed documents; replaces the pickers. */
  blocked?: ReactNode;
  pending?: boolean;
  error?: Error | null;
  onConfirm: (replacements: WorkspaceReplacement[]) => void;
}) {
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const key = (w: AffectedWorkspace, role: Role) => `${w.id}:${role}`;
  const optionsFor = (w: AffectedWorkspace, role: Role) =>
    candidates.filter(
      (c) =>
        c.model.id !== excludeId &&
        c.model.test_ok === true &&
        c.model.roles.includes(role) &&
        (!w.internal_only || c.provider.internal),
    );
  const complete = workspaces.every((w) =>
    w.roles.every((role) => chosen[key(w, role)]),
  );

  const close = (next: boolean) => {
    if (!next) setChosen({});
    onOpenChange(next);
  };
  const confirm = () =>
    onConfirm(
      workspaces.map((w) => {
        const entry: WorkspaceReplacement = { workspace_id: w.id };
        for (const role of w.roles) {
          const field = REPLACEMENT_KEY[role];
          if (field) entry[field] = chosen[key(w, role)];
        }
        return entry;
      }),
    );

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={close}
      title={title}
      description={description}
      confirmLabel={confirmLabel}
      pending={pending}
      confirmDisabled={Boolean(blocked) || !complete}
      error={error}
      onConfirm={confirm}
    >
      {blocked}
      {!blocked &&
        workspaces.map((w) => (
          <fieldset key={w.id} className="flex flex-col gap-2">
            <legend className="text-sm font-medium">
              {w.name}
              {w.internal_only ? " (internal models only)" : ""}
            </legend>
            {w.roles.map((role) => (
              <label key={role} className={labelClasses}>
                {`${w.name}: ${ROLE_LABELS[role]} model`}
                <NativeSelect
                  value={chosen[key(w, role)] ?? ""}
                  onChange={(event) =>
                    setChosen({ ...chosen, [key(w, role)]: event.target.value })
                  }
                >
                  <option value="">Choose a model</option>
                  {optionsFor(w, role).map((c) => (
                    <option key={c.model.id} value={c.model.id}>
                      {c.model.name}
                    </option>
                  ))}
                </NativeSelect>
              </label>
            ))}
          </fieldset>
        ))}
    </ConfirmDialog>
  );
}
