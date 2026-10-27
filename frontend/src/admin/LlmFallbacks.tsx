"use client";

import { ArrowDown, ArrowUp, X } from "lucide-react";
import { useState } from "react";

import {
  useLlmFallbacks,
  useSetLlmFallbacks,
  type LlmFallbackRole,
} from "../api/llmFallbacks";
import type { LlmProvider } from "../api/llm";
import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { ErrorText, NativeSelect } from "./parts";

const ROLES: { role: LlmFallbackRole; label: string }[] = [
  { role: "agent", label: "Agent" },
  { role: "light", label: "Light" },
];

/** The ordered fallback models per role, below the provider tables. */
export function LlmFallbacks({ providers }: { providers: LlmProvider[] }) {
  const fallbacks = useLlmFallbacks();
  const models = providers.flatMap((p) => p.models);

  return (
    <>
      <h3 className="text-md font-semibold text-foreground">Fallback models</h3>
      <p className="text-sm text-muted-foreground">
        When a role&apos;s model is unavailable or times out, these are tried in
        order. A budget or policy refusal never falls back, and an internal-only
        Workspace skips external models.
      </p>
      {fallbacks.isError && (
        <ErrorText>
          Could not load the fallback models: {fallbacks.error.message}
        </ErrorText>
      )}
      {fallbacks.data &&
        ROLES.map(({ role, label }) => (
          <RoleList
            key={role}
            role={role}
            label={label}
            ids={
              fallbacks.data.roles.find((r) => r.role === role)?.model_ids ?? []
            }
            models={models}
          />
        ))}
    </>
  );
}

function RoleList({
  role,
  label,
  ids,
  models,
}: {
  role: LlmFallbackRole;
  label: string;
  ids: string[];
  models: LlmProvider["models"];
}) {
  const save = useSetLlmFallbacks();
  const [adding, setAdding] = useState("");
  const nameOf = (id: string) => models.find((m) => m.id === id)?.name ?? id;
  const candidates = models.filter(
    (m) => m.roles.includes(role) && m.test_ok === true && !ids.includes(m.id),
  );

  function replace(next: string[]) {
    save.mutate(
      { role, modelIds: next },
      {
        onSuccess: () => toast.success(`${label} fallback models saved.`),
        onError: (error) => toast.error(error.message),
      },
    );
  }

  function move(index: number, by: -1 | 1) {
    const next = [...ids];
    const moved = next.splice(index, 1);
    next.splice(index + by, 0, ...moved);
    replace(next);
  }

  return (
    <section
      aria-label={`${label} fallback models`}
      className="flex flex-col gap-2 rounded-pill border border-border bg-surface-raised p-3"
    >
      <h4 className="text-sm font-medium">{label} role</h4>
      {ids.length === 0 ? (
        <p className="text-sm text-muted-foreground">No fallback models.</p>
      ) : (
        <ol className="flex flex-col gap-1">
          {ids.map((id, index) => (
            <li
              key={id}
              className="flex items-center gap-2"
              aria-label={`Fallback ${index + 1}: ${nameOf(id)}`}
            >
              <span className="w-5 text-sm text-muted-foreground">
                {index + 1}
              </span>
              <span className="font-mono text-sm">{nameOf(id)}</span>
              <span className="ms-auto flex gap-1">
                <Button
                  size="icon"
                  aria-label={`Move ${nameOf(id)} up`}
                  disabled={index === 0 || save.isPending}
                  onClick={() => move(index, -1)}
                >
                  <ArrowUp aria-hidden />
                </Button>
                <Button
                  size="icon"
                  aria-label={`Move ${nameOf(id)} down`}
                  disabled={index === ids.length - 1 || save.isPending}
                  onClick={() => move(index, 1)}
                >
                  <ArrowDown aria-hidden />
                </Button>
                <Button
                  size="icon"
                  aria-label={`Remove ${nameOf(id)}`}
                  disabled={save.isPending}
                  onClick={() => replace(ids.filter((i) => i !== id))}
                >
                  <X aria-hidden />
                </Button>
              </span>
            </li>
          ))}
        </ol>
      )}
      <div className="flex gap-2">
        <NativeSelect
          aria-label={`Add ${label.toLowerCase()} fallback model`}
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
        >
          <option value="">Choose a tested model</option>
          {candidates.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </NativeSelect>
        <Button
          disabled={!adding || save.isPending}
          onClick={() => {
            replace([...ids, adding]);
            setAdding("");
          }}
        >
          Add fallback
        </Button>
      </div>
    </section>
  );
}
