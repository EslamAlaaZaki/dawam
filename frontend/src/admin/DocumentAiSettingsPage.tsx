"use client";

import { useState, type FormEvent } from "react";

import {
  useInstallationDocumentAi,
  useReindexEverywhere,
  useSetInstallationDocumentAi,
  type InstallationDocumentAi,
  type KnobInfo,
  type SettingValue,
} from "../api/documentAi";
import { useMe } from "../api/queries";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "../components/ui/tabs";
import { Loading } from "../shell/Loading";
import { KNOB_GROUPS, KnobControl, parseNumber } from "./KnobControl";
import { ErrorText, PageHead, formCardClasses, labelClasses } from "./parts";

type BoundsDraft = Record<string, { min: SettingValue; max: SettingValue }>;

/**
 * The installation's Document AI settings (24.1): every knob of the RAG pipeline, and for
 * each knob Workspace owners may override, the range they may choose in. Admins only.
 */
export function DocumentAiSettingsPage() {
  const me = useMe();
  const isAdmin = me.data?.system_role === "admin";
  const settings = useInstallationDocumentAi({ enabled: isAdmin });
  const update = useSetInstallationDocumentAi();
  // Held here, not in the form, which remounts with every saved version.
  const reindexAll = useReindexEverywhere();
  const [reindex, setReindex] = useState(false);

  let content;
  if (!isAdmin) {
    content = (
      <p className="text-muted-foreground">
        Only admins can change these settings.
      </p>
    );
  } else if (settings.isPending) {
    content = <Loading />;
  } else if (settings.isError) {
    content = (
      <ErrorText>
        Could not load the settings: {settings.error.message}
      </ErrorText>
    );
  } else {
    // Keyed by the version, so the form shows the saved values again after every save.
    content = (
      <SettingsForm
        key={settings.data.version}
        settings={settings.data}
        update={update}
        reindexAll={reindexAll}
        reindex={reindex}
        setReindex={setReindex}
      />
    );
  }
  return (
    <section className="flex flex-col gap-6">
      <PageHead
        title="Document AI"
        description="How documents are chunked, indexed and searched, for every Workspace."
      />
      {content}
    </section>
  );
}

function SettingsForm({
  settings,
  update,
  reindexAll,
  reindex,
  setReindex,
}: {
  settings: InstallationDocumentAi;
  update: ReturnType<typeof useSetInstallationDocumentAi>;
  reindexAll: ReturnType<typeof useReindexEverywhere>;
  reindex: boolean;
  setReindex: (on: boolean) => void;
}) {
  const [values, setValues] = useState<Record<string, SettingValue>>(
    settings.values,
  );
  const [bounds, setBounds] = useState<BoundsDraft>(settings.bounds);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    reindexAll.reset();
    const before = settings.values;
    update.mutate(
      {
        version: settings.version,
        values,
        bounds: Object.fromEntries(
          Object.entries(bounds).map(([key, b]) => [
            key,
            { min: b.min as number, max: b.max as number },
          ]),
        ),
      },
      {
        onSuccess: (saved) => {
          toast.success("Document AI settings saved.");
          setReindex(
            saved.knobs.some(
              (k) => k.reindex && saved.values[k.key] !== before[k.key],
            ),
          );
        },
      },
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      aria-label="Document AI settings"
      className="flex flex-col gap-4"
    >
      <Tabs defaultValue={KNOB_GROUPS[0]?.key} className="gap-4">
        <TabsList
          aria-label="Document AI setting groups"
          className="max-w-xl px-0"
        >
          {KNOB_GROUPS.map((group) => (
            <TabsTrigger key={group.key} value={group.key}>
              {group.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {KNOB_GROUPS.map((group) => (
          <TabsContent key={group.key} value={group.key}>
            <section aria-label={group.title} className={formCardClasses}>
              {settings.knobs
                .filter((knob) => knob.group === group.key)
                .map((knob) => (
                  <KnobRow
                    key={knob.key}
                    knob={knob}
                    value={values[knob.key] ?? knob.default}
                    onChange={(v) => setValues({ ...values, [knob.key]: v })}
                    bounds={bounds[knob.key]}
                    onBounds={(b) => setBounds({ ...bounds, [knob.key]: b })}
                  />
                ))}
            </section>
          </TabsContent>
        ))}
      </Tabs>
      {update.isError && <ErrorText>{update.error.message}</ErrorText>}
      {reindex && (
        <div className="flex flex-col gap-2 rounded-md border border-border px-3 py-2">
          <p role="status" className="text-sm text-muted-foreground">
            The new settings apply to documents indexed from now on. Re-index
            every Workspace&apos;s documents to apply them to the others?
          </p>
          {reindexAll.isError && (
            <ErrorText>{reindexAll.error.message}</ErrorText>
          )}
          <div className="flex gap-2">
            <Button
              type="button"
              disabled={reindexAll.isPending}
              onClick={() =>
                reindexAll.mutate(undefined, {
                  onSuccess: () => {
                    toast.success("Re-indexing every Workspace's documents.");
                    setReindex(false);
                  },
                })
              }
            >
              Re-index now
            </Button>
            <Button type="button" onClick={() => setReindex(false)}>
              Not now
            </Button>
          </div>
        </div>
      )}
      <div className="flex items-center gap-2">
        <Button type="submit" variant="primary" disabled={update.isPending}>
          Save settings
        </Button>
      </div>
    </form>
  );
}

function KnobRow({
  knob,
  value,
  onChange,
  bounds,
  onBounds,
}: {
  knob: KnobInfo;
  value: SettingValue;
  onChange: (value: SettingValue) => void;
  bounds?: { min: SettingValue; max: SettingValue };
  onBounds: (bounds: { min: SettingValue; max: SettingValue }) => void;
}) {
  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3 first-of-type:border-t-0 first-of-type:pt-0">
      <div className="flex flex-wrap items-end gap-3">
        <KnobControl knob={knob} value={value} onChange={onChange} />
        <Badge variant={knob.owner ? "success" : "neutral"}>
          {knob.owner ? "Owners may override" : "Admin only"}
        </Badge>
      </div>
      <p className="text-sm text-muted-foreground">{knob.description}</p>
      {bounds && (
        <div className="flex flex-wrap gap-3">
          <label className={labelClasses}>
            {`${knob.label}: owners' minimum`}
            <Input
              type="number"
              step={knob.kind === "int" ? 1 : "any"}
              value={String(bounds.min)}
              onChange={(e) =>
                onBounds({ ...bounds, min: parseNumber(e.target.value) })
              }
              className="max-w-40"
            />
          </label>
          <label className={labelClasses}>
            {`${knob.label}: owners' maximum`}
            <Input
              type="number"
              step={knob.kind === "int" ? 1 : "any"}
              value={String(bounds.max)}
              onChange={(e) =>
                onBounds({ ...bounds, max: parseNumber(e.target.value) })
              }
              className="max-w-40"
            />
          </label>
        </div>
      )}
    </div>
  );
}
