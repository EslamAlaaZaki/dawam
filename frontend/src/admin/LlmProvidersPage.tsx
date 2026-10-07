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
import { Loading } from "../shell/Loading";

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
      <p className="page" role="alert">
        Could not load the providers: {providers.error.message}
      </p>
    );
  }
  return (
    <section className="page admin-page">
      <h2>Language models</h2>
      {setup.data?.complete && !setup.data.has_internal_agent_model && (
        <p role="status">
          No internal agent model is registered: new Workspaces will default to not
          internal-only.
        </p>
      )}
      {providers.data.length === 0 ? (
        <p>No provider is registered yet.</p>
      ) : (
        providers.data.map((provider) => <ProviderCard key={provider.id} provider={provider} />)
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
    <form className="admin-form" onSubmit={onSubmit} aria-label="Add a provider">
      <h3>Add a provider</h3>
      <label>
        Name
        <input name="name" required />
      </label>
      <label>
        Type
        <select name="adapter" defaultValue="openai_compatible">
          <option value="openai_compatible">OpenAI-compatible (vLLM, Ollama, OpenAI, ...)</option>
          <option value="anthropic">Anthropic (Claude)</option>
          <option value="azure_openai">Azure OpenAI</option>
          <option value="gemini">Google Gemini (API or Vertex AI)</option>
          <option value="bedrock">AWS Bedrock</option>
        </select>
      </label>
      <label>
        Base URL (with its version prefix, e.g. http://vllm:8000/v1 or https://api.anthropic.com/v1)
        <input name="base_url" type="url" required />
      </label>
      <label>
        API key (optional)
        <input name="api_key" type="password" autoComplete="off" />
      </label>
      <label>
        Where it runs
        <select name="internal" defaultValue="true">
          <option value="true">Internal: inside your own infrastructure</option>
          <option value="false">External: a third-party service</option>
        </select>
      </label>
      <label>
        Timeout (seconds)
        <input name="timeout_seconds" type="number" min={1} max={600} defaultValue={60} required />
      </label>
      {create.isError && (
        <p className="login-error" role="alert">
          {create.error.message}
        </p>
      )}
      <button type="submit" disabled={create.isPending}>
        Add provider
      </button>
    </form>
  );
}

function ProviderCard({ provider }: { provider: LlmProvider }) {
  const remove = useDeleteLlmProvider();
  return (
    <article className="admin-card" aria-label={`Provider ${provider.name}`}>
      <h3>
        {provider.name} <span>({provider.internal ? "internal" : "external"})</span>
      </h3>
      <p>
        {provider.base_url} · API key {provider.has_api_key ? "saved" : "not set"} · timeout{" "}
        {provider.timeout_seconds} s
      </p>
      {provider.models.length === 0 ? (
        <p>No models yet.</p>
      ) : (
        <ul>
          {provider.models.map((model) => (
            <ModelItem key={model.id} model={model} />
          ))}
        </ul>
      )}
      <ModelForm providerId={provider.id} />
      {remove.isError && (
        <p className="login-error" role="alert">
          {remove.error.message}
        </p>
      )}
      <button
        type="button"
        className="secondary"
        onClick={() => remove.mutate(provider.id)}
        disabled={remove.isPending}
      >
        Remove provider
      </button>
    </article>
  );
}

function ModelItem({ model }: { model: LlmModel }) {
  const test = useTestLlmModel();
  const remove = useDeleteLlmModel();
  return (
    <li aria-label={`Model ${model.name}`}>
      <strong>{model.name}</strong> ({model.roles.map((role) => ROLE_LABELS[role]).join(", ")})
      {model.limited && <span> · limited: no native tool calling</span>}
      {model.test_ok === false && (
        <p role="alert" className="login-error">
          Test failed ({model.test_error_code}): {model.test_error}
        </p>
      )}
      {model.test_ok && (
        <p>
          Tool calling: {yesNo(model.tool_calling)} · streaming: {yesNo(model.streaming)} ·
          JSON schema: {yesNo(model.json_schema)} · context window:{" "}
          {model.context_window ?? "unknown"}
          {model.embedding_dimension !== null && ` · dimension: ${model.embedding_dimension}`}
        </p>
      )}
      {model.test_ok === null && <p>Not tested yet.</p>}
      {test.isError && (
        <p className="login-error" role="alert">
          {test.error.message}
        </p>
      )}
      <div className="admin-actions">
        <button type="button" onClick={() => test.mutate(model.id)} disabled={test.isPending}>
          Test connection
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => remove.mutate(model.id)}
          disabled={remove.isPending}
        >
          Remove model
        </button>
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
    <form className="admin-form" onSubmit={onSubmit} aria-label="Add a model">
      <label>
        Model name
        <input name="name" required />
      </label>
      <label>
        Role
        <select name="role" defaultValue="agent">
          <option value="agent">Agent</option>
          <option value="light">Light</option>
          <option value="embedding">Embedding</option>
        </select>
      </label>
      <label>
        Context window in tokens (empty: ask the server)
        <input name="context_window" type="number" min={1} />
      </label>
      {add.isError && (
        <p className="login-error" role="alert">
          {add.error.message}
        </p>
      )}
      <button type="submit" disabled={add.isPending}>
        Add model
      </button>
    </form>
  );
}
