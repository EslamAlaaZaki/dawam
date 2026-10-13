// The pieces of an Assistant message: who sent it, when, and the reply's markdown.
import { Sparkles } from "lucide-react";
import type { ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { cn } from "../lib/utils";
import { monoClasses } from "./parts";

/** Up to two letters for an email's avatar: "grace.hopper@…" is "GH", "ada@…" is "AD". */
export function initials(email: string): string {
  const name = email.split("@")[0] ?? "";
  const words = name.split(/[._\-+\s]+/).filter(Boolean);
  const letters =
    words.length > 1 ? `${words[0]?.[0] ?? ""}${words[1]?.[0] ?? ""}` : name.slice(0, 2);
  return (letters || "?").toUpperCase();
}

// Theme pairs that already meet the contrast checks: text-11 on its tint.
const AVATAR_COLOURS = [
  "bg-jade-3 text-jade-11",
  "bg-amber-3 text-amber-11",
  "bg-grass-3 text-grass-11",
  "bg-red-3 text-red-11",
  "bg-sand-4 text-sand-12",
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
      <span aria-hidden className={cn(avatarClasses, "bg-accent-solid text-on-accent")}>
        <Sparkles className="size-3.5" />
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
    <time dateTime={at} title={when.toLocaleString()} className="text-xs text-sand-11">
      {relativeTime(when)}
    </time>
  );
}

const MARKDOWN: Components = {
  p: ({ children }) => <p className="my-1.5 first:mt-0 last:mb-0">{children}</p>,
  ul: ({ children }) => <ul className="my-1.5 list-disc pl-5">{children}</ul>,
  ol: ({ children }) => <ol className="my-1.5 list-decimal pl-5">{children}</ol>,
  h1: ({ children }) => <p className="mt-2 mb-1 text-md font-semibold">{children}</p>,
  h2: ({ children }) => <p className="mt-2 mb-1 font-semibold">{children}</p>,
  h3: ({ children }) => <p className="mt-2 mb-1 font-semibold">{children}</p>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noreferrer noopener" className="text-jade-11 underline">
      {children}
    </a>
  ),
  blockquote: ({ children }) => (
    <blockquote className="my-1.5 border-l-2 border-sand-6 pl-3 text-sand-11">
      {children}
    </blockquote>
  ),
  pre: ({ children }) => (
    <pre className={cn("my-1.5 overflow-x-auto rounded-md bg-sand-3 p-2.5", monoClasses)}>
      {children}
    </pre>
  ),
  code: ({ children }) => (
    <code className={cn("rounded-sm bg-sand-3 px-1", monoClasses)}>{children}</code>
  ),
  table: ({ children }) => (
    <div className="my-1.5 overflow-x-auto">
      <table className="border-collapse text-sm">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-sand-5 px-2 py-1 text-left font-semibold">{children}</th>
  ),
  td: ({ children }) => <td className="border border-sand-5 px-2 py-1">{children}</td>,
};

/** A reply's markdown. Raw HTML in it is never rendered. */
export function Markdown({ children }: { children: string }): ReactNode {
  return (
    <div className="break-words">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MARKDOWN}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
