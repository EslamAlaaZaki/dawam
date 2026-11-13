"use client";

import { Info, Plus, X } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { useMemo, useState, type KeyboardEvent, type ReactNode } from "react";

import { ApiError } from "../api/client";
import {
  KPI_STATUS_LABELS,
  useCreateKpi,
  useDecideKpi,
  useDecideRuleSuggestion,
  useDeleteKpi,
  useKpiLinks,
  useKpiList,
  useUpdateKpi,
  type Kpi,
  type KpiFeasibility,
  type KpiScope,
  type KpiStatus,
} from "../api/kpis";
import { useMembers } from "../api/members";
import { useSourceSystems, type SourceSystem } from "../api/systems";
import { allows, type Workspace } from "../api/workspaces";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetDescription,
  SheetTitle,
} from "../components/ui/sheet";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "../components/ui/tooltip";
import { cn } from "../lib/utils";
import { Loading } from "../shell/Loading";
import { KpiFields, readKpiFields } from "./KpiForm";
import {
  SystemFilterSelect,
  UnderBadge,
  documentedUnder as documentedUnderOf,
  isRuleSuggestion,
  matchesSystem,
  ruleSuggestionRow,
  type KpiRow,
  type SystemFilter,
} from "./KpiDwParts";
import { KpiSuggestJob } from "./KpiSuggestJob";
import { DecidedBy } from "./PiiPanel";
import {
  DetailList,
  EmptyState,
  ErrorText,
  Hint,
  NativeSelect,
  TableCard,
  h3Classes,
  labelClasses,
  linkButtonClasses,
  monoClasses,
} from "./parts";

const STATUS_VARIANTS = { draft: "neutral", in_review: "warning", approved: "success" } as const;

const STATUSES = Object.keys(KPI_STATUS_LABELS) as KpiStatus[];
const CONFLICT_MESSAGE =
  "Someone else changed this KPI since you opened it. The latest version is loaded: check your edits, then save again.";

// ---------------------------------------------------------------------------------------
// The review: tabs, origin, status, formula and feasibility badges, and their ⓘ tooltips.

type KpiTab = "kpis" | "suggested" | "accepted" | "rejected";

const TABS: readonly { tab: KpiTab; label: string; empty: [string, string] }[] = [
  { tab: "kpis", label: "KPIs", empty: ["No KPIs yet", "Add one, or accept a suggestion."] },
  {
    tab: "suggested",
    label: "Suggested",
    empty: ["Nothing to review", "New AI suggestions show up here."],
  },
  {
    tab: "accepted",
    label: "Accepted",
    empty: ["No accepted suggestions", "Suggestions you accept are listed here."],
  },
  {
    tab: "rejected",
    label: "Rejected",
    empty: ["No rejected suggestions", "Rejected and removed suggestions are listed here."],
  },
];

/** Which KPIs a tab holds: the KPIs tab is every live and accepted one. */
function inTab(kpi: Kpi, tab: KpiTab): boolean {
  return tab === "kpis"
    ? kpi.review_state === "live" || kpi.review_state === "accepted"
    : kpi.review_state === tab;
}

type OriginFilter = "all" | "manual" | "ai" | "rule";

const ORIGIN_FILTERS: { origin: OriginFilter; label: string }[] = [
  { origin: "all", label: "All" },
  { origin: "manual", label: "Manual" },
  { origin: "ai", label: "AI" },
  // Rule suggestions come from the DW model, so only the Data Warehouse page has them.
  { origin: "rule", label: "Rule" },
];

const matchesOrigin = (kpi: Kpi, origin: OriginFilter) =>
  origin === "all" || (origin === "manual" ? kpi.origin === "user" : kpi.origin === origin);

const matchesSearch = (kpi: Kpi, query: string) =>
  query === "" ||
  `${kpi.name}\n${kpi.definition}\n${kpi.owner}`.toLowerCase().includes(query.toLowerCase());

const DECISION_VERBS = {
  added: "Added by",
  accepted: "Accepted by",
  rejected: "Rejected by",
  removed: "Removed by",
} as const;

const FEASIBILITY: Record<KpiFeasibility, { label: string; variant: "success" | "warning" | "neutral" }> = {
  computable: { label: "Computable", variant: "success" },
  needs_mapping: { label: "Needs mapping", variant: "warning" },
  needs_data: { label: "Needs data", variant: "neutral" },
};

function OriginBadge({ origin }: { origin: Kpi["origin"] }) {
  if (origin === "ai") return <Badge variant="accent">AI</Badge>;
  if (origin === "rule") return <Badge variant="info">Rule</Badge>;
  return <Badge>Manual</Badge>;
}

