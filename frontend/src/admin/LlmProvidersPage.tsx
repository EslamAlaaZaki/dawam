"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import {
  useAddLlmModel,
  useCreateLlmProvider,
  useDeleteLlmModel,
  useDeleteLlmProvider,
  useLlmModelUsage,
  useLlmProviders,
  useRemoveLlmModelWithReplacements,
  useLlmSetup,
  useTestLlmModel,
  useUpdateLlmModel,
  useUpdateLlmProvider,
  type LlmModel,
  type LlmModelRemoval,
  type LlmProvider,
} from "../api/llm";
import { ApiError } from "../api/client";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import {
  EntityTable,
  type EntityColumn,
} from "../components/entity/EntityTable";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { ErrorText, NativeSelect, PageHead, labelClasses } from "./parts";

const ROLE_LABELS: Record<LlmModel["roles"][number], string> = {
  agent: "Agent",
  light: "Light",
  embedding: "Embedding",
};

function yesNo(value: boolean | null): string {
  return value === null ? "not tested" : value ? "yes" : "no";
}

/** A model with the provider it belongs to: the table's row. */
interface ModelRow {
  model: LlmModel;
  provider: LlmProvider;
}

const MODEL_COLUMNS: EntityColumn<ModelRow>[] = [
  {
    header: "Model",
    cell: ({ model }) => (
      <>
        <strong>{model.name}</strong>
        {model.limited && (
          <div className="text-sm text-sand-11">
            Limited: no native tool calling
          </div>
        )}
      </>
    ),
  },
  {
    header: "Provider",
    cell: ({ provider }) => (
      <>
        {provider.name}{" "}
        <Badge variant="neutral">
          {provider.internal ? "Internal" : "External"}
        </Badge>
      </>
    ),
  },
  {
    header: "Roles",
    cell: ({ model }) =>
      model.roles.length
        ? model.roles.map((role) => ROLE_LABELS[role]).join(", ")
        : "None yet",
  },
  {
    header: "Test",
    className: "whitespace-normal",
    cell: ({ model }) => <TestResult model={model} />,
  },
];

const PROVIDER_COLUMNS: EntityColumn<LlmProvider>[] = [
  { header: "Provider", cell: (provider) => provider.name },
  {
    header: "Where it runs",
    cell: (provider) => (
      <Badge variant="neutral">
        {provider.internal ? "Internal" : "External"}
      </Badge>
    ),
  },
  {
    header: "Base URL",
    className: "text-sand-11",
    cell: (provider) => provider.base_url,
  },
  {
    header: "Details",
    className: "text-sand-11",
    cell: (provider) =>
      `API key ${provider.has_api_key ? "saved" : "not set"} · timeout ${provider.timeout_seconds} s`,
  },
];

function TestResult({ model }: { model: LlmModel }) {
  if (model.test_ok === false) {
    return (
      <ErrorText>
        Test failed ({model.test_error_code}): {model.test_error}
      </ErrorText>
    );
  }
  if (model.test_ok) {
    return (
      <p className="text-sm text-sand-11">
        Tool calling: {yesNo(model.tool_calling)} · streaming:{" "}
        {yesNo(model.streaming)} · JSON schema: {yesNo(model.json_schema)} ·
        context window: {model.context_window ?? "unknown"}
        {model.embedding_dimension !== null &&
          ` · dimension: ${model.embedding_dimension}`}
      </p>
    );
  }
  return <p className="text-sm text-sand-11">Not tested yet.</p>;
}

