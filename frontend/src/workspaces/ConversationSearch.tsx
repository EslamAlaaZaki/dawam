"use client";

import { Search } from "lucide-react";
import { useEffect, useState } from "react";

import { useSearchConversations, type SearchHit } from "../api/assistant";
import { Badge } from "../components/ui/badge";
import { Input } from "../components/ui/input";
import { ErrorText, Hint } from "./parts";

/** How long typing pauses before the search runs. */
const DEBOUNCE_MS = 250;

const KIND_LABEL: Record<SearchHit["kind"], string> = {
  conversation: "Title",
  message: "Message",
  job_summary: "Job summary",
};

/** The search box above the conversation list: what is typed, once it settles. */
export function useSearchTerm() {
  const [typed, setTyped] = useState("");
  const [term, setTerm] = useState("");
  useEffect(() => {
    const timer = setTimeout(() => setTerm(typed.trim()), DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [typed]);
  return { typed, setTyped, term: typed.trim() === "" ? "" : term };
}

export function ConversationSearchBox({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="relative px-3 pb-2">
      <Search
        aria-hidden
        className="pointer-events-none absolute top-2.5 left-5 size-4 text-subtle-foreground"
      />
      <Input
        type="search"
        aria-label="Search conversations"
        placeholder="Search conversations"
        maxLength={200}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="pl-8 text-sm"
      />
    </div>
  );
}

/** The conversations and job summaries matching `term`, among those the member may read. */
export function ConversationSearchResults({
  workspaceId,
  term,
  disabled,
  onOpen,
}: {
  workspaceId: string;
  term: string;
  disabled: boolean;
  onOpen: (conversationId: string) => void;
}) {
  const results = useSearchConversations(workspaceId, term);
  return (
    <div className="flex flex-1 flex-col overflow-y-auto px-2 pt-1 pb-3">
      {results.isError && (
        <div className="px-1">
          <ErrorText>Could not search the conversations.</ErrorText>
        </div>
      )}
      {results.isSuccess && results.data.length === 0 && (
        <Hint className="px-1">Nothing matches.</Hint>
      )}
      <ul aria-label="Search results" className="flex flex-col gap-1">
        {results.data?.map((hit) => (
          <li key={`${hit.conversation_id}:${hit.message_id ?? "title"}`}>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onOpen(hit.conversation_id)}
              className="flex w-full flex-col gap-0.5 rounded-md px-2 py-1.5 text-left text-sm text-foreground transition-colors duration-120 hover:bg-muted disabled:opacity-60"
            >
              <span className="flex items-center gap-1">
                <span className="min-w-0 truncate font-medium">
                  {hit.conversation_title}
                </span>
                {!hit.mine && (
                  <span className="shrink-0 text-xs text-subtle-foreground">
                    (shared)
                  </span>
                )}
              </span>
              <span className="line-clamp-2 text-xs text-muted-foreground">
                {hit.snippet}
              </span>
              <Badge
                variant={hit.kind === "job_summary" ? "accent" : "neutral"}
                className="w-fit"
              >
                {KIND_LABEL[hit.kind]}
              </Badge>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