function StatusBadge({ status }: { status: KpiStatus }) {
  return <Badge variant={STATUS_VARIANTS[status]}>{KPI_STATUS_LABELS[status]}</Badge>;
}

function FormulaBadge({ kpi }: { kpi: Kpi }) {
  if (!kpi.formula_sql) return <Badge>Words only</Badge>;
  return (
    <Badge variant="success">
      SQL · {kpi.link_count} {kpi.link_count === 1 ? "link" : "links"}
    </Badge>
  );
}

function FeasibilityBadge({ feasibility }: { feasibility: KpiFeasibility }) {
  const { label, variant } = FEASIBILITY[feasibility];
  return <Badge variant={variant}>{label}</Badge>;
}

const Dash = () => <span className="text-subtle-foreground">—</span>;

/** An ⓘ button whose tooltip explains a column (hover or keyboard focus; Escape closes). */
function InfoTip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          aria-label={`What is ${label.toLowerCase()}?`}
          className="ms-1 inline-grid size-4 cursor-help place-items-center rounded-full align-middle text-subtle-foreground hover:text-primary focus-visible:text-primary"
          onClick={(event) => event.stopPropagation()}
        >
          <Info aria-hidden className="size-3.5" />
        </button>
      </TooltipTrigger>
      <TooltipContent className="flex flex-col gap-1.5 border border-border bg-surface-raised p-3 leading-normal text-foreground shadow-overlay">
        {children}
      </TooltipContent>
    </Tooltip>
  );
}

