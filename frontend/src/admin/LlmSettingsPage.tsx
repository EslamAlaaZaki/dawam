"use client";

import { Settings } from "lucide-react";
import { useState, type FormEvent } from "react";

import { useAdminWorkspaces } from "../api/adminWorkspaces";
import {
  useLlmProviders,
  useSetLlmModelRoles,
  type LlmModel,
  type LlmModelRole,
  type LlmProvider,
} from "../api/llm";
import {
  useClearWorkspaceBudget,
  useLlmBudgets,
  useLlmUsage,
  useSetInstallationBudget,
  useSetWorkspaceBudget,
  type LlmBudgets,
} from "../api/llmSettings";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { SettingsCard } from "../components/entity/SettingsCard";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";
import {
  ErrorText,
  NativeSelect,
  PageHead,
  TableCard,
  labelClasses,
  h3Classes,
} from "./parts";
import {
  ReplacementDialog,
  neededWorkspaces,
  type Candidate,
} from "./ReplacementDialog";

/** Model roles, monthly token budgets and AI usage (admins only). */
export function LlmSettingsPage() {
  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="AI roles, budgets and usage"
        description="Which roles each model may play, how many tokens each Workspace may use per month, and what was used. Each Workspace's owner chooses its models from these."
      />
      <ModelRolesSection />
      <BudgetsSection />
      <UsageSection />
    </section>
  );
}

const ROLE_COLUMNS: { role: LlmModelRole; label: string }[] = [
  { role: "agent", label: "Agent" },
  { role: "light", label: "Light" },
  { role: "embedding", label: "Embedding" },
];

const EMBEDDING_HINT =
  "Dedicated embedding models usually give better document search";

function testSummary(model: LlmModel): string {
  if (model.test_ok === false) {
    return `Failed (${model.test_error_code}): ${model.test_error}`;
  }
  if (model.test_ok) return "Passed";
  return "Not tested";
}

/** Every registered model with its three Model Role checkboxes (any combination). */
function ModelRolesSection() {
  const providers = useLlmProviders();
  if (providers.isPending) {
    return <Loading />;
  }
  if (providers.isError) {
    return <ErrorText>Could not load the models.</ErrorText>;
  }
  const rows = providers.data.flatMap((provider) =>
    provider.models.map((model) => ({ model, provider })),
  );
  return (
    <div className="flex flex-col gap-3.5">
      <h3 className={h3Classes}>Model roles</h3>
      <p className="text-sm text-sand-11">
        A model with no role is offered nowhere. Add models on the Language
        models page.
      </p>
      <TableCard>
        <Table aria-label="Model roles table">
          <TableHeader>
            <TableRow>
              <TableHead>Model</TableHead>
              <TableHead>Provider</TableHead>
              <TableHead>Last test</TableHead>
              {ROLE_COLUMNS.map(({ role, label }) => (
                <TableHead key={role}>{label}</TableHead>
              ))}
              <TableHead>
                <span className="sr-only">Save</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 && (
              <TableRow>
                <TableCell colSpan={7}>No models registered yet.</TableCell>
              </TableRow>
            )}
            {rows.map(({ model, provider }) => (
              <ModelRoleRow
                key={model.id}
                model={model}
                provider={provider}
                candidates={rows}
              />
            ))}
          </TableBody>
        </Table>
      </TableCard>
    </div>
  );
}

