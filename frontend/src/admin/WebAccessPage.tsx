"use client";

import { useState, type FormEvent } from "react";

import {
  useDeleteWebProvider,
  useSaveWebProvider,
  useSaveWebSettings,
  useTestWebProvider,
  useWebSettings,
  type WebProvider,
  type WebProviderKind,
  type WebSettings,
} from "../api/web";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { Switch } from "../components/ui/switch";
import { Loading } from "../shell/Loading";
import {
  ErrorText,
  NativeSelect,
  PageHead,
  formCardClasses,
  h3Classes,
  labelClasses,
} from "./parts";

const LABELS: Record<WebProviderKind, string> = {
  searxng: "SearXNG",
  langsearch: "LangSearch",
};

/**
 * Web search and web fetch for the assistant (ADR 0008): one switch, off by default; the
 * search providers (SearXNG, internal; LangSearch, external), each tested here; and the
 * provider of normal and of internal-only Workspaces. Admins only.
 */
export function WebAccessPage() {
  const settings = useWebSettings();
  if (settings.isPending) {
    return <Loading />;
  }
  if (settings.isError) {
    return (
      <ErrorText>
        Could not load the web access settings: {settings.error.message}
      </ErrorText>
    );
  }
  const saved = settings.data;
  const byKind = (kind: WebProviderKind) =>
    saved.providers.find((p) => p.kind === kind);
  return (
    <section className="flex flex-col gap-6">
      <PageHead
        title="Web access"
        description={
          saved.enabled
            ? "The assistant may search the web and read pages members link to. Every query is audited and shown in the chat."
            : "Off: the assistant has no web access."
        }
      />
      <WebSwitchForm key={saved.updated_at ?? "none"} saved={saved} />
      <SearxngForm
        key={`searxng-${byKind("searxng")?.updated_at ?? "none"}`}
        provider={byKind("searxng")}
      />
      <LangSearchForm
        key={`langsearch-${byKind("langsearch")?.updated_at ?? "none"}`}
        provider={byKind("langsearch")}
      />
    </section>
  );
}

function WebSwitchForm({ saved }: { saved: WebSettings }) {
  const save = useSaveWebSettings();
  const [enabled, setEnabled] = useState(saved.enabled);
  const internal = saved.providers.filter((p) => p.internal);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const pick = (name: string) =>
      (String(form.get(name) ?? "") || null) as WebProviderKind | null;
    save.mutate(
      {
        enabled,
        normal_provider: pick("normal_provider"),
        internal_provider: pick("internal_provider"),
      },
      { onSuccess: () => toast.success("Web access saved.") },
    );
  }

  return (
    <form onSubmit={onSubmit} aria-label="Web access">
      <section aria-label="Web search and fetch" className={formCardClasses}>
        <div>
          <h3 className={h3Classes}>Web search and fetch</h3>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Before a search goes out, DAWAM removes sensitive data from it
            (names, numbers, emails, dates and the like), so only the topic is
            sent. DAWAM opens web pages itself and never opens internal company
            addresses.
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm font-medium">
          <Switch checked={enabled} onCheckedChange={setEnabled} />
          Let the assistant use the web
        </label>
        <label className={labelClasses}>
          Search provider for Workspaces
          <NativeSelect
            name="normal_provider"
            defaultValue={saved.normal_provider ?? ""}
          >
            <option value="">None: no web search</option>
            {saved.providers.map((p) => (
              <option key={p.kind} value={p.kind}>
                {p.label} ({p.internal ? "internal" : "external"})
              </option>
            ))}
          </NativeSelect>
        </label>
        <label className={labelClasses}>
          Search provider for internal-only Workspaces
          <NativeSelect
            name="internal_provider"
            defaultValue={saved.internal_provider ?? ""}
          >
            <option value="">None: no web access</option>
            {internal.map((p) => (
              <option key={p.kind} value={p.kind}>
                {p.label} (internal)
              </option>
            ))}
          </NativeSelect>
          <span className="text-sm font-normal text-muted-foreground">
            Internal-only Workspaces use internal providers only, and read web
            pages only once one is chosen here.
          </span>
        </label>
        {save.isError && <ErrorText>{save.error.message}</ErrorText>}
        <div className="flex items-center gap-2">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            Save
          </Button>
        </div>
      </section>
    </form>
  );
}

