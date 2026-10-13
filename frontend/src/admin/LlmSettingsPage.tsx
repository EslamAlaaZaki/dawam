"use client";

import { useState, type FormEvent } from "react";

import { useAdminWorkspaces } from "../api/adminWorkspaces";
import { useLlmProviders, type LlmModel } from "../api/llm";
import {
  useClearWorkspaceBudget,
  useLlmBudgets,
  useLlmRoles,
  useLlmUsage,
  useSetInstallationBudget,
  useSetLlmRoles,
  useSetWorkspaceBudget,
  type LlmBudgets,
} from "../api/llmSettings";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { SettingsCard } from "../components/entity/SettingsCard";
import { Button } from "../components/ui/button";
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

/** Model roles, monthly token budgets and AI usage (admins only). */
export function LlmSettingsPage() {
  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="AI roles, budgets and usage"
        description="Which models play which role, how many tokens each Workspace may use per month, and what was used."
      />
      <RolesSection />
      <BudgetsSection />
      <UsageSection />
    </section>
  );
}

function ModelSelect({
  label,
  name,
  models,
  value,
  required,
}: {
  label: string;
  name: string;
  models: { id: string; label: string }[];
  value: string | null;
  required?: boolean;
}) {
  return (
    <label className={labelClasses}>
      {label}
      <NativeSelect name={name} defaultValue={value ?? ""} required={required}>
        {!required && <option value="">None</option>}
        {required && <option value="">Choose a model</option>}
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </NativeSelect>
    </label>
  );
}

function RolesSection() {
  const roles = useLlmRoles();
  const providers = useLlmProviders();
  const save = useSetLlmRoles();
  if (roles.isPending || providers.isPending) {
    return <Loading />;
  }
  if (roles.isError || providers.isError) {
    return <ErrorText>Could not load the model roles.</ErrorText>;
  }
  // Only models that passed "Test connection" can be assigned.
  const tested = providers.data.flatMap((provider) =>
    provider.models
      .filter((model: LlmModel) => model.test_ok === true)
      .map((model: LlmModel) => ({
        id: model.id,
        label: `${provider.name} / ${model.name}`,
        embedding: model.roles.includes("embedding"),
      })),
  );
  const chat = tested.filter((model) => !model.embedding);
  const embedding = tested.filter((model) => model.embedding);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    save.mutate(
      {
        agent_model_id: String(data.get("agent")),
        light_model_id: String(data.get("light") ?? "") || null,
        embedding_model_id: String(data.get("embedding") ?? "") || null,
      },
      { onSuccess: () => toast.success("Roles saved.") },
    );
  }

  return (
    <form onSubmit={onSubmit} aria-label="Model roles">
      <SettingsCard
        title="Model roles"
        description='Only models that passed "Test connection" are offered.'
        footer={
          <Button type="submit" variant="primary" disabled={save.isPending}>
            Save roles
          </Button>
        }
      >
        {roles.data.reindex_needed && (
          <p
            role="status"
            className="rounded-lg border border-amber-11/40 bg-amber-3 px-3 py-2.5 text-sm"
          >
            Documents must be indexed again: the embedding model or its vector
            dimension changed.
          </p>
        )}
        <ModelSelect
          label="Agent model (required)"
          name="agent"
          models={chat}
          value={roles.data.agent_model_id}
          required
        />
        <ModelSelect
          label="Light model (empty: use the agent model)"
          name="light"
          models={chat}
          value={roles.data.light_model_id}
        />
        <ModelSelect
          label="Embedding model"
          name="embedding"
          models={embedding}
          value={roles.data.embedding_model_id}
        />
        {save.isError && <ErrorText>{save.error.message}</ErrorText>}
      </SettingsCard>
    </form>
  );
}

function BudgetsSection() {
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
      <h3 className={h3Classes}>Monthly token budgets</h3>
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