export function LlmProvidersPage() {
  const providers = useLlmProviders();
  const setup = useLlmSetup();
  const [addingProvider, setAddingProvider] = useState(false);
  const [addingModel, setAddingModel] = useState(false);
  const list = providers.data ?? [];
  const models: ModelRow[] = list.flatMap((provider) =>
    provider.models.map((model) => ({ model, provider })),
  );

  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Language models"
        description="The providers and models that DAWAM AI features run on."
        actions={
          <>
            <Button onClick={() => setAddingProvider(true)}>
              <Plus aria-hidden />
              Add provider
            </Button>
            <Button
              variant="primary"
              disabled={list.length === 0}
              onClick={() => setAddingModel(true)}
            >
              <Plus aria-hidden />
              Add model
            </Button>
          </>
        }
      />
      {setup.data?.complete && !setup.data.has_internal_agent_model && (
        <p
          role="status"
          className="rounded-lg border border-amber-11/40 bg-amber-3 px-3 py-2.5"
        >
          No internal agent model is registered: new Workspaces will default to
          not internal-only.
        </p>
      )}
      <h3 className="text-md font-semibold tracking-[-0.005em]">Models</h3>
      <EntityTable
        rows={models}
        rowKey={({ model }) => model.id}
        rowLabel={({ model }) => `Model ${model.name}`}
        columns={MODEL_COLUMNS}
        actions={(row) => (
          <ModelActions row={row} models={models.map((r) => r.model)} />
        )}
        isPending={providers.isPending}
        error={providers.error}
        loadErrorPrefix="Could not load the providers:"
        emptyMessage="No models yet."
        count="models"
      />
      {providers.isSuccess && (
        <>
          <h3 className="text-md font-semibold tracking-[-0.005em]">
            Providers
          </h3>
          <EntityTable
            rows={list}
            rowKey={(provider) => provider.id}
            rowLabel={(provider) => `Provider ${provider.name}`}
            columns={PROVIDER_COLUMNS}
            actions={(provider) => <ProviderActions provider={provider} />}
            emptyMessage="No provider is registered yet."
            count="providers"
          />
        </>
      )}
      <ProviderDialog open={addingProvider} onOpenChange={setAddingProvider} />
      <AddModelDialog
        providers={list}
        open={addingModel}
        onOpenChange={setAddingModel}
      />
    </section>
  );
}

/** The provider fields, for both adding and editing; an empty key keeps the saved one. */
function ProviderFields({ provider }: { provider?: LlmProvider }) {
  return (
    <>
      <label className={labelClasses}>
        Name
        <Input name="name" defaultValue={provider?.name ?? ""} required />
      </label>
      <label className={labelClasses}>
        Type
        <NativeSelect
          name="adapter"
          defaultValue={provider?.adapter ?? "openai_compatible"}
        >
          <option value="openai_compatible">
            OpenAI-compatible (vLLM, Ollama, OpenAI, ...)
          </option>
          <option value="anthropic">Anthropic (Claude)</option>
          <option value="azure_openai">Azure OpenAI</option>
          <option value="gemini">Google Gemini (API or Vertex AI)</option>
          <option value="bedrock">AWS Bedrock</option>
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Base URL (with its version prefix, e.g. http://vllm:8000/v1 or
        https://api.anthropic.com/v1)
        <Input
          name="base_url"
          type="url"
          defaultValue={provider?.base_url ?? ""}
          required
        />
      </label>
      <label className={labelClasses}>
        API key (optional)
        <Input
          name="api_key"
          type="password"
          autoComplete="off"
          placeholder={
            provider?.has_api_key ? "Saved: leave empty to keep it" : ""
          }
        />
      </label>
      <label className={labelClasses}>
        Where it runs
        <NativeSelect
          name="internal"
          defaultValue={provider?.internal === false ? "false" : "true"}
        >
          <option value="true">Internal: inside your own infrastructure</option>
          <option value="false">External: a third-party service</option>
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Timeout (seconds)
        <Input
          name="timeout_seconds"
          type="number"
          min={1}
          max={600}
          defaultValue={provider?.timeout_seconds ?? 60}
          required
        />
      </label>
    </>
  );
}

function providerBody(form: FormData) {
  const apiKey = String(form.get("api_key") ?? "");
  return {
    name: String(form.get("name") ?? "").trim(),
    adapter: String(form.get("adapter") ?? "openai_compatible"),
    base_url: String(form.get("base_url") ?? "").trim(),
    api_key: apiKey || undefined,
    internal: form.get("internal") === "true",
    timeout_seconds: Number(form.get("timeout_seconds")),
  };
}

/** Add a provider, or (given one) edit it. */
function ProviderDialog({
  provider,
  open,
  onOpenChange,
}: {
  provider?: LlmProvider;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateLlmProvider();
  const update = useUpdateLlmProvider(provider?.id ?? "");
  const save = provider ? update : create;

  function onSubmit(form: FormData) {
    const body = providerBody(form);
    save.mutate(body, {
      onSuccess: () => {
        toast.success(
          provider ? `${body.name} updated.` : `${body.name} added.`,
        );
        onOpenChange(false);
      },
    });
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) save.reset();
        onOpenChange(next);
      }}
      title={provider ? "Edit provider" : "Add a provider"}
      description={
        provider
          ? provider.name
          : "Register a service that serves language models."
      }
      submitLabel={provider ? "Save" : "Add provider"}
      onSubmit={onSubmit}
      pending={save.isPending}
      error={save.error}
    >
      <ProviderFields provider={provider} />
    </EntityFormDialog>
  );
}

