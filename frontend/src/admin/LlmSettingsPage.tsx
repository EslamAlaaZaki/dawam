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
} from "../api/llmSettings";
import { Loading } from "../shell/Loading";

/** Model roles, monthly token budgets and AI usage (admins only). */
export function LlmSettingsPage() {
  return (
    <section className="page admin-page">
      <h2>AI roles, budgets and usage</h2>
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
    <label>
      {label}
      <select name={name} defaultValue={value ?? ""} required={required}>
        {!required && <option value="">None</option>}
        {required && <option value="">Choose a model</option>}
        {models.map((model) => (
          <option key={model.id} value={model.id}>
            {model.label}
          </option>
        ))}
      </select>
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
    return <p role="alert">Could not load the model roles.</p>;
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
    save.mutate({
      agent_model_id: String(data.get("agent")),
      light_model_id: String(data.get("light") ?? "") || null,
      embedding_model_id: String(data.get("embedding") ?? "") || null,
    });
  }

  return (
    <form className="admin-form" onSubmit={onSubmit} aria-label="Model roles">
      <h3>Model roles</h3>
      {roles.data.reindex_needed && (
        <p role="status">
          Documents must be indexed again: the embedding model or its vector
          dimension changed.
        </p>
      )}
      <p>Only models that passed &quot;Test connection&quot; are offered.</p>
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
      {save.isError && (
        <p className="login-error" role="alert">
          {save.error.message}
        </p>
      )}
      {save.isSuccess && <p role="status">Roles saved.</p>}
      <button type="submit" disabled={save.isPending}>
        Save roles
      </button>
    </form>
  );
}

function BudgetsSection() {
  const budgets = useLlmBudgets();
  const workspaces = useAdminWorkspaces();
  const setInstallation = useSetInstallationBudget();
  const setWorkspace = useSetWorkspaceBudget();
  const clear = useClearWorkspaceBudget();
  if (budgets.isPending) {
    return <Loading />;
  }
  if (budgets.isError) {
    return <p role="alert">Could not load the budgets.</p>;
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
  const failure = setInstallation.error ?? setWorkspace.error ?? clear.error;
  return (
    <div>
      <h3>Monthly token budgets</h3>
      <form
        className="admin-form"
        onSubmit={onInstallation}
        aria-label="Installation budget"
      >
        <label>
          Installation (tokens per month; empty: unlimited)
          <input
            name="tokens"
            type="number"
            min={0}
            key={budgets.data.installation_monthly_token_budget ?? "none"}
            defaultValue={budgets.data.installation_monthly_token_budget ?? ""}
          />
        </label>
        <button type="submit" disabled={setInstallation.isPending}>
          Save installation budget
        </button>
      </form>
      {budgets.data.workspaces.length > 0 && (
        <ul aria-label="Workspace budgets">
          {budgets.data.workspaces.map((budget) => (
            <li key={budget.workspace_id}>
              {budget.workspace_name ?? budget.workspace_id}:{" "}
              {budget.monthly_token_budget.toLocaleString("en")} tokens
              <button
                type="button"
                className="secondary"
                onClick={() => clear.mutate(budget.workspace_id)}
              >
                Remove budget for {budget.workspace_name ?? budget.workspace_id}
              </button>
            </li>
          ))}
        </ul>
      )}
      <form
        className="admin-form"
        onSubmit={onWorkspace}
        aria-label="Workspace budget"
      >
        <label>
          Workspace
          <select name="workspace" required defaultValue="">
            <option value="">Choose a Workspace</option>
            {choices.map((workspace) => (
              <option key={workspace.id} value={workspace.id}>
                {workspace.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Tokens per month
          <input name="tokens" type="number" min={0} required />
        </label>
        <button type="submit" disabled={setWorkspace.isPending}>
          Set Workspace budget
        </button>
      </form>
      {failure && (
        <p className="login-error" role="alert">
          {failure.message}
        </p>
      )}
    </div>
  );
}

function UsageSection() {
  const [month, setMonth] = useState("");
  const usage = useLlmUsage(month || undefined);
  return (
    <div>
      <h3>Usage</h3>
      <label>
        Month
        <input
          type="month"
          value={month}
          onChange={(event) => setMonth(event.target.value)}
        />
      </label>
      {usage.isPending && <Loading />}
      {usage.isError && <p role="alert">Could not load the usage.</p>}
      {usage.data && (
        <>
          <p>
            {usage.data.month}:{" "}
            {usage.data.totals.total_tokens.toLocaleString("en")} tokens in{" "}
            {usage.data.totals.calls} calls
            {usage.data.installation_monthly_token_budget !== null &&
              ` (budget ${usage.data.installation_monthly_token_budget.toLocaleString("en")})`}
          </p>
          <table aria-label="Usage per Workspace">
            <thead>
              <tr>
                <th>Workspace</th>
                <th>Tokens</th>
                <th>Calls</th>
                <th>Budget</th>
              </tr>
            </thead>
            <tbody>
              {usage.data.workspaces.map((row) => (
                <tr key={row.workspace_id ?? "none"}>
                  <td>{row.workspace_name ?? "No Workspace"}</td>
                  <td>{row.totals.total_tokens.toLocaleString("en")}</td>
                  <td>{row.totals.calls}</td>
                  <td>
                    {row.monthly_token_budget?.toLocaleString("en") ?? "none"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <table aria-label="Usage per user">
            <thead>
              <tr>
                <th>User</th>
                <th>Tokens</th>
                <th>Calls</th>
              </tr>
            </thead>
            <tbody>
              {usage.data.users.map((row) => (
                <tr key={row.user_id ?? "none"}>
                  <td>{row.display_name ?? row.email ?? "System"}</td>
                  <td>{row.totals.total_tokens.toLocaleString("en")}</td>
                  <td>{row.totals.calls}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
