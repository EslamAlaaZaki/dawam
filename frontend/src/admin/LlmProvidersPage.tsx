"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import {
  useAddLlmModel,
  useCreateLlmProvider,
  useDeleteLlmModel,
  useDeleteLlmProvider,
  useLlmProviders,
  useLlmSetup,
  useTestLlmModel,
  useUpdateLlmModel,
  useUpdateLlmProvider,
  type LlmModel,
  type LlmProvider,
} from "../api/llm";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import { EntityTable, type EntityColumn } from "../components/entity/EntityTable";
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
          <div className="text-sm text-sand-11">Limited: no native tool calling</div>
        )}
      </>
    ),
  },
  {
    header: "Provider",
    cell: ({ provider }) => (
      <>
        {provider.name}{" "}
        <Badge variant="neutral">{provider.internal ? "Internal" : "External"}</Badge>
      </>
    ),
  },
  {
    header: "Roles",
    cell: ({ model }) => model.roles.map((role) => ROLE_LABELS[role]).join(", "),
  },
  { header: "Test", className: "whitespace-normal", cell: ({ model }) => <TestResult model={model} /> },
];

const PROVIDER_COLUMNS: EntityColumn<LlmProvider>[] = [
  { header: "Provider", cell: (provider) => provider.name },
  {
    header: "Where it runs",
    cell: (provider) => (
      <Badge variant="neutral">{provider.internal ? "Internal" : "External"}</Badge>
    ),
  },
  { header: "Base URL", className: "text-sand-11", cell: (provider) => provider.base_url },
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
        Tool calling: {yesNo(model.tool_calling)} · streaming: {yesNo(model.streaming)} · JSON
        schema: {yesNo(model.json_schema)} · context window: {model.context_window ?? "unknown"}
        {model.embedding_dimension !== null && ` · dimension: ${model.embedding_dimension}`}
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
        actions={(row) => <ModelActions row={row} />}
        isPending={providers.isPending}
        error={providers.error}
        loadErrorPrefix="Could not load the providers:"
        emptyMessage="No models yet."
        count="models"
      />
      {providers.isSuccess && (
        <>
          <h3 className="text-md font-semibold tracking-[-0.005em]">Providers</h3>
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
      <AddModelDialog providers={list} open={addingModel} onOpenChange={setAddingModel} />
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
        <NativeSelect name="adapter" defaultValue={provider?.adapter ?? "openai_compatible"}>
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
        <Input name="base_url" type="url" defaultValue={provider?.base_url ?? ""} required />
      </label>
      <label className={labelClasses}>
        API key (optional)
        <Input
          name="api_key"
          type="password"
          autoComplete="off"
          placeholder={provider?.has_api_key ? "Saved: leave empty to keep it" : ""}
        />
      </label>
      <label className={labelClasses}>
        Where it runs
        <NativeSelect name="internal" defaultValue={provider?.internal === false ? "false" : "true"}>
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
        toast.success(provider ? `${body.name} updated.` : `${body.name} added.`);
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
      description={provider ? provider.name : "Register a service that serves language models."}
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
      <ProviderDialog provider={provider} open={editing} onOpenChange={setEditing} />
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
        onConfirm={() => remove.mutate(provider.id, { onSuccess: () => setRemoving(false) })}
      />
    </>
  );
}

function ModelActions({ row: { model, provider } }: { row: ModelRow }) {
  const test = useTestLlmModel();
  const remove = useDeleteLlmModel();
  const [editing, setEditing] = useState(false);
  const [removing, setRemoving] = useState(false);
  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setEditing(true)}>
          Edit
        </Button>
        <Button size="sm" onClick={() => test.mutate(model.id)} disabled={test.isPending}>
          Test connection
        </Button>
        <Button size="sm" onClick={() => setRemoving(true)}>
          Remove model
        </Button>
      </div>
      {test.isError && <ErrorText>{test.error.message}</ErrorText>}
      <EditModelDialog model={model} open={editing} onOpenChange={setEditing} />
      <ConfirmDialog
        open={removing}
        onOpenChange={(next) => {
          if (!next) remove.reset();
          setRemoving(next);
        }}
        title={`Remove ${model.name}?`}
        description={`The model is removed from ${provider.name}.`}
        confirmLabel="Remove model"
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() => remove.mutate(model.id, { onSuccess: () => setRemoving(false) })}
      />
    </>
  );
}

function ModelFields({ model }: { model?: LlmModel }) {
  return (
    <>
      <label className={labelClasses}>
        Model name
        <Input name="name" defaultValue={model?.name ?? ""} required />
      </label>
      <label className={labelClasses}>
        Role
        <NativeSelect name="role" defaultValue={model?.roles[0] ?? "agent"}>
          <option value="agent">Agent</option>
          <option value="light">Light</option>
          <option value="embedding">Embedding</option>
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Context window in tokens (empty: ask the server)
        <Input
          name="context_window"
          type="number"
          min={1}
          defaultValue={model?.context_window ?? ""}
        />
      </label>
    </>
  );
}

function modelBody(form: FormData, current?: LlmModel) {
  const window = String(form.get("context_window") ?? "").trim();
  const role = String(form.get("role")) as LlmModel["roles"][number];
  return {
    name: String(form.get("name") ?? "").trim(),
    // An untouched role keeps every role the model has.
    roles: current && current.roles[0] === role ? current.roles : [role],
    context_window: window ? Number(window) : undefined,
  };
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
    const body = modelBody(form);
    add.mutate(body, {
      onSuccess: () => {
        toast.success(`${body.name} added.`);
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
      description="Register a model on one of the providers."
      submitLabel="Add model"
      onSubmit={onSubmit}
      pending={add.isPending}
      error={add.error}
    >
      <label className={labelClasses}>
        Provider
        <NativeSelect value={chosen} onChange={(event) => setProviderId(event.target.value)}>
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.name}
            </option>
          ))}
        </NativeSelect>
      </label>
      <ModelFields />
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
    update.mutate(modelBody(form, model), {
      onSuccess: () => {
        toast.success(`${model.name} updated.`);
        onOpenChange(false);
      },
    });
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) update.reset();
        onOpenChange(next);
      }}
      title="Edit model"
      description="A new name or role drops its test result."
      submitLabel="Save"
      onSubmit={onSubmit}
      pending={update.isPending}
      error={update.error}
    >
      <ModelFields model={model} />
    </EntityFormDialog>
  );
}