function ProviderActions({ provider }: { provider: LlmProvider }) {
  const remove = useDeleteLlmProvider();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setEditing(true)}>
          Edit provider
        </Button>
        <Button size="sm" onClick={() => setRemoving(true)}>
          Remove provider
        </Button>
      </div>
      <ProviderDialog
        provider={provider}
        open={editing}
        onOpenChange={setEditing}
      />
      <ConfirmDialog
        open={removing}
        onOpenChange={(next) => {
          if (!next) remove.reset();
          setRemoving(next);
        }}
        title={`Remove ${provider.name}?`}
        description="The provider and all its models are removed."
        confirmLabel="Remove provider"
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() =>
          remove.mutate(provider.id, { onSuccess: () => setRemoving(false) })
        }
      />
    </>
  );
}

function ModelActions({
  row: { model, provider },
  models,
}: {
  row: ModelRow;
  models: LlmModel[];
}) {
  const test = useTestLlmModel();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setEditing(true)}>
          Edit
        </Button>
        <Button
          size="sm"
          onClick={() => test.mutate(model.id)}
          disabled={test.isPending}
        >
          Test connection
        </Button>
        <Button size="sm" onClick={() => setRemoving(true)}>
          Remove model
        </Button>
      </div>
      {test.isError && <ErrorText>{test.error.message}</ErrorText>}
      <EditModelDialog model={model} open={editing} onOpenChange={setEditing} />
      <RemoveModelDialog
        model={model}
        providerName={provider.name}
        candidates={models}
        open={removing}
        onOpenChange={setRemoving}
      />
    </>
  );
}

type Role = LlmModel["roles"][number];
type ReplacementKey =
  "agent_model_id" | "light_model_id" | "embedding_model_id";
const REPLACEMENT_KEY: Record<Role, ReplacementKey> = {
  agent: "agent_model_id",
  light: "light_model_id",
  embedding: "embedding_model_id",
};

interface UnallowedWorkspace {
  id: string;
  name: string;
  roles: Role[];
}

function unallowedWorkspaces(error: Error | null): UnallowedWorkspace[] {
  if (!(error instanceof ApiError) || error.code !== "replacement_not_allowed")
    return [];
  const list = (error.details as Record<string, unknown> | null | undefined)
    ?.workspaces;
  return Array.isArray(list) ? (list as UnallowedWorkspace[]) : [];
}

/** Asks before removing a model. One in use also asks for a replacement per role it
 * fills; Workspaces the replacement is not allowed for then get a choice of their own. */
