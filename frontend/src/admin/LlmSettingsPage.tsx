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
  formatCost,
  useClearLlmModelPrice,
  useClearWorkspaceBudget,
  useClearWorkspaceCostBudget,
  useLlmBudgets,
  useLlmModelPrices,
  useLlmUsage,
  useSetInstallationBudget,
  useSetInstallationCostBudget,
  useSetLlmModelPrice,
  useSetWorkspaceBudget,
  useSetWorkspaceCostBudget,
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
  WarningNote,
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
      <PricesSection />
      <UsageSection />
    </section>
  );
}

const ROLE_COLUMNS: { role: LlmModelRole; label: string }[] = [
  { role: "agent", label: "Agent" },
  { role: "light", label: "Light" },
  { role: "embedding", label: "Embedding" },
  { role: "vision", label: "Vision" },
  { role: "rerank", label: "Rerank" },
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

/** Every registered model with a checkbox per Model Role (any combination). */
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
    <div className="flex flex-col gap-3.5 rounded-pill border border-border bg-surface-raised p-5">
      <h3 className={h3Classes}>Model roles</h3>
      <p className="text-sm text-muted-foreground">
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
                <TableCell colSpan={4 + ROLE_COLUMNS.length}>
                  No models registered yet.
                </TableCell>
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
  const changed = ROLE_COLUMNS.some(
    ({ role }) => roles.includes(role) !== model.roles.includes(role),
  );
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
        <strong className="font-mono font-medium">{model.name}</strong>
        {mixed && (
          <div role="note" className="text-sm text-muted-foreground">
            {EMBEDDING_HINT}
          </div>
        )}
        {save.isError && !asking && <ErrorText>{save.error.message}</ErrorText>}
        {model.warnings.map((warning) => (
          <WarningNote key={warning}>{warning}</WarningNote>
        ))}
      </TableCell>
      <TableCell>
        {provider.name}{" "}
        <Badge variant="neutral">
          {provider.internal ? "Internal" : "External"}
        </Badge>
      </TableCell>
      <TableCell className="whitespace-normal text-muted-foreground">
        {testSummary(model)}
      </TableCell>
      {ROLE_COLUMNS.map(({ role, label }) => (
        <TableCell key={role}>
          <input
            type="checkbox"
            aria-label={`${label} role of ${model.name}`}
            className="size-4 accent-primary"
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
    <div className="flex flex-col gap-3.5 rounded-pill border border-border bg-surface-raised p-5 items-start">
      <h3 className={h3Classes}>Monthly budgets</h3>
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
              Monthly token and currency budgets for the installation and per
              Workspace. Whichever is reached first stops AI use.
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
  const setInstallationCost = useSetInstallationCostBudget();
  const setWorkspaceCost = useSetWorkspaceCostBudget();
  const clearCost = useClearWorkspaceCostBudget();
  const [removing, setRemoving] = useState<
    LlmBudgets["workspaces"][number] | null
  >(null);
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

  function onInstallationCost(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const amount = String(data.get("amount") ?? "").trim();
    setInstallationCost.mutate(
      amount
        ? {
            currency: String(data.get("currency")).trim().toUpperCase(),
            amount,
          }
        : null,
    );
  }

  function onWorkspaceCost(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    setWorkspaceCost.mutate({
      workspaceId: String(data.get("workspace")),
      budget: {
        currency: String(data.get("currency")).trim().toUpperCase(),
        amount: String(data.get("amount")).trim(),
      },
    });
  }

  const installationCost = budgets.data.installation_monthly_cost_budget;
  const choices = workspaces.data?.pages.flatMap((page) => page.items) ?? [];
  const failure =
    setInstallation.error ??
    setWorkspace.error ??
    setInstallationCost.error ??
    setWorkspaceCost.error ??
    clearCost.error;
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
              defaultValue={
                budgets.data.installation_monthly_token_budget ?? ""
              }
            />
          </label>
        </SettingsCard>
      </form>
      {budgets.data.workspaces.length > 0 && (
        <ul
          aria-label="Workspace budgets"
          className="flex max-w-xl flex-col divide-y divide-border rounded-pill border border-border bg-surface-raised"
        >
          {budgets.data.workspaces.map((budget) => (
            <li
              key={budget.workspace_id}
              className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
            >
              <span className="tabular-nums">
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
      <form
        onSubmit={onInstallationCost}
        aria-label="Installation currency budget"
      >
        <SettingsCard
          title="Installation currency budget"
          description="Only calls on models priced in this currency count. Empty amount: unlimited."
          footer={
            <Button type="submit" disabled={setInstallationCost.isPending}>
              Save installation currency budget
            </Button>
          }
        >
          <label className={labelClasses}>
            Currency (ISO code)
            <Input
              name="currency"
              required
              maxLength={3}
              key={`c-${installationCost?.currency ?? "none"}`}
              defaultValue={installationCost?.currency ?? "USD"}
            />
          </label>
          <label className={labelClasses}>
            Amount per month
            <Input
              name="amount"
              type="number"
              min={0}
              step="any"
              key={`a-${installationCost?.amount ?? "none"}`}
              defaultValue={
                installationCost ? Number(installationCost.amount) : ""
              }
            />
          </label>
        </SettingsCard>
      </form>
      {budgets.data.workspace_cost_budgets.length > 0 && (
        <ul
          aria-label="Workspace currency budgets"
          className="flex max-w-xl flex-col divide-y divide-border rounded-pill border border-border bg-surface-raised"
        >
          {budgets.data.workspace_cost_budgets.map((row) => (
            <li
              key={row.workspace_id}
              className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
            >
              <span className="tabular-nums">
                {row.workspace_name ?? row.workspace_id}:{" "}
                {Number(row.budget.amount)} {row.budget.currency}
              </span>
              <Button
                type="button"
                size="sm"
                disabled={clearCost.isPending}
                onClick={() => clearCost.mutate(row.workspace_id)}
              >
                Remove currency budget for{" "}
                {row.workspace_name ?? row.workspace_id}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <form onSubmit={onWorkspaceCost} aria-label="Workspace currency budget">
        <SettingsCard
          title="Workspace currency budget"
          footer={
            <Button type="submit" disabled={setWorkspaceCost.isPending}>
              Set Workspace currency budget
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
            Currency (ISO code)
            <Input name="currency" required maxLength={3} defaultValue="USD" />
          </label>
          <label className={labelClasses}>
            Amount per month
            <Input name="amount" type="number" min={0} step="any" required />
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
          clear.mutate(removing.workspace_id, {
            onSuccess: () => setRemoving(null),
          })
        }
      />
      {failure && <ErrorText>{failure.message}</ErrorText>}
    </div>
  );
}

function PricesSection() {
  const prices = useLlmModelPrices();
  const setPrice = useSetLlmModelPrice();
  const clear = useClearLlmModelPrice();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setPrice.mutate(
      {
        model_name: String(data.get("model_name")).trim(),
        currency: String(data.get("currency")).trim().toUpperCase(),
        input_per_million: String(data.get("input")),
        output_per_million: String(data.get("output")),
        cached_input_per_million: String(data.get("cached")),
      },
      { onSuccess: () => form.reset() },
    );
  }

  const failure = setPrice.error ?? clear.error;
  return (
    <div className="flex flex-col gap-3.5 rounded-pill border border-border bg-surface-raised p-5">
      <h3 className={h3Classes}>Model prices</h3>
      <p className="text-muted-foreground">
        Prices per million tokens turn usage into cost. A model without a price
        shows its cost as unknown.
      </p>
      {prices.isPending && <Loading />}
      {prices.isError && <ErrorText>Could not load the prices.</ErrorText>}
      {prices.data && prices.data.prices.length > 0 && (
        <TableCard>
          <Table aria-label="Model prices">
            <TableHeader>
              <TableRow>
                <TableHead>Model</TableHead>
                <TableHead>Currency</TableHead>
                <TableHead>Input</TableHead>
                <TableHead>Output</TableHead>
                <TableHead>Cached input</TableHead>
                <TableHead />
              </TableRow>
            </TableHeader>
            <TableBody>
              {prices.data.prices.map((row) => (
                <TableRow key={row.model_name}>
                  <TableCell className="font-mono">{row.model_name}</TableCell>
                  <TableCell>{row.currency}</TableCell>
                  <TableCell className="tabular-nums">
                    {Number(row.input_per_million)}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {Number(row.output_per_million)}
                  </TableCell>
                  <TableCell className="tabular-nums">
                    {Number(row.cached_input_per_million)}
                  </TableCell>
                  <TableCell>
                    <Button
                      type="button"
                      size="sm"
                      disabled={clear.isPending}
                      onClick={() => clear.mutate(row.model_name)}
                    >
                      Remove price of {row.model_name}
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
      <form onSubmit={onSubmit} aria-label="Model price">
        <SettingsCard
          title="Set a model price"
          description="Saving a name that is already listed replaces its price."
          footer={
            <Button type="submit" disabled={setPrice.isPending}>
              Save price
            </Button>
          }
        >
          <label className={labelClasses}>
            Model name
            <Input name="model_name" required className="font-mono" />
          </label>
          <label className={labelClasses}>
            Currency (ISO code)
            <Input name="currency" required maxLength={3} defaultValue="USD" />
          </label>
          <label className={labelClasses}>
            Input price per million tokens
            <Input name="input" type="number" min={0} step="any" required />
          </label>
          <label className={labelClasses}>
            Output price per million tokens
            <Input name="output" type="number" min={0} step="any" required />
          </label>
          <label className={labelClasses}>
            Cached input price per million tokens
            <Input name="cached" type="number" min={0} step="any" required />
          </label>
        </SettingsCard>
      </form>
      {failure && <ErrorText>{failure.message}</ErrorText>}
    </div>
  );
}

function UsageSection() {
  const [month, setMonth] = useState("");
  const usage = useLlmUsage(month || undefined);
  return (
    <div className="flex flex-col gap-3.5 rounded-pill border border-border bg-surface-raised p-5">
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
          <p className="text-muted-foreground">
            {usage.data.month}:{" "}
            {usage.data.totals.total_tokens.toLocaleString("en")} tokens in{" "}
            {usage.data.totals.calls} calls, cost{" "}
            {formatCost(usage.data.totals.cost)}
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
                  <TableHead>Cost</TableHead>
                  <TableHead>Budget</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.data.workspaces.map((row) => (
                  <TableRow key={row.workspace_id ?? "none"}>
                    <TableCell>
                      {row.workspace_name ?? "No Workspace"}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {row.totals.total_tokens.toLocaleString("en")}
                    </TableCell>
                    <TableCell>{row.totals.calls}</TableCell>
                    <TableCell className="tabular-nums">
                      {formatCost(row.totals.cost)}
                    </TableCell>
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
                  <TableHead>Cost</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {usage.data.users.map((row) => (
                  <TableRow key={row.user_id ?? "none"}>
                    <TableCell>
                      {row.display_name ?? row.email ?? "System"}
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {row.totals.total_tokens.toLocaleString("en")}
                    </TableCell>
                    <TableCell>{row.totals.calls}</TableCell>
                    <TableCell className="tabular-nums">
                      {formatCost(row.totals.cost)}
                    </TableCell>
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
