"use client";

import { useState, type FormEvent } from "react";

import { useReindexAllDocuments } from "../api/files";
import {
  useSetWorkspaceDocumentAi,
  useWorkspaceDocumentAi,
  type KnobInfo,
  type SettingValue,
  type WorkspaceDocumentAi,
} from "../api/documentAi";
import { allows, type Workspace } from "../api/workspaces";
import { KNOB_GROUPS, KnobControl, knobText } from "../admin/KnobControl";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { Loading } from "../shell/Loading";
import {
  Actions,
  DetailList,
  ErrorText,
  Hint,
  Panel,
  checkClasses,
  checkLabelClasses,
  h3Classes,
  h4Classes,
} from "./parts";

/**
 * The Workspace's Document AI settings (24.1): the chunking and retrieval knobs its owners
 * may override, within the bounds an admin set; every other knob inherits the installation's
 * value. Every member sees the effective values; owners of an active Workspace override them.
 */
export function DocumentAiPanel({ workspace }: { workspace: Workspace }) {
  const settings = useWorkspaceDocumentAi(workspace.id);
  if (settings.isPending) {
    return <Loading />;
  }
  if (settings.isError) {
    return (
      <Panel aria-labelledby="document-ai-title" className="border-0 p-0">
        <h3 id="document-ai-title" className={h3Classes}>
          Document AI
        </h3>
        <Hint>
          Could not load the Document AI settings: {settings.error.message}
        </Hint>
      </Panel>
    );
  }
  if (allows(workspace, "workspace.edit") && workspace.status === "active") {
    return <OwnerForm workspaceId={workspace.id} settings={settings.data} />;
  }
  return (
    <Panel aria-labelledby="document-ai-title" className="border-0 p-0">
      <h3 id="document-ai-title" className={h3Classes}>
        Document AI
      </h3>
      <DetailList>
        {settings.data.knobs.map((knob) => (
          <Effective key={knob.key} knob={knob} settings={settings.data} />
        ))}
      </DetailList>
      <Hint>
        {workspace.status === "active"
          ? "Only owners can override these settings."
          : "The Workspace is archived, so its settings cannot change."}
      </Hint>
    </Panel>
  );
}

function Effective({
  knob,
  settings,
}: {
  knob: KnobInfo;
  settings: WorkspaceDocumentAi;
}) {
  return (
    <>
      <dt>{knob.label}</dt>
      <dd>
        {knobText(knob, settings.effective[knob.key])}
        {knob.key in settings.overrides && (
          <span className="text-muted-foreground"> (this Workspace)</span>
        )}
      </dd>
    </>
  );
}

function OwnerForm({
  workspaceId,
  settings,
}: {
  workspaceId: string;
  settings: WorkspaceDocumentAi;
}) {
  const update = useSetWorkspaceDocumentAi(workspaceId);
  const [overrides, setOverrides] = useState<Record<string, SettingValue>>(
    settings.overrides,
  );
  const reindexAll = useReindexAllDocuments(workspaceId);
  const [reindex, setReindex] = useState(false);

  function toggle(knob: KnobInfo, on: boolean) {
    const next = { ...overrides };
    if (on) {
      next[knob.key] = settings.effective[knob.key] ?? knob.default;
    } else {
      delete next[knob.key];
    }
    setOverrides(next);
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    reindexAll.reset();
    const before = settings.effective;
    update.mutate(
      { version: settings.version, overrides },
      {
        onSuccess: (saved) => {
          toast.success("Document AI settings saved.");
          setReindex(
            saved.knobs.some(
              (k) => k.reindex && saved.effective[k.key] !== before[k.key],
            ),
          );
        },
      },
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      aria-labelledby="document-ai-title"
      className="flex max-w-xl flex-col gap-4"
    >
      <h3 id="document-ai-title" className={h3Classes}>
        Document AI
      </h3>
      <Hint>
        Override how this Workspace&apos;s documents are chunked and searched. A
        setting left alone uses the installation&apos;s value.
      </Hint>
      {KNOB_GROUPS.map((group) => {
        const knobs = settings.knobs.filter((k) => k.group === group.key);
        if (knobs.length === 0) {
          return null;
        }
        return (
          <section
            key={group.key}
            aria-label={group.title}
            className="flex flex-col gap-3"
          >
            <h4 className={h4Classes}>{group.title}</h4>
            {knobs.map((knob) => (
              <OverrideRow
                key={knob.key}
                knob={knob}
                settings={settings}
                value={overrides[knob.key]}
                onToggle={(on) => toggle(knob, on)}
                onChange={(v) => setOverrides({ ...overrides, [knob.key]: v })}
              />
            ))}
          </section>
        );
      })}
      {update.isError && <ErrorText>{update.error.message}</ErrorText>}
      {reindex && (
        <div className="flex flex-col gap-2 rounded-md border border-border px-3 py-2">
          <p role="status" className="text-sm text-muted-foreground">
            The new chunking applies to documents indexed from now on. Re-index
            the Workspace&apos;s documents to apply it to the others?
          </p>
          {reindexAll.isError && (
            <ErrorText>{reindexAll.error.message}</ErrorText>
          )}
          <div className="flex gap-2">
            <Button
              type="button"
              disabled={reindexAll.isPending}
              onClick={() =>
                reindexAll.mutate(undefined, {
                  onSuccess: () => {
                    toast.success("Re-indexing all documents.");
                    setReindex(false);
                  },
                })
              }
            >
              Re-index now
            </Button>
            <Button type="button" onClick={() => setReindex(false)}>
              Not now
            </Button>
          </div>
        </div>
      )}
      <Actions>
        <Button type="submit" variant="primary" disabled={update.isPending}>
          Save Document AI settings
        </Button>
      </Actions>
    </form>
  );
}

function OverrideRow({
  knob,
  settings,
  value,
  onToggle,
  onChange,
}: {
  knob: KnobInfo;
  settings: WorkspaceDocumentAi;
  value: SettingValue | undefined;
  onToggle: (on: boolean) => void;
  onChange: (value: SettingValue) => void;
}) {
  const overridden = value !== undefined;
  const bounds = settings.bounds[knob.key];
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border px-3 py-2">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <KnobControl
          knob={knob}
          value={
            overridden ? value : (settings.inherited[knob.key] ?? knob.default)
          }
          onChange={onChange}
          disabled={!overridden}
          min={bounds?.min}
          max={bounds?.max}
        />
        <label className={checkLabelClasses}>
          <input
            type="checkbox"
            className={checkClasses}
            checked={overridden}
            onChange={(e) => onToggle(e.target.checked)}
            aria-label={`Override ${knob.label}`}
          />
          Override
        </label>
      </div>
      <p className="text-sm text-muted-foreground">
        {knob.description}
        {bounds && ` Allowed: ${bounds.min} to ${bounds.max}.`}
      </p>
      {!overridden && (
        <Badge variant="neutral" className="self-start">
          Installation value: {knobText(knob, settings.inherited[knob.key])}
        </Badge>
      )}
    </div>
  );
}
