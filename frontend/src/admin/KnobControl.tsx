"use client";

import type { KnobInfo, SettingValue } from "../api/documentAi";
import { Input } from "../components/ui/input";
import { Switch } from "../components/ui/switch";
import { NativeSelect } from "./parts";

export const KNOB_GROUPS: { key: KnobInfo["group"]; title: string }[] = [
  { key: "chunking", title: "Chunking" },
  { key: "indexing", title: "Indexing" },
  { key: "retrieval", title: "Retrieval" },
  { key: "reranking", title: "Reranking" },
];

/** A knob's value for reading: on/off, a choice, or a number. */
export function knobText(knob: KnobInfo, value: SettingValue | undefined) {
  if (value === undefined) {
    return "—";
  }
  if (knob.kind === "bool") {
    return value ? "On" : "Off";
  }
  return String(value);
}

/** A number input's text as the knob's value; an empty or invalid entry is kept as text so
 * the API reports it. */
export function parseNumber(text: string): SettingValue {
  const n = Number(text);
  return text.trim() === "" || Number.isNaN(n) ? text : n;
}

/** The control that edits one Document AI knob: a switch, a select or a number field. */
export function KnobControl({
  knob,
  value,
  onChange,
  disabled = false,
  label = knob.label,
  min = knob.min,
  max = knob.max,
}: {
  knob: KnobInfo;
  value: SettingValue;
  onChange: (value: SettingValue) => void;
  disabled?: boolean;
  label?: string;
  min?: number | null;
  max?: number | null;
}) {
  if (knob.kind === "bool") {
    return (
      <label className="flex items-center gap-2 text-sm font-medium">
        <Switch
          checked={value === true}
          onCheckedChange={onChange}
          disabled={disabled}
        />
        {label}
      </label>
    );
  }
  if (knob.kind === "choice") {
    return (
      <label className="flex flex-col gap-1.5 text-sm font-medium">
        {label}
        <NativeSelect
          value={String(value)}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        >
          {knob.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </NativeSelect>
      </label>
    );
  }
  return (
    <label className="flex flex-col gap-1.5 text-sm font-medium">
      {label}
      <Input
        type="number"
        inputMode={knob.kind === "int" ? "numeric" : "decimal"}
        step={knob.kind === "int" ? 1 : "any"}
        min={min ?? undefined}
        max={max ?? undefined}
        value={String(value)}
        disabled={disabled}
        onChange={(e) => onChange(parseNumber(e.target.value))}
        className="max-w-40"
      />
    </label>
  );
}