function RemoveModelDialog({
  model,
  providerName,
  candidates,
  open,
  onOpenChange,
}: {
  model: LlmModel;
  providerName: string;
  candidates: LlmModel[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const usage = useLlmModelUsage(model.id, { enabled: open });
  const remove = useDeleteLlmModel();
  const replace = useRemoveLlmModelWithReplacements(model.id);
  const [chosen, setChosen] = useState<Partial<Record<Role, string>>>({});
  const [perWorkspace, setPerWorkspace] = useState<Record<string, string>>({});
  const roles = usage.data?.roles ?? [];
  const indexed = usage.data?.indexed_workspaces ?? [];
  const unallowed = unallowedWorkspaces(replace.error);
  const inUse = usage.data?.in_use ?? false;
  const optionsFor = (role: Role) =>
    candidates.filter(
      (c) =>
        c.id !== model.id &&
        c.test_ok === true &&
        c.roles.includes(role),
    );
  const complete =
    roles.every((role) => chosen[role]) &&
    unallowed.every((w) =>
      w.roles.every((role) => perWorkspace[`${w.id}:${role}`]),
    );

  const close = (next: boolean) => {
    if (!next) {
      remove.reset();
      replace.reset();
      setChosen({});
      setPerWorkspace({});
    }
    onOpenChange(next);
  };
  const confirm = () => {
    const done = { onSuccess: () => close(false) };
    if (!inUse) {
      remove.mutate(model.id, done);
      return;
    }
    const body: LlmModelRemoval = { workspace_replacements: [] };
    for (const role of roles) body[REPLACEMENT_KEY[role]] = chosen[role];
    for (const w of unallowed) {
      const entry: NonNullable<
        LlmModelRemoval["workspace_replacements"]
      >[number] = {
        workspace_id: w.id,
      };
      for (const role of w.roles)
        entry[REPLACEMENT_KEY[role]] = perWorkspace[`${w.id}:${role}`];
      body.workspace_replacements?.push(entry);
    }
    replace.mutate(body, done);
  };
  const picker = (
    label: string,
    value: string,
    role: Role,
    onChange: (id: string) => void,
  ) => (
    <label key={label} className={labelClasses}>
      {label}
      <NativeSelect
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Choose a model</option>
        {optionsFor(role).map((c) => (
          <option key={c.id} value={c.id}>
            {c.name}
          </option>
        ))}
      </NativeSelect>
    </label>
  );

  return (
    <ConfirmDialog
      open={open}
      onOpenChange={close}
      title={`Remove ${model.name}?`}
      description={
        inUse
          ? "This model is in use. Choose what replaces it for each role it fills."
          : `The model is removed from ${providerName}.`
      }
      confirmLabel="Remove model"
      pending={usage.isLoading || remove.isPending || replace.isPending}
      confirmDisabled={
        usage.isLoading || indexed.length > 0 || (inUse && !complete)
      }
      error={remove.error ?? replace.error ?? usage.error}
      onConfirm={confirm}
    >
      {indexed.length > 0 && (
        <p role="alert" className="text-sm">
          Documents in {indexed.map((w) => w.name).join(", ")} are indexed with
          this model, so it cannot be removed.
        </p>
      )}
      {indexed.length === 0 &&
        roles.map((role) =>
          picker(
            `Replace as ${ROLE_LABELS[role]} model`,
            chosen[role] ?? "",
            role,
            (id) => setChosen({ ...chosen, [role]: id }),
          ),
        )}
      {unallowed.length > 0 && (
        <p role="status" className="text-sm">
          The replacement is not allowed in these Workspaces. Choose another
          model for each.
        </p>
      )}
      {unallowed.flatMap((w) =>
        w.roles.map((role) =>
          picker(
            `${w.name}: ${ROLE_LABELS[role]} model`,
            perWorkspace[`${w.id}:${role}`] ?? "",
            role,
            (id) =>
              setPerWorkspace({ ...perWorkspace, [`${w.id}:${role}`]: id }),
          ),
        ),
      )}
    </ConfirmDialog>
  );
}

function ContextWindowField({ model }: { model?: LlmModel }) {
  return (
    <label className={labelClasses}>
      Context window in tokens (empty: ask the server)
      <Input
        name="context_window"
        type="number"
        min={1}
        defaultValue={model?.context_window ?? ""}
      />
    </label>
  );
}

function contextWindow(form: FormData): number | undefined {
  const window = String(form.get("context_window") ?? "").trim();
  return window ? Number(window) : undefined;
}

function AddModelDialog({
  providers,
  open,
  onOpenChange,
}: {
  providers: LlmProvider[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [providerId, setProviderId] = useState("");
  const chosen = providerId || providers[0]?.id || "";
  const add = useAddLlmModel(chosen);

  function onSubmit(form: FormData) {
    const body = {
      name: String(form.get("name") ?? "").trim(),
      context_window: contextWindow(form),
    };
    add.mutate(body, {
      onSuccess: () => {
        toast.success(`${body.name} added. Give it a role in AI settings.`);
        onOpenChange(false);
      },
    });
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) add.reset();
        onOpenChange(next);
      }}
      title="Add a model"
      description="Register a model on one of the providers. Its roles are set in the AI settings."
      submitLabel="Add model"
      onSubmit={onSubmit}
      pending={add.isPending}
      error={add.error}
    >
      <label className={labelClasses}>
        Provider
        <NativeSelect
          value={chosen}
          onChange={(event) => setProviderId(event.target.value)}
        >
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.name}
            </option>
          ))}
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Model name
        <Input name="name" required />
      </label>
      <ContextWindowField />
    </EntityFormDialog>
  );
}

function EditModelDialog({
  model,
  open,
  onOpenChange,
}: {
  model: LlmModel;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const update = useUpdateLlmModel(model.id);

  function onSubmit(form: FormData) {
    update.mutate(
      { context_window: contextWindow(form) },
      {
        onSuccess: () => {
          toast.success(`${model.name} updated.`);
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) update.reset();
        onOpenChange(next);
      }}
      title="Edit model"
      description={`${model.name}: only the context window can change.`}
      submitLabel="Save"
      onSubmit={onSubmit}
      pending={update.isPending}
      error={update.error}
    >
      <ContextWindowField model={model} />
    </EntityFormDialog>
  );
}
