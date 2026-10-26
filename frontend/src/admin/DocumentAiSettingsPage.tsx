"use client";

import { useState, type FormEvent } from "react";

import {
  useInstallationDocumentAi,
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
import { Loading } from "../shell/Loading";
import { KNOB_GROUPS, KnobControl, parseNumber } from "./KnobControl";
import {
  ErrorText,
  PageHead,
  formCardClasses,
  h3Classes,
  labelClasses,
} from "./parts";

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
}: {
  settings: InstallationDocumentAi;
  update: ReturnType<typeof useSetInstallationDocumentAi>;
}) {
  const [values, setValues] = useState<Record<string, SettingValue>>(
    settings.values,
  );
  const [bounds, setBounds] = useState<BoundsDraft>(settings.bounds);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
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
      { onSuccess: () => toast.success("Document AI settings saved.") },
    );
  }

  return (
    <form
      onSubmit={onSubmit}
      aria-label="Document AI settings"
      className="flex flex-col gap-4"
    >
      {KNOB_GROUPS.map((group) => (
        <section
          key={group.key}
          aria-label={group.title}
          className={formCardClasses}
        >
          <h3 className={h3Classes}>{group.title}</h3>
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
      ))}
      {update.isError && <ErrorText>{update.error.message}</ErrorText>}
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
