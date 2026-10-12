import { ShieldAlert } from "lucide-react";

/** The PII marker of the diagrams: an icon with a label, never colour alone. */
export function PiiMarker({ label }: { label: string }) {
  return (
    <span className="pii-marker" role="img" aria-label={label} title={label}>
      <ShieldAlert aria-hidden />
    </span>
  );
}
