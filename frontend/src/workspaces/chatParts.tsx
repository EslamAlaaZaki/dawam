// The pieces of an Assistant message: who sent it, when, and the reply's markdown.
import { Children, isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "../lib/utils";
import { MermaidDiagram } from "./MermaidDiagram";
import { monoClasses } from "./parts";

/** Up to two letters for an email's avatar: "grace.hopper@…" is "GH", "ada@…" is "AD". */
export function initials(email: string): string {
  const name = email.split("@")[0] ?? "";
  const words = name.split(/[._\-+\s]+/).filter(Boolean);
  const letters =
    words.length > 1 ? `${words[0]?.[0] ?? ""}${words[1]?.[0] ?? ""}` : name.slice(0, 2);
  return (letters || "?").toUpperCase();
}

// Theme pairs that already meet the contrast checks: strong text on its subtle tint.
const AVATAR_COLOURS = [
  "bg-primary-subtle text-primary",
  "bg-warning-subtle text-warning-strong",
  "bg-success-subtle text-success-strong",
  "bg-info-subtle text-info-strong",
  "bg-muted text-foreground",
];

/** The same email always gets the same colour. */
function colourOf(email: string): string {
  let hash = 0;
  for (const char of email) {
    hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  }
  return AVATAR_COLOURS[hash % AVATAR_COLOURS.length] ?? "";
}

const avatarClasses =
  "flex size-7 shrink-0 items-center justify-center rounded-full text-2xs font-semibold";

export function Avatar({ email }: { email: string | null }) {
  if (email === null) {
    return (
      <span aria-hidden className={cn(avatarClasses, "bg-ai-gradient text-sm text-white")}>
        ✦
      </span>
    );
  }
  return (
    <span aria-hidden className={cn(avatarClasses, colourOf(email))}>
      {initials(email)}
    </span>
  );
}

/** "just now", "5 min ago", "3 h ago", "2 days ago", then the date. */
export function relativeTime(when: Date, now: Date = new Date()): string {
  const minutes = Math.floor((now.getTime() - when.getTime()) / 60_000);
  if (minutes < 1) {
    return "just now";
  }
  if (minutes < 60) {
    return `${minutes} min ago`;
  }
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    return `${hours} h ago`;
  }
  const days = Math.floor(hours / 24);
  if (days < 7) {
    return days === 1 ? "yesterday" : `${days} days ago`;
  }
  return when.toLocaleDateString();
}

/** The relative time, with the full date and time on hover. */
export function MessageTime({ at }: { at: string }) {
  const when = new Date(at);
  return (
    <time dateTime={at} title={when.toLocaleString()} className="text-xs text-subtle-foreground">
      {relativeTime(when)}
    </time>
  );
}

const MARKDOWN: Components = {
  p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-1.5 flex list-disc flex-col gap-0.5 pl-5 marker:text-subtle-foreground">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 flex list-decimal flex-col gap-0.5 pl-5 marker:text-subtle-foreground">{children}</ol>,
  h1: ({ children }) => <p className="mt-3 mb-1.5 text-lg font-semibold first:mt-0">{children}</p>,
  h2: ({ children }) => <p className="mt-3 mb-1 text-md font-semibold first:mt-0">{children}</p>,
  h3: ({ children }) => <p className="mt-2.5 mb-1 font-semibold first:mt-0">{children}</p>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer noopener" className="font-medium text-primary underline underline-offset-2 hover:text-primary-hover">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-1.5 border-l-2 border-border-strong pl-3 text-muted-foreground">
      {children}
    </blockquote>
  ),
  pre: ({ children }) => {
    const only = Children.toArray(children)[0];
    if (isValidElement<{ className?: string; children?: ReactNode }>(only)) {
      if ((only.props.className ?? "").split(" ").includes("language-mermaid")) {
        return <MermaidDiagram code={String(only.props.children ?? "").trim()} />;
      }
    }
    return (
    <pre
      className={cn(
        "my-2 overflow-x-auto rounded-md border border-border bg-surface-raised p-3 leading-relaxed text-foreground [&_code]:border-0 [&_code]:bg-transparent [&_code]:p-0",
        monoClasses,
      )}
    >
      {children}
    </pre>
    );
  },
  code: ({ children }) => (
    <code
      className={cn("rounded-sm border border-border bg-surface-raised px-1", monoClasses)}
    >{children}</code>
  ),
  table: ({ children }) => (
    <div className="my-2 overflow-x-auto rounded-md border border-border bg-surface-raised">
      <table className="w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border-b border-border bg-muted px-2.5 py-1.5 text-left text-xs font-semibold text-muted-foreground">{children}</th>
  ),
  td: ({ children }) => <td className="border-t border-border px-2.5 py-1.5">{children}</td>,
};

/** A reply's markdown. Raw HTML in it is never rendered; a `mermaid` code block is drawn. */
export function Markdown({ children }: { children: string }): ReactNode {
  return (
    <div className="break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
