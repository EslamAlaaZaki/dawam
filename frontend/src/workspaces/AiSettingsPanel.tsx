"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  useSetWorkspaceAiSettings,
  useWorkspaceAiSettings,
  type ModelChoice,
  type WorkspaceAiSettings,
  type WorkspaceModel,
} from "../api/workspaceAi";
import { allows, type Workspace } from "../api/workspaces";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { Loading } from "../shell/Loading";
import {
  Actions,
  DetailList,
  ErrorText,
  Hint,
  NativeSelect,
  Panel,
  checkClasses,
  checkLabelClasses,
  fieldsetClasses,
  h3Classes,
  h4Classes,
  labelClasses,
} from "./parts";

const NONE = "";

/** A settings section inside the dialog: the dialog is the card, so no border of its own. */
const dialogFormClasses = "flex max-w-xl flex-col gap-4";


/**
 * The Workspace's AI settings (stories 160, 161): its Workspace Models. Every member sees
 * them; owners of an active Workspace choose one or more agent and light models (one of
 * each the default) and the embedding model, from the models an admin enabled per role.
 */
export function AiSettingsPanel({ workspace }: { workspace: Workspace }) {
  const settings = useWorkspaceAiSettings(workspace.id);
  if (settings.isPending) {
    return <Loading />;
  }
  if (settings.isError) {
    // Not an alert: the rest of the page works without it.
    return (
      <Panel aria-labelledby="ai-title" className="border-0 p-0">
        <h3 id="ai-title" className={h3Classes}>
          AI settings
        </h3>
        <Hint>Could not load the AI settings: {settings.error.message}</Hint>
      </Panel>
    );
  }
  return allows(workspace, "workspace.edit") &&
    workspace.status === "active" ? (
    <OwnerForm workspaceId={workspace.id} settings={settings.data} />
  ) : (
    <Panel aria-labelledby="ai-title" className="border-0 p-0">
      <h3 id="ai-title" className={h3Classes}>
        AI settings
      </h3>
      <Chosen settings={settings.data} />
      <Hint>
        {workspace.status === "active"
          ? "Only owners can choose the models."
          : "The Workspace is archived, so its models cannot change."}
      </Hint>
    </Panel>
  );
}

function names(models: WorkspaceModel[]): string {
  return models
    .map((m) => (m.is_default ? `${m.name} (default)` : m.name))
    .join(", ");
}

function Chosen({ settings }: { settings: WorkspaceAiSettings }) {
  return (
    <DetailList>
      <dt>Agent models</dt>
      <dd>{names(settings.agent_models) || "None chosen"}</dd>
      <dt>Light models</dt>
      <dd>
        {names(settings.light_models) ||
          "None: light tasks use the default agent model"}
      </dd>
      <dt>Embedding model</dt>
      <dd>
        {settings.embedding_model?.name ??
          "None: document search matches text only"}
        {settings.embedding_locked && (
          <span className="text-muted-foreground">
            {" "}
            (fixed: documents are indexed)
          </span>
        )}
      </dd>
    </DetailList>
  );
}

type Pick = { ids: string[]; defaultId: string | null };

function initial(models: WorkspaceModel[]): Pick {
  return {
    ids: models.map((m) => m.id),
    defaultId: models.find((m) => m.is_default)?.id ?? null,
  };
}

/** What an owner may choose for a role, plus chosen models no longer offered. */
function offered(
  settings: WorkspaceAiSettings,
  choices: ModelChoice[],
  chosen: WorkspaceModel[],
): { model: ModelChoice; stale: boolean }[] {
  const usable = choices.filter((m) => m.internal || !settings.internal_only);
  const stale = chosen.filter((c) => !usable.some((m) => m.id === c.id));
  return [
    ...usable.map((model) => ({ model, stale: false })),
    ...stale.map((model) => ({ model, stale: true })),
  ];
}

