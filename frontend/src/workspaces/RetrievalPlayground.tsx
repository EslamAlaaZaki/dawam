"use client";

import { useState, type FormEvent } from "react";

import {
  useExplainSearch,
  type RetrievalTrace,
  type TraceEntry,
} from "../api/files";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { ErrorText, Hint, Panel, h4Classes, monoClasses } from "./parts";

function StageTable({
  title,
  entries,
  empty,
  floor,
}: {
  title: string;
  entries: TraceEntry[];
  empty: string;
  floor?: boolean;
}) {
  return (
    <section aria-label={title} className="flex flex-col gap-2">
      <h5 className="text-sm font-semibold text-muted-foreground">{title}</h5>
      {entries.length === 0 ? (
        <Hint>{empty}</Hint>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead scope="col">#</TableHead>
              <TableHead scope="col">Document</TableHead>
              <TableHead scope="col">Section</TableHead>
              <TableHead scope="col">Score</TableHead>
              {floor && <TableHead scope="col">Relevance</TableHead>}
            </TableRow>
          </TableHeader>
          <TableBody>
            {entries.map((entry) => (
              <TableRow key={`${entry.rank}-${entry.file_id}-${entry.section}`}>
                <TableCell>{entry.rank}</TableCell>
                <TableCell className={monoClasses}>{entry.document}</TableCell>
                <TableCell>{entry.section}</TableCell>
                <TableCell className={monoClasses}>
                  {entry.score.toFixed(4)}
                </TableCell>
                {floor && (
                  <TableCell>
                    <Badge variant={entry.above_floor ? "success" : "neutral"}>
                      {entry.above_floor ? "Above floor" : "Below floor"}
                    </Badge>
                  </TableCell>
                )}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </section>
  );
}

function Stages({ trace }: { trace: RetrievalTrace }) {
  return (
    <div className="flex flex-col gap-5">
      {trace.queries.length > 1 && (
        <Hint>Searched as: {trace.queries.join(" | ")}</Hint>
      )}
      <StageTable
        title="Full-text ranking"
        entries={trace.full_text}
        empty="Nothing matched the words."
        floor
      />
      <StageTable
        title="Vector ranking"
        entries={trace.vector}
        empty={
          trace.vector_used
            ? "No passage matched by meaning."
            : "Not used: the Workspace's settings keep documents from the embedding model, or it has none."
        }
        floor
      />
      <StageTable
        title="Fused ranking"
        entries={trace.fused}
        empty="Nothing cleared the relevance floors."
      />
      <StageTable
        title="Reranked"
        entries={trace.reranked_results}
        empty="Not used: reranking is off, has no model, or the settings keep documents from it."
      />
      <StageTable
        title="Final results"
        entries={trace.final}
        empty="No results."
      />
    </div>
  );
}

/**
 * The retrieval playground: type a query and see each stage of document search (full-text,
 * vector, fused, reranked, final) with its scores, under the Workspace's effective settings.
 */
export function RetrievalPlayground({ workspaceId }: { workspaceId: string }) {
  const [query, setQuery] = useState("");
  const explain = useExplainSearch(workspaceId);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (query.trim()) {
      explain.mutate(query.trim());
    }
  }

  return (
    <Panel aria-labelledby="playground-title">
      <h4 id="playground-title" className={h4Classes}>
        Retrieval playground
      </h4>
      <form onSubmit={submit} className="flex gap-2">
        <Input
          aria-label="Query"
          placeholder="What would a user ask?"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Button type="submit" disabled={explain.isPending || !query.trim()}>
          Run query
        </Button>
      </form>
      {explain.isError && <ErrorText>{explain.error.message}</ErrorText>}
      {explain.data && <Stages trace={explain.data} />}
    </Panel>
  );
}