function TestStatus({ provider }: { provider: WebProvider }) {
  if (provider.test_ok === null) {
    return <Badge variant="neutral">Not tested</Badge>;
  }
  return provider.test_ok ? (
    <Badge variant="success">Test passed</Badge>
  ) : (
    <Badge variant="danger">Test failed</Badge>
  );
}

function ProviderActions({ provider }: { provider: WebProvider | undefined }) {
  const test = useTestWebProvider();
  const remove = useDeleteWebProvider();
  const [removing, setRemoving] = useState(false);
  if (!provider) {
    return null;
  }
  const label = LABELS[provider.kind];
  return (
    <>
      <Button
        type="button"
        disabled={test.isPending}
        onClick={() =>
          test.mutate(provider.kind, {
            onSuccess: (tested) =>
              tested.test_ok
                ? toast.success(`${label} answered.`)
                : toast.error(`${label} did not answer.`),
          })
        }
      >
        Test
      </Button>
      <Button type="button" onClick={() => setRemoving(true)}>
        Remove
      </Button>
      {test.isError && <ErrorText>{test.error.message}</ErrorText>}
      <ConfirmDialog
        open={removing}
        onOpenChange={(next) => {
          if (!next) remove.reset();
          setRemoving(next);
        }}
        title={`Remove ${label}?`}
        description="Workspaces that search through it have no web search until another provider is chosen."
        confirmLabel="Remove"
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() =>
          remove.mutate(provider.kind, { onSuccess: () => setRemoving(false) })
        }
      />
    </>
  );
}

function ProviderHead({
  provider,
  label,
  kind,
  children,
}: {
  provider: WebProvider | undefined;
  label: string;
  kind: string;
  children: string;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className={h3Classes}>{label}</h3>
        <Badge variant="neutral">{kind}</Badge>
        {provider && <TestStatus provider={provider} />}
      </div>
      <p className="mt-0.5 text-sm text-muted-foreground">{children}</p>
      {provider?.test_error && (
        <p className="mt-1 text-sm text-danger-strong">{provider.test_error}</p>
      )}
    </div>
  );
}

function SearxngForm({ provider }: { provider: WebProvider | undefined }) {
  const save = useSaveWebProvider();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    save.mutate(
      {
        kind: "searxng",
        body: { base_url: String(form.get("base_url") ?? "").trim() },
      },
      { onSuccess: () => toast.success("SearXNG saved.") },
    );
  }

  return (
    <form onSubmit={onSubmit} aria-label="SearXNG">
      <section aria-label="SearXNG provider" className={formCardClasses}>
        <ProviderHead provider={provider} label="SearXNG" kind="Internal">
          Your self-hosted SearXNG server, no API key. Turn on its JSON output:
          add json under search.formats in its settings.yml.
        </ProviderHead>
        <label className={labelClasses}>
          SearXNG URL
          <Input
            name="base_url"
            className="font-mono"
            placeholder="http://searxng:8080"
            defaultValue={provider?.base_url ?? ""}
            required
          />
        </label>
        {save.isError && <ErrorText>{save.error.message}</ErrorText>}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            {provider ? "Save" : "Register"}
          </Button>
          <ProviderActions provider={provider} />
        </div>
      </section>
    </form>
  );
}

function LangSearchForm({ provider }: { provider: WebProvider | undefined }) {
  const save = useSaveWebProvider();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const apiKey = String(form.get("api_key") ?? "").trim();
    // Left empty: keep the saved key (the API then leaves it as it is).
    save.mutate(
      { kind: "langsearch", body: apiKey ? { api_key: apiKey } : {} },
      { onSuccess: () => toast.success("LangSearch saved.") },
    );
  }

  return (
    <form onSubmit={onSubmit} aria-label="LangSearch">
      <section aria-label="LangSearch provider" className={formCardClasses}>
        <ProviderHead provider={provider} label="LangSearch" kind="External">
          A cloud search API with a free tier. Queries go to LangSearch under
          this installation&apos;s key; internal-only Workspaces never use it.
        </ProviderHead>
        <label className={labelClasses}>
          API key
          <Input
            name="api_key"
            type="password"
            autoComplete="new-password"
            placeholder={
              provider?.has_api_key ? "Saved: leave empty to keep it" : ""
            }
            required={!provider?.has_api_key}
          />
        </label>
        {save.isError && <ErrorText>{save.error.message}</ErrorText>}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="submit" variant="primary" disabled={save.isPending}>
            {provider ? "Save" : "Register"}
          </Button>
          <ProviderActions provider={provider} />
        </div>
      </section>
    </form>
  );
}
