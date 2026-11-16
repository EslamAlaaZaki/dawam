"use client";

import { Check, Pencil, X } from "lucide-react";
import { useState } from "react";

import {
  CLASSIFICATIONS,
  FIELD_LABELS,
  useAcceptEditedSuggestion,
  useAcceptSuggestions,
  useEditCatalogField,
  useRejectSuggestions,
  type CatalogField,
  type CatalogSuggestion,
  type CatalogTarget,
} from "../api/catalogSuggestions";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { NativeSelect } from "./parts";

/** The dashed mark of a pending AI suggestion: neutral, never the Assistant's gradient or star mark. */
export function SuggestedTag() {
  return (
    <Badge className="border border-dashed border-border-strong bg-transparent">
      AI suggested
    </Badge>
  );
}

/** The small grey mark of accepted AI text, until a person edits it. */
export function AiTag() {
  return <Badge title="Written by the AI, accepted by a person">AI</Badge>;
}

/** A suggested or saved value: the classification as a badge, anything else as text. */
export function CatalogText({ field, value }: { field: CatalogField; value: string }) {
  return field === "classification" ? <Badge>{value}</Badge> : <span>{value}</span>;
}

/** The field's input: a select for the classification, a text field otherwise. */
export function CatalogInput({
  field,
  value,
  onChange,
  onEnter,
  onEscape,
  allowEmpty,
}: {
  field: CatalogField;
  value: string;
  onChange: (value: string) => void;
  onEnter: () => void;
  onEscape: () => void;
  allowEmpty: boolean;
}) {
  const label = FIELD_LABELS[field];
  const keys = {
    onKeyDown: (event: React.KeyboardEvent) => {
      if (event.key === "Enter") {
        event.preventDefault();
        onEnter();
      } else if (event.key === "Escape") {
        onEscape();
      }
    },
  };
  return field === "classification" ? (
    <NativeSelect
      aria-label={label}
      className="w-auto min-w-36"
      autoFocus
      value={value}
      onChange={(event) => onChange(event.target.value)}
      {...keys}
    >
      {allowEmpty && <option value="">Not classified</option>}
      {CLASSIFICATIONS.map((c) => (
        <option key={c} value={c}>
          {c}
        </option>
      ))}
    </NativeSelect>
  ) : (
    <Input
      aria-label={label}
      className="h-8 min-w-48 flex-1"
      autoFocus
      value={value}
      onChange={(event) => onChange(event.target.value)}
      {...keys}
    />
  );
}

/**
 * One catalog field in place (a table's description, classification or SCD hint, or a
 * column's description): a pending AI suggestion with accept, edit and reject, otherwise the
 * saved value with a pencil to edit it by hand. Only `canEdit` (owners and editors) sees the
 * suggestion and the pencil; everyone sees the saved value, with the AI tag until a person
 * edits it.
 */
export function CatalogValue({
  workspaceId,
  systemId,
  target,
  field,
  value,
  ai,
  pending,
  canEdit,
  empty,
  subject,
}: {
  workspaceId: string;
  systemId: string;
  target: CatalogTarget;
  field: CatalogField;
  value: string | null | undefined;
  ai: boolean;
  pending: CatalogSuggestion | undefined;
  canEdit: boolean;
  empty: string;
  /** What the field belongs to, e.g. `customers.city`, named by the buttons. */
  subject: string;
}) {
  const accept = useAcceptSuggestions(workspaceId, systemId);
  const reject = useRejectSuggestions(workspaceId, systemId);
  const acceptEdited = useAcceptEditedSuggestion(workspaceId, systemId);
  const edit = useEditCatalogField(workspaceId, systemId);
  const [editing, setEditing] = useState<"suggestion" | "value" | null>(null);
  const [draft, setDraft] = useState("");
  const label = `${subject} ${FIELD_LABELS[field].toLowerCase()}`;
  const onError = (error: Error) => toast.error(error.message);
  const busy =
    accept.isPending || reject.isPending || acceptEdited.isPending || edit.isPending;

  const begin = (what: "suggestion" | "value") => {
    setDraft(what === "suggestion" ? (pending?.value ?? "") : (value ?? ""));
    setEditing(what);
  };
  const cancel = () => setEditing(null);

  const saveSuggestion = () => {
    const text = draft.trim();
    if (!pending || !text) {
      return;
    }
    const done = { onSuccess: () => setEditing(null), onError };
    if (text === pending.value) {
      accept.mutate([pending.id], done);
    } else {
      acceptEdited.mutate({ id: pending.id, value: text }, done);
    }
  };
  const saveValue = () => {
    const text = draft.trim();
    edit.mutate(
      { target, field, value: text === "" ? null : text },
      { onSuccess: () => setEditing(null), onError },
    );
  };

  if (canEdit && pending && editing === "suggestion") {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <SuggestedTag />
        <CatalogInput
          field={field}
          value={draft}
          onChange={setDraft}
          onEnter={saveSuggestion}
          onEscape={cancel}
          allowEmpty={false}
        />
        <Button type="button" size="sm" disabled={busy || !draft.trim()} onClick={saveSuggestion}>
          Save and accept
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={cancel}>
          Cancel
        </Button>
      </span>
    );
  }
  if (canEdit && pending) {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <SuggestedTag />
        <CatalogText field={field} value={pending.value} />
        <span className="inline-flex gap-0.5">
          <IconAction
            label={`Accept the suggested ${label}`}
            disabled={busy}
            onClick={() =>
              accept.mutate([pending.id], {
                onSuccess: (result) => {
                  if (result.skipped.length > 0) {
                    toast.error("That suggestion is no longer pending.");
                  }
                },
                onError,
              })
            }
          >
            <Check aria-hidden />
          </IconAction>
          <IconAction
            label={`Edit the suggested ${label} before accepting`}
            disabled={busy}
            onClick={() => begin("suggestion")}
          >
            <Pencil aria-hidden />
          </IconAction>
          <IconAction
            label={`Reject the suggested ${label}`}
            disabled={busy}
            onClick={() => reject.mutate([pending.id], { onError })}
          >
            <X aria-hidden />
          </IconAction>
        </span>
      </span>
    );
  }
  if (canEdit && editing === "value") {
    return (
      <span className="flex flex-wrap items-center gap-2">
        <CatalogInput
          field={field}
          value={draft}
          onChange={setDraft}
          onEnter={saveValue}
          onEscape={cancel}
          allowEmpty
        />
        <Button type="button" size="sm" disabled={busy} onClick={saveValue}>
          Save
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={cancel}>
          Cancel
        </Button>
      </span>
    );
  }
  return (
    <span className="flex flex-wrap items-center gap-2">
      {value ? (
        <>
          <CatalogText field={field} value={value} />
          {ai && <AiTag />}
        </>
      ) : (
        <span className="text-subtle-foreground">{empty}</span>
      )}
      {canEdit && (
        <IconAction label={`Edit ${label}`} onClick={() => begin("value")}>
          <Pencil aria-hidden />
        </IconAction>
      )}
    </span>
  );
}

/** A small icon button that names its action. */
export function IconAction({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      variant="ghost"
      size="icon"
      className="size-7"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}
