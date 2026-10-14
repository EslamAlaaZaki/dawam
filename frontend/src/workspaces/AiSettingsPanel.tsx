"use client";

import type { FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  useSetWorkspaceAiSettings,
  useWorkspaceAiSettings,
  type WorkspaceAiSettings,
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
  formCardClasses,
  h3Classes,
  labelClasses,
} from "./parts";

const INSTALLATION = "";

/**
 * The Workspace's AI settings (stories 160, 161): the model each role uses. Every member
 * sees the effective models; owners choose the agent and light model from the Workspace's
 * Allowed Models, or fall back to the installation's.
 */
export function AiSettingsPanel({ workspace }: { workspace: Workspace }) {
  const settings = useWorkspaceAiSettings(workspace.id);
  if (settings.isPending) {
    return <Loading />;
  }
  if (settings.isError) {
    // Not an alert: the rest of the page works without it.
    return (
      <Panel aria-labelledby="ai-title">
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
    <Panel aria-labelledby="ai-title">
      <h3 id="ai-title" className={h3Classes}>
        AI settings
      </h3>
      <Effective settings={settings.data} />
      <Hint>Only owners can choose the models.</Hint>
    </Panel>
  );
}

function modelText(name: string | null | undefined, own: boolean): string {
  if (!name) {
    return "None assigned";
  }
  return own ? name : `${name} (installation default)`;
}

function Effective({ settings }: { settings: WorkspaceAiSettings }) {
  return (
    <DetailList>
      <dt>Agent model</dt>
      <dd>
        {modelText(
          settings.effective_agent_model_name,
          settings.agent_model_id !== null,
        )}
      </dd>
      <dt>Light model</dt>
      <dd>
        {settings.effective_light_model_name
          ? modelText(
              settings.effective_light_model_name,
              settings.light_model_id !== null,
            )
          : "None assigned: light tasks use the agent model"}
      </dd>
      <dt>Embedding model</dt>
      <dd>
        {settings.embedding_model_name ?? "None assigned"}{" "}
        <span className="text-muted-foreground">(fixed at creation)</span>
      </dd>
    </DetailList>
  );
}

function OwnerForm({
  workspaceId,
  settings,
}: {
  workspaceId: string;
  settings: WorkspaceAiSettings;
}) {
  const save = useSetWorkspaceAiSettings(workspaceId);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const pick = (field: string) => String(form.get(field)) || null;
    save
      .mutateAsync({
        internal_only: settings.internal_only,
        data_sharing_level: settings.data_sharing_level,
        agent_model_id: pick("agent"),
        light_model_id: pick("light"),
      })
      .then(
        () => toast.success("Saved."),
        () => {},
      );
  }

  return (
    <form
      // Saved settings refill the selects.
      key={`${settings.agent_model_id}/${settings.light_model_id}`}
      className={formCardClasses}
      onSubmit={onSubmit}
      aria-labelledby="ai-title"
    >
      <h3 id="ai-title" className={h3Classes}>
        AI settings
      </h3>
      <Effective settings={settings} />
      <label className={labelClasses}>
        Agent model
        <NativeSelect
          name="agent"
          defaultValue={settings.agent_model_id ?? INSTALLATION}
        >
          <option value={INSTALLATION}>Installation default</option>
          {settings.approved_models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} ({m.provider_name})
            </option>
          ))}
          <StaleOption
            id={settings.agent_model_id}
            listed={settings.approved_models}
            name={settings.effective_agent_model_name}
          />
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Light model
        <NativeSelect
          name="light"
          defaultValue={settings.light_model_id ?? INSTALLATION}
        >
          <option value={INSTALLATION}>Installation default</option>
          {settings.light_models.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name} ({m.provider_name})
            </option>
          ))}
          <StaleOption
            id={settings.light_model_id}
            listed={settings.light_models}
            name={settings.effective_light_model_name}
          />
        </NativeSelect>
      </label>
      <Hint>
        Only models in the Workspace&apos;s Allowed Models can be chosen.
      </Hint>
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

/** The saved choice when it is no longer offered, so Save keeps it unless the owner picks. */
function StaleOption({
  id,
  listed,
  name,
}: {
  id: string | null;
  listed: { id: string }[];
  name: string | null | undefined;
}) {
  if (id === null || listed.some((m) => m.id === id)) {
    return null;
  }
  return <option value={id}>{name ?? id} (no longer allowed)</option>;
}