function ModelRoleRow({
  model,
  provider,
  candidates,
}: {
  model: LlmModel;
  provider: LlmProvider;
  candidates: Candidate[];
}) {
  const save = useSetLlmModelRoles(model.id);
  const [asking, setAsking] = useState(false);
  const needed = neededWorkspaces(save.error);
  const saved = () => {
    setAsking(false);
    toast.success(`Roles of ${model.name} saved.`);
  };
  const [roles, setRoles] = useState<LlmModelRole[]>(model.roles);
  const changed =
    ROLE_COLUMNS.some(({ role }) => roles.includes(role) !== model.roles.includes(role));
  const mixed =
    roles.includes("embedding") &&
    (roles.includes("agent") || roles.includes("light"));

  function toggle(role: LlmModelRole, on: boolean) {
    save.reset();
    setRoles(
      ROLE_COLUMNS.map((c) => c.role).filter((r) =>
        r === role ? on : roles.includes(r),
      ),
    );
  }

  return (
    <TableRow aria-label={`Roles of ${model.name}`}>
      <TableCell className="whitespace-normal">
        <strong>{model.name}</strong>
        {mixed && (
          <div role="note" className="text-sm text-sand-11">
            {EMBEDDING_HINT}
          </div>
        )}
        {save.isError && !asking && (
          <ErrorText>{save.error.message}</ErrorText>
        )}
      </TableCell>
      <TableCell>
        {provider.name}{" "}
        <Badge variant="neutral">
          {provider.internal ? "Internal" : "External"}
        </Badge>
      </TableCell>
      <TableCell className="whitespace-normal text-sand-11">
        {testSummary(model)}
      </TableCell>
      {ROLE_COLUMNS.map(({ role, label }) => (
        <TableCell key={role}>
          <input
            type="checkbox"
            aria-label={`${label} role of ${model.name}`}
            className="size-4 accent-[var(--accent-solid)]"
            checked={roles.includes(role)}
            onChange={(event) => toggle(role, event.target.checked)}
          />
        </TableCell>
      ))}
      <TableCell>
        <Button
          type="button"
          size="sm"
          disabled={!changed || save.isPending}
          onClick={() =>
            save.mutate(
              { roles },
              {
                onSuccess: saved,
                onError: (error) =>
                  setAsking(neededWorkspaces(error).length > 0),
              },
            )
          }
        >
          Save roles of {model.name}
        </Button>
        <ReplacementDialog
          open={asking}
          onOpenChange={(next) => {
            setAsking(next);
            if (!next) save.reset();
          }}
          title={`Replace ${model.name}?`}
          description="Some Workspaces depend on this model for a role you are removing. Choose what replaces it for each; it is dropped from the others."
          confirmLabel="Replace and save roles"
          workspaces={needed}
          candidates={candidates}
          excludeId={model.id}
          pending={save.isPending}
          error={save.error}
          onConfirm={(workspace_replacements) =>
            save.mutate(
              { roles, workspace_replacements },
              {
                onSuccess: saved,
                onError: (error) =>
                  setAsking(neededWorkspaces(error).length > 0),
              },
            )
          }
        />
      </TableCell>
    </TableRow>
  );
}

function BudgetsSection() {
  return (
    <div className="flex flex-col items-start gap-3.5">
      <h3 className={h3Classes}>Monthly token budgets</h3>
      <Dialog>
        <DialogTrigger asChild>
          <Button>
            <Settings aria-hidden />
            Budget settings
          </Button>
        </DialogTrigger>
        <DialogContent className="max-h-[min(720px,calc(100%-2rem))] w-[min(640px,calc(100%-2rem))] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Budget settings</DialogTitle>
            <DialogDescription>
              Monthly token budgets for the installation and per Workspace.
            </DialogDescription>
          </DialogHeader>
          <BudgetsPanel />
        </DialogContent>
      </Dialog>
    </div>
  );
}