function OwnerForm({
  workspaceId,
  settings,
}: {
  workspaceId: string;
  settings: WorkspaceAiSettings;
}) {
  const save = useSetWorkspaceAiSettings(workspaceId);
  const [agent, setAgent] = useState(() => initial(settings.agent_models));
  const [light, setLight] = useState(() => initial(settings.light_models));
  const [embedding, setEmbedding] = useState(
    settings.embedding_model?.id ?? NONE,
  );
  const embeddingChoices = offered(
    settings,
    settings.choices.embedding,
    settings.embedding_model ? [settings.embedding_model] : [],
  );

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save
      .mutateAsync({
        internal_only: settings.internal_only,
        data_sharing_level: settings.data_sharing_level,
        agent_model_ids: agent.ids,
        default_agent_model_id: agent.defaultId,
        light_model_ids: light.ids,
        default_light_model_id: light.defaultId,
        embedding_model_id: embedding || null,
      })
      .then(
        () => toast.success("Saved."),
        () => {},
      );
  }

  return (
    <form
      className={dialogFormClasses}
      onSubmit={onSubmit}
      aria-labelledby="ai-title"
    >
      <h3 id="ai-title" className={h3Classes}>
        AI settings
      </h3>
      <RoleModels
        role="agent"
        title="Agent models"
        rows={offered(settings, settings.choices.agent, settings.agent_models)}
        pick={agent}
        onChange={setAgent}
      />
      <RoleModels
        role="light"
        title="Light models"
        rows={offered(settings, settings.choices.light, settings.light_models)}
        pick={light}
        onChange={setLight}
        none="None chosen: light tasks use the default agent model."
      />
      <label className={labelClasses}>
        Embedding model
        <NativeSelect
          value={embedding}
          disabled={settings.embedding_locked}
          onChange={(event) => setEmbedding(event.target.value)}
        >
          <option value={NONE}>None: document search matches text only</option>
          {embeddingChoices.map(({ model, stale }) => (
            <option key={model.id} value={model.id}>
              {model.name} ({model.provider_name})
              {stale ? " (no longer offered)" : ""}
            </option>
          ))}
        </NativeSelect>
      </label>
      {settings.embedding_locked && (
        <Hint>
          Fixed: this Workspace has indexed documents, and changing the model
          would need them indexed again.
        </Hint>
      )}
      {settings.internal_only && (
        <Hint>
          The Workspace is internal-only: only internal models are offered.
        </Hint>
      )}
      {save.isError && (
        <ErrorText>
          {save.error instanceof ApiError
            ? save.error.message
            : "Could not save."}
        </ErrorText>
      )}
      <Actions>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          Save
        </Button>
      </Actions>
    </form>
  );
}

/** One role's models: a checkbox per model and a radio marking the default. */
function RoleModels({
  role,
  title,
  rows,
  pick,
  onChange,
  none,
}: {
  role: "agent" | "light";
  title: string;
  rows: { model: ModelChoice; stale: boolean }[];
  pick: Pick;
  onChange: (next: Pick) => void;
  none?: string;
}) {
  function toggle(id: string, on: boolean) {
    const ids = on ? [...pick.ids, id] : pick.ids.filter((x) => x !== id);
    const keep = pick.defaultId !== null && ids.includes(pick.defaultId);
    onChange({ ids, defaultId: keep ? pick.defaultId : (ids[0] ?? null) });
  }

  return (
    <fieldset className={fieldsetClasses}>
      <legend className={h4Classes}>{title}</legend>
      {rows.length === 0 && <Hint>No model is enabled for this role.</Hint>}
      {rows.map(({ model, stale }) => {
        const chosen = pick.ids.includes(model.id);
        return (
          <div
            key={model.id}
            className="flex flex-wrap items-center justify-between gap-4 rounded-md border border-border px-3 py-2"
          >
            <label className={checkLabelClasses}>
              <input
                type="checkbox"
                className={checkClasses}
                checked={chosen}
                onChange={(event) => toggle(model.id, event.target.checked)}
              />
              {model.name} ({model.provider_name})
              {stale ? " (no longer offered)" : ""}
            </label>
            <label className={checkLabelClasses}>
              <input
                type="radio"
                name={`default-${role}`}
                className={checkClasses}
                aria-label={`Default ${role} model: ${model.name}`}
                checked={pick.defaultId === model.id}
                disabled={!chosen}
                onChange={() => onChange({ ...pick, defaultId: model.id })}
              />
              Default
            </label>
          </div>
        );
      })}
      {none && pick.ids.length === 0 && <Hint>{none}</Hint>}
    </fieldset>
  );
}
