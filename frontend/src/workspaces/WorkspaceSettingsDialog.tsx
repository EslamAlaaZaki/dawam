"use client";

import { Settings } from "lucide-react";
import { type FormEvent } from "react";

import { ApiError } from "../api/client";
import {
  allows,
  useUpdateWorkspace,
  type Workspace,
} from "../api/workspaces";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../components/ui/dialog";
import { toast } from "../components/ui/sonner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "../components/ui/tabs";
import { AiSettingsPanel } from "./AiSettingsPanel";
import { DetailsFields, readDetails } from "./DetailsFields";
import { DocumentAiPanel } from "./DocumentAiPanel";
import { LifecyclePanel } from "./LifecyclePanel";
import {
  Actions,
  DetailList,
  ErrorText,
  Hint,
  Panel,
  h3Classes,
} from "./parts";

/** A settings section inside the dialog: the dialog is the card, so no border of its own. */
const dialogFormClasses = "flex max-w-xl flex-col gap-4";

const tabClasses =
  "justify-start whitespace-nowrap rounded-md border-b-0 px-3 py-1.5 text-left md:w-full data-[state=active]:bg-primary-subtle data-[state=active]:text-primary";

/**
 * The Workspace's settings: Details, AI settings and Archive & delete, in a dialog with
 * a tab sidebar (a top row at phone width). Every member can open it; the panels keep
 * their own read-only and permission behaviour, and Archive & delete shows only to those
 * the API lets archive, unarchive or delete.
 */
export function WorkspaceSettingsDialog({
  workspace,
  reload,
}: {
  workspace: Workspace;
  reload: () => void;
}) {
  const showLifecycle =
    allows(workspace, "workspace.archive") ||
    allows(workspace, "workspace.unarchive") ||
    allows(workspace, "workspace.delete");

  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button>
          <Settings aria-hidden />
          Workspace settings
        </Button>
      </DialogTrigger>
      <DialogContent className="h-[min(640px,calc(100%-2rem))] w-[min(860px,calc(100%-2rem))] gap-3 overflow-hidden">
        <DialogHeader>
          <DialogTitle>Workspace settings</DialogTitle>
          <DialogDescription className="sr-only">
            Details, AI settings and, for those allowed, archiving and deleting
            this Workspace.
          </DialogDescription>
        </DialogHeader>
        <Tabs
          defaultValue="details"
          orientation="vertical"
          className="min-h-0 flex-1 gap-3 md:flex-row md:gap-5"
        >
          <TabsList
            aria-label="Workspace settings sections"
            className="flex-none gap-1 overflow-x-auto border-b px-0 pb-2 md:w-44 md:flex-col md:border-r md:border-b-0 md:pr-4 md:pb-0"
          >
            <TabsTrigger value="details" className={tabClasses}>
              Details
            </TabsTrigger>
            <TabsTrigger value="ai" className={tabClasses}>
              AI
            </TabsTrigger>
            <TabsTrigger value="document-ai" className={tabClasses}>
              Document AI
            </TabsTrigger>
            {showLifecycle && (
              <TabsTrigger value="lifecycle" className={tabClasses}>
                Archive &amp; delete
              </TabsTrigger>
            )}
          </TabsList>
          <div className="min-h-0 min-w-0 flex-1 overflow-y-auto">
            <TabsContent value="details">
              <WorkspaceDetails workspace={workspace} reload={reload} />
            </TabsContent>
            <TabsContent value="ai">
              <AiSettingsPanel workspace={workspace} />
            </TabsContent>
            <TabsContent value="document-ai">
              <DocumentAiPanel workspace={workspace} />
            </TabsContent>
            {showLifecycle && (
              <TabsContent value="lifecycle">
                <LifecyclePanel workspace={workspace} />
              </TabsContent>
            )}
          </div>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}

function WorkspaceDetails({
  workspace,
  reload,
}: {
  workspace: Workspace;
  reload: () => void;
}) {
  const update = useUpdateWorkspace(workspace.id);

  if (!allows(workspace, "workspace.edit")) {
    return (
      <Panel aria-labelledby="details-title" className="border-0 p-0">
        <h3 id="details-title" className={h3Classes}>
          Details
        </h3>
        <DetailList>
          <dt>Description</dt>
          <dd>{workspace.description || "—"}</dd>
          <dt>Business domain</dt>
          <dd>{workspace.domain || "—"}</dd>
        </DetailList>
        <Hint>
          {workspace.status === "archived"
            ? "This Workspace is archived, so its details cannot be edited."
            : "Only owners can edit these details."}
        </Hint>
      </Panel>
    );
  }

  const conflict =
    update.error instanceof ApiError && update.error.status === 409;

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    // The form remounts on the new version, so a callback on mutate() would be dropped.
    update
      .mutateAsync({
        version: workspace.version,
        ...readDetails(event.currentTarget),
      })
      .then(
        () => toast.success("Saved."),
        () => {},
      );
  }

  return (
    // A new version (saved, or reloaded) refills the inputs.
    <form
      key={workspace.version}
      className={dialogFormClasses}
      onSubmit={onSubmit}
      aria-labelledby="details-title"
    >
      <h3 id="details-title" className={h3Classes}>
        Details
      </h3>
      <DetailsFields initial={workspace} />
      {conflict ? (
        <div
          className="flex flex-col items-start gap-2 text-danger-strong"
          role="alert"
        >
          <p>
            Someone else changed this Workspace since you opened it. Reload it
            to see their changes, then edit again.
          </p>
          <Button
            type="button"
            onClick={() => {
              update.reset();
              reload();
            }}
          >
            Reload
          </Button>
        </div>
      ) : (
        update.isError && <ErrorText>{update.error.message}</ErrorText>
      )}
      <Actions>
        <Button type="submit" variant="primary" disabled={update.isPending}>
          Save
        </Button>
      </Actions>
    </form>
  );
}
