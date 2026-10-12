"use client";

import { type FormEvent } from "react";

import {
  useAddLlmModel,
  useCreateLlmProvider,
  useDeleteLlmModel,
  useDeleteLlmProvider,
  useLlmProviders,
  useLlmSetup,
  useTestLlmModel,
  type LlmModel,
  type LlmProvider,
} from "../api/llm";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Loading } from "../shell/Loading";
import { ErrorText, NativeSelect, PageHead, labelClasses, EmptyState, formCardClasses, h3Classes } from "./parts";

const cardClasses = formCardClasses;

const ROLE_LABELS: Record<LlmModel["roles"][number], string> = {
  agent: "Agent",
  light: "Light",
  embedding: "Embedding",
};

function yesNo(value: boolean | null): string {
  return value === null ? "not tested" : value ? "yes" : "no";
}

export function LlmProvidersPage() {
  const providers = useLlmProviders();
  const setup = useLlmSetup();
  if (providers.isPending) {
    return <Loading />;
  }
  if (providers.isError) {
    return (
      <ErrorText>
        Could not load the providers: {providers.error.message}
      </ErrorText>
    );
  }
  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Language models"
        description="The providers and models that DAWAM AI features run on."
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
      {providers.data.length === 0 ? (
        <EmptyState>No provider is registered yet.</EmptyState>
      ) : (
        providers.data.map((provider) => (
          <ProviderCard key={provider.id} provider={provider} />
        ))
      )}
      <ProviderForm />
    </section>
  );
}

function ProviderForm() {
  const create = useCreateLlmProvider();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const apiKey = String(data.get("api_key") ?? "");
    create.mutate(
      {
        name: String(data.get("name") ?? "").trim(),
        adapter: String(data.get("adapter") ?? "openai_compatible"),
        base_url: String(data.get("base_url") ?? "").trim(),
        api_key: apiKey || undefined,
        internal: data.get("internal") === "true",
        timeout_seconds: Number(data.get("timeout_seconds")),
      },
      { onSuccess: () => form.reset() },
    );
  }

  return (
    <form
      className={cardClasses}
      onSubmit={onSubmit}
      aria-label="Add a provider"
    >
      <h3 className={h3Classes}>Add a provider</h3>
      <label className={labelClasses}>
        Name
        <Input name="name" required />
      </label>
      <label className={labelClasses}>
        Type
        <NativeSelect name="adapter" defaultValue="openai_compatible">
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
        <Input name="base_url" type="url" required />
      </label>
      <label className={labelClasses}>
        API key (optional)
        <Input name="api_key" type="password" autoComplete="off" />
      </label>
      <label className={labelClasses}>
        Where it runs
        <NativeSelect name="internal" defaultValue="true">
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
          defaultValue={60}
          required
        />
      </label>
      {create.isError && <ErrorText>{create.error.message}</ErrorText>}
      <Button
        type="submit"
        variant="primary"
        className="self-start"
        disabled={create.isPending}
      >
        Add provider
      </Button>
    </form>
  );
}

function ProviderCard({ provider }: { provider: LlmProvider }) {
  const remove = useDeleteLlmProvider();
  return (
    <article
      className="flex max-w-3xl flex-col gap-3 rounded-xl border border-sand-5 bg-sand-1 p-5"
      aria-label={`Provider ${provider.name}`}
    >
      <h3 className={h3Classes}>
        {provider.name}{" "}
        <span className="font-normal text-sand-11">
          ({provider.internal ? "internal" : "external"})
        </span>
      </h3>
      <p className="text-sm text-sand-11">
        {provider.base_url} · API key{" "}
        {provider.has_api_key ? "saved" : "not set"} · timeout{" "}
        {provider.timeout_seconds} s
      </p>
      {provider.models.length === 0 ? (
        <EmptyState>No models yet.</EmptyState>
      ) : (
        <ul className="flex flex-col divide-y divide-sand-4 rounded-lg border border-sand-4">
          {provider.models.map((model) => (
            <ModelItem key={model.id} model={model} />
          ))}
        </ul>
      )}
      <ModelForm providerId={provider.id} />
      {remove.isError && <ErrorText>{remove.error.message}</ErrorText>}
      <Button
        type="button"
        className="self-start"
        onClick={() => remove.mutate(provider.id)}
        disabled={remove.isPending}
      >
        Remove provider
      </Button>
    </article>
  );
}

function ModelItem({ model }: { model: LlmModel }) {
  const test = useTestLlmModel();
  const remove = useDeleteLlmModel();
  return (
    <li className="flex flex-col gap-2 p-3" aria-label={`Model ${model.name}`}>
      <div>
        <strong>{model.name}</strong> (
        {model.roles.map((role) => ROLE_LABELS[role]).join(", ")})
        {model.limited && <span> · limited: no native tool calling</span>}
      </div>
      {model.test_ok === false && (
        <ErrorText>
          Test failed ({model.test_error_code}): {model.test_error}
        </ErrorText>
      )}
      {model.test_ok && (
        <p className="text-sm text-sand-11">
          Tool calling: {yesNo(model.tool_calling)} · streaming:{" "}
          {yesNo(model.streaming)} · JSON schema: {yesNo(model.json_schema)} ·
          context window: {model.context_window ?? "unknown"}
          {model.embedding_dimension !== null &&
            ` · dimension: ${model.embedding_dimension}`}
        </p>
      )}
      {model.test_ok === null && (
        <p className="text-sm text-sand-11">Not tested yet.</p>
      )}
      {test.isError && <ErrorText>{test.error.message}</ErrorText>}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          onClick={() => test.mutate(model.id)}
          disabled={test.isPending}
        >
          Test connection
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={() => remove.mutate(model.id)}
          disabled={remove.isPending}
        >
          Remove model
        </Button>
      </div>
    </li>
  );
}

function ModelForm({ providerId }: { providerId: string }) {
  const add = useAddLlmModel(providerId);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const window = String(data.get("context_window") ?? "").trim();
    add.mutate(
      {
        name: String(data.get("name") ?? "").trim(),
        roles: [String(data.get("role"))] as LlmModel["roles"],
        context_window: window ? Number(window) : undefined,
      },
      { onSuccess: () => form.reset() },
    );
  }

  return (
    <form
      className="flex flex-col gap-3.5"
      onSubmit={onSubmit}
      aria-label="Add a model"
    >
      <label className={labelClasses}>
        Model name
        <Input name="name" required />
      </label>
      <label className={labelClasses}>
        Role
        <NativeSelect name="role" defaultValue="agent">
          <option value="agent">Agent</option>
          <option value="light">Light</option>
          <option value="embedding">Embedding</option>
        </NativeSelect>
      </label>
      <label className={labelClasses}>
        Context window in tokens (empty: ask the server)
        <Input name="context_window" type="number" min={1} />
      </label>
      {add.isError && <ErrorText>{add.error.message}</ErrorText>}
      <Button type="submit" className="self-start" disabled={add.isPending}>
        Add model
      </Button>
    </form>
  );
}
