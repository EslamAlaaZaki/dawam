"use client";

import { useSourceSummary } from "../api/sourceSummary";
import type { SourceSystem } from "../api/systems";
import { Badge } from "../components/ui/badge";
import { DetailList, EmptyState, ErrorText, Panel, h3Classes } from "./parts";

const STATUS_LABELS = {
  not_started: "Not started",
  in_progress: "In progress",
  complete: "Complete",
} as const;

const STATUS_VARIANTS = {
  not_started: "neutral",
  in_progress: "accent",
  complete: "success",
} as const;

/** A Source System's dashboard: how complete its analysis is (spec story 63). */
export function SourceSummaryPanel({
  workspaceId,
  system,
}: {
  workspaceId: string;
  system: SourceSystem;
}) {
  const summary = useSourceSummary(workspaceId, system.id);

  return (
    <Panel aria-labelledby="source-summary-title">
      <h3 id="source-summary-title" className={h3Classes}>
        Source summary
      </h3>
      {summary.isPending && <p>Loading…</p>}
      {summary.isError && (
        <ErrorText>
          Could not load the source summary: {summary.error.message}
        </ErrorText>
      )}
      {summary.isSuccess &&
        (summary.data.has_snapshot ? (
          <DetailList>
            <dt>Source Analysis</dt>
            <dd>
              <Badge variant={STATUS_VARIANTS[summary.data.status]} dot>
                {STATUS_LABELS[summary.data.status]}
              </Badge>
            </dd>
            <dt>Tables</dt>
            <dd>{summary.data.table_count}</dd>
            <dt>Documented</dt>
            <dd>
              {summary.data.documented_pct}% ({summary.data.documented_tables}{" "}
              of {summary.data.table_count})
            </dd>
            <dt>Profiled</dt>
            <dd>
              {summary.data.profiled_pct}% ({summary.data.profiled_tables} of{" "}
              {summary.data.table_count})
            </dd>
            <dt>Relationships found</dt>
            <dd>
              {summary.data.relationships_found} (
              {summary.data.relationships_to_review} to review)
            </dd>
            <dt>PII found</dt>
            <dd>
              {summary.data.pii_found} ({summary.data.pii_to_review} to review)
            </dd>
          </DetailList>
        ) : (
          <EmptyState>
            No Snapshot yet. Extract the schema or import one to see this
            system&apos;s numbers.
          </EmptyState>
        ))}
    </Panel>
  );
}
