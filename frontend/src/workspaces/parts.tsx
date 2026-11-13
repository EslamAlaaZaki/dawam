import { Check, Copy, Info } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ComponentProps, ReactNode } from "react";

import { Button } from "../components/ui/button";
import { toast } from "../components/ui/sonner";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";
import { cn } from "../lib/utils";

export {
  EmptyState,
  formCardClasses,
  h3Classes,
  ErrorText,
  NativeSelect,
  PageHead,
  TableCard,
  labelClasses,
} from "../admin/parts";

/** The prototype's mono face, for identifiers (System Codes, table and column names, SQL). */
export const monoClasses = "font-mono text-mono";

/** Headings inside a panel; the reset leaves them unstyled. */
export const h4Classes = "text-base font-semibold";
export const h5Classes = "text-sm font-semibold text-muted-foreground";

/** A panel's section: a card around its content. */
export function Panel({ className, ...props }: ComponentProps<"section">) {
  return (
    <section
      className={cn(
        "flex flex-col gap-3 rounded-pill border border-border bg-surface-raised p-5",
        className,
      )}
      {...props}
    />
  );
}

/** A secondary line: what a control does, or what a number means. */
export function Hint({ className, ...props }: ComponentProps<"p">) {
  return (
    <p className={cn("text-sm text-muted-foreground", className)} {...props} />
  );
}

/** A success note, shown inline where the user acted. */
export function OkText({ className, ...props }: ComponentProps<"p">) {
  return (
    <p className={cn("text-sm text-success-strong", className)} {...props} />
  );
}

/** Term and value pairs; use plain `dt` and `dd` inside. */
export function DetailList({ className, ...props }: ComponentProps<"dl">) {
  return (
    <dl
      className={cn(
        "grid grid-cols-[max-content_minmax(0,1fr)] gap-x-6 gap-y-1.5 [&_dd]:min-w-0 [&_dd]:break-words [&_dt]:font-medium [&_dt]:text-muted-foreground",
        className,
      )}
      {...props}
    />
  );
}

/** The row of a form's buttons. */
export function Actions({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      {children}
    </div>
  );
}

/** A download or other plain link. */
export const linkClasses =
  "font-medium text-primary underline underline-offset-2 hover:text-primary-hover";

/** A button that reads as a mono identifier link (a table or routine to open). */
export const linkButtonClasses =
  "cursor-pointer rounded-sm font-mono text-mono text-primary underline-offset-2 hover:underline";

/** Code and SQL. */
export const preClasses =
  "max-h-80 overflow-auto rounded-lg border border-border bg-surface p-3 font-mono text-mono";

const PREVIEW_LINES = 12;

/**
 * Code and SQL, shown in a box that never grows: at most 12 lines, each cut at the box
 * width. A copy button takes the full text.
 */
export function CodePreview({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);

  const lines = code.split("\n");
  const shown = lines.slice(0, PREVIEW_LINES);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      toast.error("Could not copy the definition.");
      return;
    }
    setCopied(true);
    toast.success("Definition copied.");
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1500);
  }

  return (
    <div className="relative rounded-lg border border-border bg-surface">
      <pre className="m-0 overflow-hidden p-3 pr-12 font-mono text-mono">
        {shown.map((line, i) => (
          <div key={i} className="truncate whitespace-pre">
            {line || " "}
          </div>
        ))}
        {lines.length > PREVIEW_LINES && <div>…</div>}
      </pre>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="absolute top-2 right-2"
            aria-label="Copy definition"
            onClick={copy}
          >
            {copied ? <Check aria-hidden /> : <Copy aria-hidden />}
          </Button>
        </TooltipTrigger>
        <TooltipContent>Copy definition</TooltipContent>
      </Tooltip>
    </div>
  );
}

/** A bordered list of rows, for object lists. */
export const listCardClasses =
  "divide-y divide-border overflow-hidden rounded-lg border border-border bg-surface-raised";

/** A native checkbox in the accent colour; its label wraps it in a row. */
export const checkClasses = "size-4 accent-primary";
export const checkLabelClasses = "flex items-center gap-2 text-sm font-medium";

/** The fieldset of a form: no border, controls stacked like the card around it. */
export const fieldsetClasses = "m-0 flex min-w-0 flex-col gap-3.5 border-0 p-0";

const calloutTones = {
  neutral: "bg-muted text-muted-foreground",
  success: "bg-success-subtle text-foreground [&>svg]:text-success-strong",
  warning: "bg-warning-subtle text-foreground [&>svg]:text-warning-strong",
  danger: "bg-danger-subtle text-foreground [&>svg]:text-danger-strong",
} as const;

/** A tinted box with an icon: a notice, or the outcome of a test or a check. */
export function Callout({
  tone = "neutral",
  icon,
  className,
  children,
  ...props
}: Omit<ComponentProps<"div">, "children"> & {
  tone?: keyof typeof calloutTones;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-lg px-3.5 py-3 text-sm [&>svg]:mt-0.5 [&>svg]:size-4 [&>svg]:shrink-0",
        calloutTones[tone],
        className,
      )}
      {...props}
    >
      {icon}
      <div className="flex min-w-0 flex-1 flex-col gap-2">{children}</div>
    </div>
  );
}

/** An ⓘ button whose tooltip explains a column (hover or keyboard focus; Escape closes). */
export function InfoTip({
  label,
  ariaLabel,
  contentClassName,
  children,
}: {
  label: string;
  /** The button's accessible name; defaults to "What is <label>?". */
  ariaLabel?: string;
  contentClassName?: string;
  children: ReactNode;
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={ariaLabel ?? `What is ${label.toLowerCase()}?`}
          className="ms-1 inline-grid size-4 cursor-help place-items-center rounded-full align-middle text-subtle-foreground hover:text-primary focus-visible:text-primary"
          onClick={(event) => event.stopPropagation()}
        >
          <Info aria-hidden className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent
        className={cn(
          "flex flex-col gap-1.5 border border-border bg-surface-raised p-3 leading-normal text-foreground shadow-overlay",
          contentClassName,
        )}
      >
        {children}
      </TooltipContent>
    </Tooltip>
  );
}

export function TipItems({ items }: { items: [ReactNode, string][] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 p-0">
      {items.map(([badge, text]) => (
        <li key={text}>
          <span className="me-1.5">{badge}</span>
          {text}
        </li>
      ))}
    </ul>
  );
}