function TipItems({ items }: { items: [ReactNode, string][] }) {
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

const TIPS = {
  "Documented under": (
    <>
      <p className="m-0">
        <b>Documented under</b>: where the KPI lives.
      </p>
      <TipItems
        items={[
          [<Badge key="s">Core Banking</Badge>, "A Source System: the KPI uses that system's data only."],
          [
            <Badge key="d" variant="accent">
              Data Warehouse
            </Badge>,
            "A cross-system KPI, or one from the DW model.",
          ],
        ]}
      />
    </>
  ),
  Origin: (
    <>
      <p className="m-0">
        <b>Origin</b>: where the KPI came from.
      </p>
      <TipItems
        items={[
          [<Badge key="m">Manual</Badge>, "Added by a person."],
          [
            <Badge key="a" variant="accent">
              AI
            </Badge>,
            "Suggested by the AI from the Source Schema, profiles and documents.",
          ],
          [
            <Badge key="r" variant="info">
              Rule
            </Badge>,
            "Generated by DAWAM from the DW model's facts and measures (Data Warehouse page).",
          ],
        ]}
      />
    </>
  ),
  Status: (
    <>
      <p className="m-0">
        <b>Status</b>: how far the KPI is through sign-off.
      </p>
      <TipItems
        items={[
          [<StatusBadge key="d" status="draft" />, "Being worked on, not signed off. Every KPI starts here."],
          [<StatusBadge key="i" status="in_review" />, "Someone asked for a review. It does not lock anything."],
          [<StatusBadge key="a" status="approved" />, "Signed off: the agreed definition."],
        ]}
      />
      <p className="m-0">
        An approved KPI goes back to draft when its formula SQL or DW links change. Text edits
        keep it approved.
      </p>
    </>
  ),
  Formula: (
    <>
      <p className="m-0">
        <b>Formula</b>: is the calculation written in SQL yet?
      </p>
      <TipItems
        items={[
          [
            <Badge key="s" variant="success">
              SQL · N links
            </Badge>,
            "Formula SQL is written against the DW Schema and uses N DW columns.",
          ],
          [
            <Badge key="w">Words only</Badge>,
            "Only the formula in words. SQL can be added once the Data Warehouse model exists.",
          ],
        ]}
      />
    </>
  ),
  Feasibility: (
    <>
      <p className="m-0">
        <b>Feasibility</b>: can this KPI be calculated from the Data Warehouse now? DAWAM checks
        the DW columns it uses.
      </p>
      <TipItems
        items={[
          [<FeasibilityBadge key="c" feasibility="computable" />, "Every column it needs exists in the DW model and is mapped."],
          [<FeasibilityBadge key="m" feasibility="needs_mapping" />, "The columns exist, but some are not mapped from the source yet."],
          [<FeasibilityBadge key="d" feasibility="needs_data" />, "A column it needs is not in the DW model."],
          [<Badge key="n">—</Badge>, "Not checked yet: the KPI has no DW links (words only)."],
        ]}
      />
    </>
  ),
};

/** A column header, with its ⓘ tooltip when it has one. */
function Head({ children }: { children: string }) {
  const tip = TIPS[children as keyof typeof TIPS];
  return (
    <TableHead>
      {children}
      {tip && <InfoTip label={children}>{tip}</InfoTip>}
    </TableHead>
  );
}

function Decision({ kpi, names }: { kpi: Kpi; names: ReadonlyMap<string, string> }) {
  if (!kpi.decision) return <Dash />;
  return (
    <DecidedBy
      verb={DECISION_VERBS[kpi.decision]}
      by={kpi.decided_by}
      at={kpi.decided_at}
      names={names}
    />
  );
}

interface KpiActionHandlers {
  onEdit: (kpi: Kpi) => void;
  onDelete: (kpi: Kpi) => void;
  onDecide: (kpi: KpiRow, action: "accept" | "reject") => void;
  pending: boolean;
}

/** What owners and editors can do to one KPI: Reject / Accept a suggestion, Accept a
 * rejected one after all, else Edit / Delete. `compact` for a table row. */
function KpiActions({
  kpi,
  compact = false,
  handlers,
}: {
  kpi: KpiRow;
  compact?: boolean;
  handlers: KpiActionHandlers;
}) {
  const size = compact ? "sm" : "default";
  if (kpi.review_state === "suggested") {
    return (
      <>
        <Button
          type="button"
          size={size}
          disabled={handlers.pending}
          onClick={() => handlers.onDecide(kpi, "reject")}
        >
          Reject
        </Button>
        <Button
          type="button"
          size={size}
          disabled={handlers.pending}
          onClick={() => handlers.onDecide(kpi, "accept")}
        >
          Accept
        </Button>
      </>
    );
  }
  if (kpi.review_state === "rejected") {
    return (
      <Button
        type="button"
        size={size}
        disabled={handlers.pending}
        onClick={() => handlers.onDecide(kpi, "accept")}
      >
        Accept after all
      </Button>
    );
  }
  return (
    <>
      <Button
        type="button"
        size={size}
        variant={compact ? "ghost" : "secondary"}
        onClick={() => handlers.onEdit(kpi)}
      >
        Edit
      </Button>
      <Button
        type="button"
        size={size}
        variant={compact ? "ghost" : "secondary"}
        className="text-danger-strong"
        onClick={() => handlers.onDelete(kpi)}
      >
        Delete
      </Button>
    </>
  );
}

function StatCard({ label, value, sub }: { label: string; value: number; sub: string }) {
  return (
    <div className="rounded-pill border border-border bg-surface-raised px-3.5 py-3">
      <div className="text-xs font-medium text-subtle-foreground">{label}</div>
      <div className="text-2xl font-semibold tabular-nums">{value}</div>
      <div className="text-xs text-muted-foreground">{sub}</div>
    </div>
  );
}

/**
 * The KPIs folder of a Source System (`systemId`) or of the Data Warehouse (`systemId`
 * null), a review like the PII folder: the KPIs tab (added by hand or accepted) for every
 * member, and for owners and editors the Suggested, Accepted and Rejected tabs. Clicking a
 * row opens its details in a side panel. Owners and editors add, edit and delete KPIs and
 * accept or reject the AI's suggestions; deleting an accepted suggestion moves it to Rejected.
 * With `scope.workspaceWide` (the Data Warehouse page) it lists every KPI in the Workspace
 * with a "Documented under" column and filter, and the rule-based suggestions.
 */
export function KpiCatalog({
  workspace,
  scope,
  title,
  description,
  hideTitle = false,
  systemName = "Data Warehouse",
}: {
  workspace: Workspace;
  scope: KpiScope;
  title: string;
  /** One line under the title, e.g. what the KPIs are based on. */
  description?: string;
  /** Keep the title for screen readers only: the breadcrumb already names the page. */
  hideTitle?: boolean;
  /** The Source System the KPIs are documented under; "Data Warehouse" when omitted. */
  systemName?: string;
}) {
  const canEdit = allows(workspace, "kpi.edit");
  // Owners and editors see every tab, archived too; they change KPIs only while active.
  const canSee = allows(workspace, "kpi.view_review");
  const workspaceWide = scope.workspaceWide === true;
  // One request: the KPIs and, on the Data Warehouse page, the rule suggestions.
  const kpis = useKpiList(workspace.id, scope, canSee ? "all" : "catalog");
  const decide = useDecideKpi(workspace.id);
  const decideRule = useDecideRuleSuggestion(workspace.id);
  const sourceSystems = useSourceSystems(workspace.id);
  const systems = sourceSystems.data ?? [];
  const members = useMembers(workspace.id);
  const names = useMemo(
    () => new Map((members.data ?? []).map((m) => [m.user_id, m.display_name])),
    [members.data],
  );
  // A chat's suggested KPIs link here with `tab=suggested`.
  const linkedTab = useSearchParams().get("tab");
  const [tab, setTab] = useState<KpiTab>(linkedTab === "suggested" ? "suggested" : "kpis");
  const shownTab: KpiTab = canSee ? tab : "kpis";
  const [origin, setOrigin] = useState<OriginFilter>("all");
  const [systemFilter, setSystemFilter] = useState<SystemFilter>("all");
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  // A rule suggestion is not a stored KPI: it is a row of Suggested (open) or Rejected.
  const all: KpiRow[] = useMemo(
    () => [
      ...(kpis.data?.kpis ?? []),
      ...(kpis.data?.ruleSuggestions ?? []).map((s) => ruleSuggestionRow(workspace.id, s)),
      ...(kpis.data?.rejectedRuleSuggestions ?? []).map((s) =>
        ruleSuggestionRow(workspace.id, s, s),
      ),
    ],
    [kpis.data, workspace.id],
  );
  // The owners already in use, offered as suggestions in the form.
  const owners = useMemo(
    () => [...new Set(all.map((k) => k.owner.trim()).filter(Boolean))].sort(),
    [all],
  );
  const count = (t: KpiTab) => all.filter((k) => inTab(k, t)).length;
  const catalog = all.filter((k) => inTab(k, "kpis"));
  const inShownTab = all.filter((k) => inTab(k, shownTab));
  const rows = inShownTab.filter(
    (k) => matchesOrigin(k, origin) && matchesSystem(k, systemFilter) && matchesSearch(k, query),
  );
  const current = TABS.find((t) => t.tab === shownTab)!;
  const opened = all.find((k) => k.id === openId) ?? null;
  // Looked up in the list, so a reload after a version conflict shows the latest version.
  const editing = all.find((k) => k.id === editingId) ?? null;
  const deleting = all.find((k) => k.id === deletingId) ?? null;

  const decided = (kpi: Kpi, action: "accept" | "reject") => {
    setOpenId(null);
    toast.success(
      action === "accept" ? `${kpi.name} accepted. It is now in KPIs.` : `${kpi.name} rejected.`,
    );
  };

  const handlers: KpiActionHandlers = {
    pending: decide.isPending || decideRule.isPending,
    onEdit: (kpi) => {
      setOpenId(null);
      setEditingId(kpi.id);
    },
    onDelete: (kpi) => {
      setOpenId(null);
      setDeletingId(kpi.id);
    },
    onDecide: (kpi, action) =>
      kpi.ruleKey !== undefined
        ? decideRule.mutate({ key: kpi.ruleKey, action }, { onSuccess: () => decided(kpi, action) })
        : decide.mutate({ kpiId: kpi.id, action }, { onSuccess: () => decided(kpi, action) }),
  };

  const suggestedTab = shownTab === "suggested";
  const showStatus = shownTab === "kpis" || shownTab === "accepted";
  const heads = [
    "Name",
    ...(workspaceWide ? ["Documented under"] : []),
    "Origin",
    ...(showStatus ? ["Status"] : []),
    suggestedTab ? "Feasibility" : "Formula",
    "Unit",
    "Owner",
    "Refresh",
    suggestedTab ? "Why suggested" : "Decision",
  ];

  const openOnEnter = (kpi: KpiRow) => (event: KeyboardEvent) => {
    if (event.key === "Enter" && event.target === event.currentTarget) {
      setOpenId(kpi.id);
    }
  };

  return (
    <section aria-labelledby="folder-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1">
          <h3 id="folder-title" className={hideTitle ? "sr-only" : h3Classes}>
            {title}
          </h3>
          {description && <p className="m-0 text-sm text-muted-foreground">{description}</p>}
        </div>
        {canEdit && canSee && scope.systemId && (
          <KpiSuggestJob
            workspaceId={workspace.id}
            systemId={scope.systemId}
            kpis={all}
            disabled={workspace.status !== "active"}
            onSuggested={() => {
              setTab("suggested");
              setOrigin("all");
            }}
          />
        )}
        {canEdit && (
          <Button variant="primary" onClick={() => setAdding(true)}>
            <Plus aria-hidden />
            {workspaceWide ? "Add cross-system KPI" : "Add KPI"}
          </Button>
        )}
      </div>
      {kpis.isSuccess && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatCard
            label="KPIs"
            value={catalog.length}
            sub={`${catalog.filter((k) => k.origin === "user").length} added by hand`}
          />
          <StatCard
            label="With formula SQL"
            value={catalog.filter((k) => k.formula_sql).length}
            sub={`of ${catalog.length} KPIs`}
          />
          {canSee && (
            <StatCard
              label="To review"
              value={count("suggested")}
              sub={workspaceWide ? "AI and rule suggestions" : "AI suggestions"}
            />
          )}
          {canSee && (
            <StatCard label="Rejected" value={count("rejected")} sub="incl. removed suggestions" />
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3 border-b border-border">
        <Tabs value={shownTab} onValueChange={(value) => setTab(value as KpiTab)}>
          <TabsList className="border-b-0 px-0">
            {TABS.filter((t) => canSee || t.tab === "kpis").map((t) => (
              <TabsTrigger key={t.tab} value={t.tab} className="flex items-center gap-1.5 text-sm">
                {t.label}
                {t.tab === "suggested" && count("suggested") > 0 && (
                  <span aria-hidden className="size-1.5 rounded-full bg-warning" />
                )}
                {kpis.isSuccess && (
                  <span className="rounded-md bg-muted px-1.5 text-xs font-normal text-subtle-foreground tabular-nums">
                    {count(t.tab)}
                  </span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <div className="ms-auto flex flex-wrap items-center gap-1 pb-1.5">
          {workspaceWide && (
            <SystemFilterSelect value={systemFilter} systems={systems} onChange={setSystemFilter} />
          )}
          <Input
            type="search"
            aria-label="Search KPIs"
            placeholder="Search KPIs"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-7 w-52 max-w-full text-sm"
          />
          <div role="group" aria-label="Filter by origin" className="flex gap-1">
            {ORIGIN_FILTERS.filter((f) => workspaceWide || f.origin !== "rule").map((f) => (
              <Button
                key={f.origin}
                type="button"
                size="sm"
                variant="secondary"
                className={
                  origin === f.origin
                    ? "border-transparent bg-primary-subtle text-primary"
                    : "border-border text-muted-foreground"
                }
                aria-pressed={origin === f.origin}
                onClick={() => setOrigin(f.origin)}
              >
                {f.label}
              </Button>
            ))}
          </div>
        </div>
      </div>
      {!canEdit && (
        <Hint>
          {canSee
            ? "Read only. This Workspace is archived."
            : "Only owners and editors can document KPIs."}
        </Hint>
      )}
      {decide.isError && <ErrorText>{decide.error.message}</ErrorText>}
      {decideRule.isError && <ErrorText>{decideRule.error.message}</ErrorText>}
      {kpis.isPending && <Loading label="Loading KPIs…" className="p-0" />}
      {kpis.isError && <ErrorText>Could not load the KPIs: {kpis.error.message}</ErrorText>}
      {kpis.isSuccess && rows.length === 0 && (
        <EmptyState title={inShownTab.length ? "No matches" : current.empty[0]}>
          {inShownTab.length
            ? "No KPI here matches the search or filter."
            : workspaceWide && shownTab === "suggested"
              ? "New AI and rule suggestions show up here."
              : current.empty[1]}
        </EmptyState>
      )}
      {kpis.isSuccess && rows.length > 0 && (
        <TableCard>
          <Table aria-label={`${current.label} list`}>
            <TableHeader>
              <TableRow>
                {heads.map((h) => (
                  <Head key={h}>{h}</Head>
                ))}
                {canEdit && (
                  <TableHead>
                    <span className="sr-only">Actions</span>
                  </TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((kpi) => (
                <TableRow
                  key={kpi.id}
                  tabIndex={0}
                  className={cn(
                    "cursor-pointer",
                    openId === kpi.id && "[&>td]:bg-primary-subtle",
                    shownTab === "rejected" && "text-muted-foreground",
                  )}
                  onClick={() => setOpenId(kpi.id)}
                  onKeyDown={openOnEnter(kpi)}
                >
                  <TableCell className="align-top whitespace-normal">
                    <button
                      type="button"
                      className={cn(linkButtonClasses, "font-medium")}
                      onClick={(event) => {
                        event.stopPropagation();
                        setOpenId(kpi.id);
                      }}
                    >
                      {kpi.name}
                    </button>
                    {kpi.definition && (
                      <p className="m-0 mt-0.5 line-clamp-2 max-w-80 text-xs text-muted-foreground">
                        {kpi.definition}
                      </p>
                    )}
                  </TableCell>
                  {workspaceWide && (
                    <TableCell className="align-top">
                      <UnderBadge kpi={kpi} systems={systems} />
                    </TableCell>
                  )}
                  <TableCell className="align-top">
                    <OriginBadge origin={kpi.origin} />
                  </TableCell>
                  {showStatus && (
                    <TableCell className="align-top">
                      <StatusBadge status={kpi.status} />
                    </TableCell>
                  )}
                  <TableCell className="align-top">
                    {suggestedTab ? (
                      kpi.feasibility ? (
                        <FeasibilityBadge feasibility={kpi.feasibility} />
                      ) : (
                        <Dash />
                      )
                    ) : (
                      <FormulaBadge kpi={kpi} />
                    )}
                  </TableCell>
                  <TableCell className="align-top">{kpi.unit || <Dash />}</TableCell>
                  <TableCell className="align-top">{kpi.owner || <Dash />}</TableCell>
                  <TableCell className="align-top">{kpi.refresh_frequency || <Dash />}</TableCell>
                  <TableCell className="align-top whitespace-normal">
                    {suggestedTab ? (
                      <p className="m-0 max-w-64 text-xs text-muted-foreground">
                        {kpi.rationale ?? "—"}
                      </p>
                    ) : (
                      <Decision kpi={kpi} names={names} />
                    )}
                  </TableCell>
                  {canEdit && (
                    <TableCell className="align-top" onClick={(event) => event.stopPropagation()}>
                      <div className="flex justify-end gap-1.5">
                        <KpiActions kpi={kpi} compact handlers={handlers} />
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
      <KpiDetails
        workspaceId={workspace.id}
        kpi={opened}
        names={names}
        systems={systems}
        actions={opened && canEdit ? <KpiActions kpi={opened} handlers={handlers} /> : null}
        onClose={() => setOpenId(null)}
      />
      {canEdit && (
        <AddKpiDialog
          workspaceId={workspace.id}
          scope={scope}
          documentedUnder={workspaceWide ? "Data Warehouse" : systemName}
          owners={owners}
          crossSystem={workspaceWide}
          open={adding}
          onOpenChange={setAdding}
        />
      )}
      {canEdit && editing && (
        <EditKpiDialog
          workspaceId={workspace.id}
          kpi={editing}
          documentedUnder={documentedUnderOf(editing, systems)}
          owners={owners}
          reload={() => void kpis.refetch()}
          open
          onOpenChange={(open) => !open && setEditingId(null)}
        />
      )}
      {canEdit && deleting && (
        <DeleteKpiDialog
          workspaceId={workspace.id}
          kpi={deleting}
          onClose={() => setDeletingId(null)}
        />
      )}
    </section>
  );
}

/** A section of the details panel: a small label over its content. */
function DetailSection({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="m-0 text-xs font-medium text-subtle-foreground">{label}</p>
      <div className="text-sm">{children}</div>
    </div>
  );
}

/** One KPI's details in a side panel (any member), with the row's actions in its footer. */
function KpiDetails({
  workspaceId,
  kpi,
  names,
  systems,
  actions,
  onClose,
}: {
  workspaceId: string;
  kpi: KpiRow | null;
  names: ReadonlyMap<string, string>;
  systems: readonly SourceSystem[];
  actions: ReactNode;
  onClose: () => void;
}) {
  // A rule suggestion is not stored yet: it brings its columns with it.
  const links = useKpiLinks(
    workspaceId,
    kpi?.id ?? "",
    kpi != null && kpi.link_count > 0 && !isRuleSuggestion(kpi),
  );
  const inCatalog = kpi != null && (kpi.review_state === "live" || kpi.review_state === "accepted");

  return (
    <Sheet open={kpi != null} onOpenChange={(open) => !open && onClose()}>
      {kpi && (
        <SheetContent side="right" className="bg-surface-raised">
          <header className="flex items-start gap-3 border-b border-border px-5 py-4">
            <div className="min-w-0 flex-1">
              <SheetTitle className="text-lg font-semibold">{kpi.name}</SheetTitle>
              <SheetDescription className="sr-only">KPI details</SheetDescription>
              <div className="mt-1 flex flex-wrap gap-1.5">
                <OriginBadge origin={kpi.origin} />
                {kpi.review_state === "suggested" && <Badge variant="warning">Suggested</Badge>}
                {kpi.review_state === "rejected" && <Badge>Rejected</Badge>}
                {kpi.feasibility && <FeasibilityBadge feasibility={kpi.feasibility} />}
              </div>
            </div>
            <SheetClose asChild>
              <Button type="button" variant="ghost" size="icon" aria-label="Close">
                <X aria-hidden />
              </Button>
            </SheetClose>
          </header>
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto px-5 py-4">
            {kpi.origin === "ai" && kpi.rationale && (
              <div aria-label="AI rationale" className="rounded-lg bg-ai-subtle px-3 py-2.5 text-sm">
                <span className="mb-0.5 block text-xs font-medium text-primary">
                  Why the AI suggested it
                </span>
                {kpi.rationale}
              </div>
            )}
            {kpi.based_on.length > 0 && (
              <DetailSection label="Based on">
                <ul className="m-0 flex list-none flex-col gap-1 p-0">
                  {kpi.based_on.map((table) => (
                    <li key={table} className={cn(monoClasses, "text-xs")}>
                      {table}
                    </li>
                  ))}
                </ul>
              </DetailSection>
            )}
            <DetailSection label="Definition">{kpi.definition || <Dash />}</DetailSection>
            <DetailSection label="Formula in words">{kpi.formula_text || <Dash />}</DetailSection>
            <DetailSection label="Formula SQL">
              {kpi.formula_sql ? (
                <code
                  className={cn(
                    monoClasses,
                    "block rounded-md border border-border bg-surface p-2 text-xs break-words whitespace-pre-wrap",
                  )}
                >
                  {kpi.formula_sql}
                </code>
              ) : (
                <span className="text-muted-foreground">
                  Not yet. The AI proposes it once the Data Warehouse exists.
                </span>
              )}
            </DetailSection>
            {kpi.link_count > 0 && (
              <DetailSection label="Linked DW columns">
                {kpi.ruleLinks ? (
                  <ul className="m-0 flex list-none flex-col gap-1 p-0">
                    {kpi.ruleLinks.map((label) => (
                      <li key={label} className={cn(monoClasses, "text-xs")}>
                        {label}
                      </li>
                    ))}
                  </ul>
                ) : links.isSuccess ? (
                  <ul className="m-0 flex list-none flex-col gap-1 p-0">
                    {links.data.map((link) => (
                      <li key={link.dw_column_id} className={cn(monoClasses, "text-xs")}>
                        {link.layer}.{link.table_name}.{link.column_name}
                      </li>
                    ))}
                  </ul>
                ) : links.isError ? (
                  <ErrorText>Could not load the links: {links.error.message}</ErrorText>
                ) : (
                  <span className="text-muted-foreground">Loading…</span>
                )}
              </DetailSection>
            )}
            <DetailList className="m-0 gap-y-2.5 text-sm [&_dd]:m-0 [&_dt]:font-normal [&_dt]:text-subtle-foreground">
              <dt>Documented under</dt>
              <dd>
                <UnderBadge kpi={kpi} systems={systems} />
              </dd>
              <dt>Unit</dt>
              <dd>{kpi.unit || <Dash />}</dd>
              <dt>Aggregation</dt>
              <dd>{kpi.aggregation || <Dash />}</dd>
              <dt>Owner</dt>
              <dd>{kpi.owner || <Dash />}</dd>
              <dt>Refresh</dt>
              <dd>{kpi.refresh_frequency || <Dash />}</dd>
              {inCatalog && (
                <>
                  <dt>Status</dt>
                  <dd>
                    <StatusBadge status={kpi.status} />
                  </dd>
                </>
              )}
              <dt>Targets</dt>
              <dd>
                {kpi.targets.length ? (
                  kpi.targets.map((t, i) => (
                    <div key={i} className="tabular-nums">
                      {`${t.label}: ${t.value}`}
                    </div>
                  ))
                ) : (
                  <Dash />
                )}
              </dd>
              {kpi.feasibility && (
                <>
                  <dt>
                    Feasibility
                    <InfoTip label="Feasibility">{TIPS.Feasibility}</InfoTip>
                  </dt>
                  <dd>
                    <FeasibilityBadge feasibility={kpi.feasibility} />
                  </dd>
                </>
              )}
              <dt>Decision</dt>
              <dd>
                <Decision kpi={kpi} names={names} />
              </dd>
            </DetailList>
          </div>
          {actions && (
            <footer className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-3">
              {actions}
            </footer>
          )}
        </SheetContent>
      )}
    </Sheet>
  );
}

function AddKpiDialog({
  workspaceId,
  scope,
  documentedUnder,
  owners,
  crossSystem,
  open,
  onOpenChange,
}: {
  workspaceId: string;
  scope: KpiScope;
  documentedUnder: string;
  owners: readonly string[];
  /** The Data Warehouse page adds cross-system KPIs, documented under the Data Warehouse. */
  crossSystem: boolean;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const create = useCreateKpi(workspaceId);
  const [problem, setProblem] = useState<string | null>(null);

  function onSubmit(form: FormData) {
    const fields = readKpiFields(form);
    if (typeof fields === "string") {
      setProblem(fields);
      return;
    }
    setProblem(null);
    create.mutate(
      { ...fields, source_system_id: crossSystem ? null : scope.systemId },
      {
        onSuccess: (kpi) => {
          toast.success(`${kpi.name} added.`);
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          create.reset();
          setProblem(null);
        }
        onOpenChange(next);
      }}
      wide
      title={crossSystem ? "Add cross-system KPI" : "Add KPI"}
      description={
        crossSystem
          ? "Document a KPI that needs more than one Source System: what it means and how it is calculated."
          : "Document a KPI: what it means and how it is calculated."
      }
      submitLabel={crossSystem ? "Add cross-system KPI" : "Add KPI"}
      onSubmit={onSubmit}
      pending={create.isPending}
      error={problem ? new Error(problem) : create.error}
    >
      <KpiFields documentedUnder={documentedUnder} owners={owners} />
    </EntityFormDialog>
  );
}

/** Asks before deleting, saying what happens: a KPI that came from a suggestion moves to
 * Rejected ("Removed by"), one added by hand is removed for good. */
function DeleteKpiDialog({
  workspaceId,
  kpi,
  onClose,
}: {
  workspaceId: string;
  kpi: Kpi;
  onClose: () => void;
}) {
  const remove = useDeleteKpi(workspaceId, kpi.id);
  const fromSuggestion = kpi.review_state !== "live";

  return (
    <ConfirmDialog
      open
      onOpenChange={(next) => !next && onClose()}
      title={`Delete ${kpi.name}?`}
      description={
        fromSuggestion
          ? "This KPI came from a suggestion. It moves to Rejected as “Removed by you”, and the same suggestion won't come back. You can accept it again from there."
          : kpi.origin === "user"
            ? "This KPI was added by hand. It is removed for good."
            : "This KPI is removed for good."
      }
      confirmLabel="Delete KPI"
      pending={remove.isPending}
      error={remove.error}
      onConfirm={() =>
        remove.mutate(undefined, {
          onSuccess: () => {
            toast.success(
              fromSuggestion ? `${kpi.name} moved to Rejected.` : `${kpi.name} deleted.`,
            );
            onClose();
          },
        })
      }
    />
  );
}

/** One KPI's fields and status, saved against the version that was opened. */
function EditKpiDialog({
  workspaceId,
  kpi,
  documentedUnder,
  owners,
  reload,
  open,
  onOpenChange,
}: {
  workspaceId: string;
  kpi: Kpi;
  documentedUnder: string;
  owners: readonly string[];
  reload: () => void;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const update = useUpdateKpi(workspaceId, kpi.id);
  const [problem, setProblem] = useState<string | null>(null);
  const conflict =
    update.error instanceof ApiError && update.error.code === "version_conflict";

  function onSubmit(form: FormData) {
    const fields = readKpiFields(form);
    if (typeof fields === "string") {
      setProblem(fields);
      return;
    }
    setProblem(null);
    const status = String(form.get("status")) as KpiStatus;
    update.mutate(
      { version: kpi.version, ...fields, status },
      {
        onSuccess: () => {
          toast.success("Saved.");
          onOpenChange(false);
        },
        // The newer version loads, so saving again works.
        onError: (error) => {
          if (error instanceof ApiError && error.code === "version_conflict") {
            reload();
          }
        },
      },
    );
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          update.reset();
          setProblem(null);
        }
        onOpenChange(next);
      }}
      wide
      title="Edit KPI"
      description={kpi.name}
      submitLabel="Save"
      onSubmit={onSubmit}
      pending={update.isPending}
      error={
        problem
          ? new Error(problem)
          : conflict
            ? new Error(CONFLICT_MESSAGE)
            : update.error
      }
    >
      <KpiFields initial={kpi} documentedUnder={documentedUnder} owners={owners}>
        <label className={labelClasses}>
          Status
          <NativeSelect name="status" defaultValue={kpi.status}>
            {STATUSES.map((status) => (
              <option key={status} value={status}>
                {KPI_STATUS_LABELS[status]}
              </option>
            ))}
          </NativeSelect>
        </label>
      </KpiFields>
    </EntityFormDialog>
  );
}