function BudgetsPanel() {
  const budgets = useLlmBudgets();
  const workspaces = useAdminWorkspaces();
  const setInstallation = useSetInstallationBudget();
  const setWorkspace = useSetWorkspaceBudget();
  const clear = useClearWorkspaceBudget();
  const [removing, setRemoving] = useState<LlmBudgets["workspaces"][number] | null>(null);
  if (budgets.isPending) {
    return <Loading />;
  }
  if (budgets.isError) {
    return <ErrorText>Could not load the budgets.</ErrorText>;
  }

  function onInstallation(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = String(
      new FormData(event.currentTarget).get("tokens") ?? "",
    ).trim();
    setInstallation.mutate(value ? Number(value) : null);
  }

  function onWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setWorkspace.mutate({
      workspaceId: String(data.get("workspace")),
      tokens: Number(data.get("tokens")),
    });
  }

  const choices = workspaces.data?.pages.flatMap((page) => page.items) ?? [];
  const failure = setInstallation.error ?? setWorkspace.error;
  return (
    <div className="flex flex-col gap-3.5">
      <form onSubmit={onInstallation} aria-label="Installation budget">
        <SettingsCard
          title="Installation budget"
          footer={
            <Button type="submit" disabled={setInstallation.isPending}>
              Save installation budget
            </Button>
          }
        >
          <label className={labelClasses}>
            Installation (tokens per month; empty: unlimited)
            <Input
              name="tokens"
              type="number"
              min={0}
              key={budgets.data.installation_monthly_token_budget ?? "none"}
              defaultValue={budgets.data.installation_monthly_token_budget ?? ""}
            />
          </label>
        </SettingsCard>
      </form>
      {budgets.data.workspaces.length > 0 && (
        <ul
          aria-label="Workspace budgets"
          className="flex max-w-xl flex-col divide-y divide-sand-4 rounded-xl border border-sand-5 bg-sand-1"
        >
          {budgets.data.workspaces.map((budget) => (
            <li
              key={budget.workspace_id}
              className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
            >
              <span>
                {budget.workspace_name ?? budget.workspace_id}:{" "}
                {budget.monthly_token_budget.toLocaleString("en")} tokens
              </span>
              <Button
                type="button"
                size="sm"
                onClick={() => setRemoving(budget)}
              >
                Remove budget for {budget.workspace_name ?? budget.workspace_id}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={onWorkspace} aria-label="Workspace budget">
        <SettingsCard
          title="Workspace budget"
          footer={
            <Button type="submit" disabled={setWorkspace.isPending}>
              Set Workspace budget
            </Button>
          }
        >
          <label className={labelClasses}>
            Workspace
            <NativeSelect name="workspace" required defaultValue="">
              <option value="">Choose a Workspace</option>
              {choices.map((workspace) => (
                <option key={workspace.id} value={workspace.id}>
                  {workspace.name}
                </option>
              ))}
            </NativeSelect>
          </label>
          <label className={labelClasses}>
            Tokens per month
            <Input name="tokens" type="number" min={0} required />
          </label>
        </SettingsCard>
      </form>
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(next) => {
          if (!next) {
            clear.reset();
            setRemoving(null);
          }
        }}
        title={`Remove the budget for ${removing?.workspace_name ?? removing?.workspace_id}?`}
        description="The Workspace goes back to the installation budget."
        confirmLabel="Remove budget"
        pending={clear.isPending}
        error={clear.error}
        onConfirm={() =>
          removing &&
          clear.mutate(removing.workspace_id, { onSuccess: () => setRemoving(null) })
        }
      />
      {failure && <ErrorText>{failure.message}</ErrorText>}
    </div>
  );
}

function UsageSection() {
  const [month, setMonth] = useState("");
  const usage = useLlmUsage(month || undefined);
  return (
    <div className="flex flex-col gap-3.5">
      <h3 className={h3Classes}>Usage</h3>
      <label className={`${labelClasses} w-48`}>
        Month
        <Input
          type="month"
          value={month}
          onChange={(event) => setMonth(event.target.value)}
        />
      </label>
      {usage.isPending && <Loading />}
      {usage.isError && <ErrorText>Could not load the usage.</ErrorText>}
      {usage.data && (
        <>
          <p className="text-sand-11">
            {usage.data.month}:{" "}
            {usage.data.totals.total_tokens.toLocaleString("en")} tokens in{" "}
            {usage.data.totals.calls} calls
            {usage.data.installation_monthly_token_budget !== null &&
              ` (budget ${usage.data.installation_monthly_token_budget.toLocaleString("en")})`}
          </p>
          <TableCard>
            <Table aria-label="Usage per Workspace">
              <TableHeader>
                <TableRow>
                  <TableHead>Workspace</TableHead>
                  <TableHead>Tokens</TableHead>
                  <TableHead>Calls</TableHead>
                  <TableHead>Budget</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.data.workspaces.map((row) => (
                  <TableRow key={row.workspace_id ?? "none"}>
                    <TableCell>
                      {row.workspace_name ?? "No Workspace"}
                    </TableCell>
                    <TableCell>
                      {row.totals.total_tokens.toLocaleString("en")}
                    </TableCell>
                    <TableCell>{row.totals.calls}</TableCell>
                    <TableCell>
                      {row.monthly_token_budget?.toLocaleString("en") ?? "none"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
          <TableCard>
            <Table aria-label="Usage per user">
              <TableHeader>
                <TableRow>
                  <TableHead>User</TableHead>
                  <TableHead>Tokens</TableHead>
                  <TableHead>Calls</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.data.users.map((row) => (
                  <TableRow key={row.user_id ?? "none"}>
                    <TableCell>
                      {row.display_name ?? row.email ?? "System"}
                    </TableCell>
                    <TableCell>
                      {row.totals.total_tokens.toLocaleString("en")}
                    </TableCell>
                    <TableCell>{row.totals.calls}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableCard>
        </>
      )}
    </div>
  );
}
