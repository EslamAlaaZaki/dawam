"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  useSetWorkspaceAiSettings,
  useWorkspaceAiSettings,
  type WorkspaceAiSettings,
} from "../api/workspaceAi";
import { allows, type Workspace } from "../api/workspaces";
import { Badge } from "../components/ui/badge";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { Switch } from "../components/ui/switch";
import { Loading } from "../shell/Loading";
import {
  Actions,
  ErrorText,
  Hint,
  Panel,
  checkLabelClasses,
  h3Classes,
} from "./parts";

type Kind = "share_profiles" | "share_documents" | "share_samples";
type Shared = Record<Kind, boolean>;

const METADATA_TEXT = "Table and column names, types and keys.";

/** What an owner may let the AI see besides metadata, each an independent choice. */
const KINDS: { key: Kind; label: string; noun: string; text: string }[] = [
  {
    key: "share_profiles",
    label: "Profile statistics",
    noun: "profile statistics",
    text: "Null rates, ranges and value distributions of columns.",
  },
  {
    key: "share_documents",
    label: "Documents",
    noun: "documents",
    text: "The text of uploaded documents, for search and answers.",
  },
  {
    key: "share_samples",
    label: "Sample rows",
    noun: "sample rows",
    text: "Rows and most frequent values from the source data, and read-only queries against it.",
  },
];

/**
 * What the AI may see (spec, AI data exposure): metadata is always shared; profile
 * statistics, documents and sample rows are three independent opt-ins, off for a new
 * Workspace. Every member sees them; owners of an active Workspace change them.
 */
export function AiPrivacyPanel({ workspace }: { workspace: Workspace }) {
  const settings = useWorkspaceAiSettings(workspace.id);
  if (settings.isPending) {
    return <Loading />;
  }
  if (settings.isError) {
    return (
      <Panel aria-labelledby="ai-privacy-title" className="border-0 p-0">
        <h3 id="ai-privacy-title" className={h3Classes}>
          AI privacy
        </h3>
        <Hint>
          Could not load the AI privacy settings: {settings.error.message}
        </Hint>
      </Panel>
    );
  }
  return allows(workspace, "workspace.edit") &&
    workspace.status === "active" ? (
    <OwnerForm workspaceId={workspace.id} settings={settings.data} />
  ) : (
    <Panel aria-labelledby="ai-privacy-title" className="border-0 p-0">
      <h3 id="ai-privacy-title" className={h3Classes}>
        AI privacy
      </h3>
      <ul className="flex flex-col gap-2">
        <Row label="Metadata" text={METADATA_TEXT} fixed />
        {KINDS.map((k) => (
          <Row
            key={k.key}
            label={k.label}
            text={k.text}
            on={settings.data[k.key]}
          />
        ))}
      </ul>
      <Hint>
        {workspace.status === "active"
          ? "Only owners can change what the AI may see."
          : "The Workspace is archived, so what the AI may see cannot change."}
      </Hint>
    </Panel>
  );
}

function Row({
  label,
  text,
  on,
  fixed,
}: {
  label: string;
  text: string;
  on?: boolean;
  fixed?: boolean;
}) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border px-3 py-2">
      <span className="flex flex-col">
        <span className="text-sm font-medium">{label}</span>
        <span className="text-sm text-muted-foreground">{text}</span>
      </span>
      {fixed ? (
        <Badge variant="neutral">Always shared</Badge>
      ) : (
        <Badge variant={on ? "success" : "neutral"} dot>
          {on ? "Shared" : "Not shared"}
        </Badge>
      )}
    </li>
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
  const [shared, setShared] = useState<Shared>({
    share_profiles: settings.share_profiles,
    share_documents: settings.share_documents,
    share_samples: settings.share_samples,
  });
  const [asking, setAsking] = useState<Kind | null>(null);
  const asked = KINDS.find((k) => k.key === asking);

  function toggle(kind: Kind) {
    if (shared[kind]) {
      setShared({ ...shared, [kind]: false });
    } else {
      // Turning one on sends more data to the models, so it asks first.
      setAsking(kind);
    }
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    save
      .mutateAsync({ internal_only: settings.internal_only, ...shared })
      .then(
        () => toast.success("Saved."),
        () => {},
      );
  }

  return (
    <form
      className="flex max-w-xl flex-col gap-4"
      onSubmit={onSubmit}
      aria-labelledby="ai-privacy-title"
    >
      <h3 id="ai-privacy-title" className={h3Classes}>
        AI privacy
      </h3>
      <Hint>
        What the models may see, for every model role. Metadata is always
        shared; the rest is off until you turn it on. Changes are saved with
        Save.
      </Hint>
      <section aria-label="Metadata" className="flex flex-col gap-1">
        <span className="flex items-center gap-2 text-sm font-medium">
          Metadata
          <Badge variant="neutral">Always shared</Badge>
        </span>
        <Hint>{METADATA_TEXT}</Hint>
      </section>
      {KINDS.map((k) => (
        <section
          key={k.key}
          aria-label={k.label}
          className="flex flex-col gap-1"
        >
          <label className={checkLabelClasses}>
            <Switch
              checked={shared[k.key]}
              onCheckedChange={() => toggle(k.key)}
            />
            {k.label}
          </label>
          <Hint>{k.text}</Hint>
        </section>
      ))}
      <ConfirmDialog
        open={asking !== null}
        onOpenChange={(open) => !open && setAsking(null)}
        title={`Share ${asked?.noun ?? ""} with the AI?`}
        description={`The models will see ${asked?.noun ?? ""} of this Workspace, including external providers unless the Workspace is internal-only. The change is saved with Save.`}
        confirmLabel="Turn on"
        onConfirm={() => {
          if (asking) {
            setShared({ ...shared, [asking]: true });
          }
          setAsking(null);
        }}
      />
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
