"""Change propagation (spec §6.10, stories 53, 113).

Every structural change, once applied, is followed here: the impacted Assets across all
Layers and KPIs are named in an ``impact_alert`` to the owners and editors, and what can be
carried through lineage is proposed as one ``propagation`` Change Set, grouped by Layer,
which the Editor accepts in full or in part. What is not accepted stays visible as
validation errors. Applying a propagation Change Set never propagates again, so there is no
loop.

"Fix impacts" (story 113a) recomputes the propagation from what is broken now: the open
validation errors propagation can fix are turned back into the structural changes behind
them (``open_impacts``, with former names from the audit trail) and propagated again as a
new Change Set. Items that expired (a newer propagation superseded theirs, or theirs was
closed) are never revived.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session

from dawam.modules.audit import AuditService
from dawam.modules.auth import User
from dawam.modules.changesets import (
    AppliedChange,
    AppliedChangeSet,
    ChangeSetDetail,
    ChangeSetService,
    ProposedItem,
)
from dawam.modules.kpis import kpi_names, plan_kpi_propagation
from dawam.modules.notifications import NotificationService
from dawam.modules.warehouse import (
    RENAMED_ENTITY_TYPES,
    Impact,
    StructuralChange,
    lineage_nodes,
    open_impacts,
    plan_propagation,
)
from dawam.modules.workspaces import Action, WorkspaceService

LAYERS = ("staging", "core", "mart", "kpi")
"""How items and impacts are grouped, upstream first."""
LAYER_TITLES = {"staging": "Staging", "core": "Core", "mart": "Mart", "kpi": "KPIs"}
NAMED_PER_LAYER = 5
"""How many impacted Assets the alert names per Layer before "and N more"."""
FIX_IMPACTS_SCOPE = {"trigger": "fix_impacts"}
"""A newer "Fix impacts" supersedes a pending one."""


@dataclass(frozen=True)
class PropagationResult:
    change_set: ChangeSetDetail | None
    """``None`` when nothing could be proposed (e.g. a delete, which never cascades)."""
    impacts: list[Impact]
    """Every impacted Asset, grouped by Layer (``kpi`` for KPIs)."""


def structural_changes(changes: Sequence[AppliedChange]) -> list[StructuralChange]:
    """What applied Change Set items changed structurally in the DW Schema: renames, type and
    nullability changes, and tables or columns flagged ``source_removed``. Creates (new
    inputs) never propagate, and a delete is of something nothing reads."""
    found: list[StructuralChange] = []
    for change in changes:
        if change.entity_type == "mapping_branch" and change.new:
            # A mapping draft newly mapped these columns (a Mart ``direct`` one re-points KPIs).
            found.extend(
                StructuralChange("dw_column", uuid.UUID(str(i)), "mapped")
                for i in change.new.get("mapped_column_ids") or ()
            )
            continue
        kind = {
            "staging_table": "dw_table",
            "dw_table": "dw_table",
            "staging_column": "dw_column",
            "dw_column": "dw_column",
        }.get(change.entity_type)
        if kind is None or change.old is None or change.new is None:
            continue
        object_id = uuid.UUID(str(change.entity_id))
        old, new = change.old, change.new
        if "name" in new and old.get("name") != new["name"]:
            found.append(StructuralChange(kind, object_id, "rename", old.get("name"), new["name"]))  # type: ignore[arg-type]
        if "data_type" in new and old.get("data_type") != new["data_type"]:
            found.append(
                StructuralChange(kind, object_id, "retype", old.get("data_type"), new["data_type"])  # type: ignore[arg-type]
            )
        if "is_nullable" in new and old.get("is_nullable") != new["is_nullable"]:
            found.append(
                StructuralChange(
                    kind,  # type: ignore[arg-type]
                    object_id,
                    "nullability",
                    old.get("is_nullable"),
                    new["is_nullable"],
                )
            )
        if new.get("status") == "source_removed" and old.get("status") != "source_removed":
            found.append(StructuralChange(kind, object_id, "removed"))  # type: ignore[arg-type]
    return found


def _describe(change: StructuralChange, labels: Mapping[uuid.UUID, str]) -> str:
    what = labels.get(change.object_id) or ".".join(
        p for p in (change.table_name, change.column_name) if p
    )
    if change.kind == "rename":
        return f"{change.old} renamed to {change.new}"
    if change.kind == "retype":
        return f"type change of {what}"
    if change.kind == "nullability":
        return f"{what} now nullable" if change.new else f"{what} now required"
    if change.kind == "removed":
        return f"{what} removed"
    return f"{what} mapped from Core"


class PropagationService:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        workspaces: WorkspaceService,
        change_sets: ChangeSetService,
        notifications: NotificationService,
    ) -> None:
        self._engine = engine
        self._workspaces = workspaces
        self._change_sets = change_sets
        self._notifications = notifications
        self._audit = AuditService(engine)

    # -- the hooks --------------------------------------------------------------------

    def after_edit(
        self, user: User, workspace_id: uuid.UUID, changes: Sequence[StructuralChange]
    ) -> None:
        """A direct edit was committed. A newer edit of the same object supersedes a pending
        propagation of an older one."""
        first = changes[0]
        self.propagate(
            user,
            workspace_id,
            changes,
            scope={"trigger": "edit", "object_id": str(first.object_id), "kind": first.kind},
        )

    def after_change_set(self, user: User, applied: AppliedChangeSet) -> None:
        """A Change Set was applied. A sync's propagation is scoped to its Source System, so
        the next sync's supersedes it."""
        if applied.origin == "propagation":
            return
        changes = structural_changes(applied.changes)
        if not changes:
            return
        if applied.origin == "sync" and applied.scope.get("source_system_id"):
            scope = {"trigger": "sync", "source_system_id": applied.scope["source_system_id"]}
        else:
            scope = {"trigger": "change_set", "change_set_id": str(applied.change_set_id)}
        self.propagate(user, applied.workspace_id, changes, scope=scope)

    def fix_impacts(self, user: User, workspace_id: uuid.UUID) -> PropagationResult:
        """Recompute the propagation Change Set from the open validation errors propagation
        can fix (owners and editors; refused while the Workspace is archived). An empty
        result (no Change Set, no impacts) when nothing is broken that way."""
        self._workspaces.authorize(user, Action.REVIEW_CHANGE_SETS, workspace_id)
        renames = self._audit.field_changes(
            workspace_id, entity_types=RENAMED_ENTITY_TYPES, field="name"
        )
        former = [(uuid.UUID(e.entity_id), str(e.old["name"])) for e in renames if e.old]
        with Session(self._engine) as db:
            changes = open_impacts(db, workspace_id, former)
        result = None
        if changes:
            result = self.propagate(
                user, workspace_id, changes, scope=FIX_IMPACTS_SCOPE, title="Fix impacts"
            )
        return result or PropagationResult(None, [])

    # -- propagating ------------------------------------------------------------------

    def propagate(
        self,
        user: User,
        workspace_id: uuid.UUID,
        changes: Sequence[StructuralChange],
        *,
        scope: Mapping[str, Any],
        title: str = "Propagate",
    ) -> PropagationResult | None:
        """Plan, propose and announce what ``changes`` (already applied) call for, as a
        Change Set titled ``title: <the changes>``. ``None`` when they reach nothing."""
        with Session(self._engine) as db:
            plan = plan_propagation(db, workspace_id, changes)
            kpis = plan_kpi_propagation(db, workspace_id, plan)
            reached_kpis = [
                *(i.object_id for i in kpis.items if i.object_id is not None),
                *kpis.flagged,
                *plan.reached,
            ]
            kpi_labels = kpi_names(db, workspace_id, list(dict.fromkeys(reached_kpis)))
            labels = {
                n.id: n.label
                for n in lineage_nodes(db, workspace_id, [c.object_id for c in changes]).values()
            }
        impacts = [
            *plan.impacts,
            *(
                Impact("kpi", "kpi", kpi_id, name)
                for kpi_id, name in sorted(kpi_labels.items(), key=lambda kv: kv[1])
            ),
        ]
        rank = {layer: i for i, layer in enumerate(LAYERS)}
        items: list[tuple[str, ProposedItem]] = [*plan.items, *(("kpi", i) for i in kpis.items)]
        items.sort(key=lambda entry: rank[entry[0]])  # stable: dependencies stay first
        if not impacts and not items:
            return None
        summary = "; ".join(dict.fromkeys(_describe(c, labels) for c in changes))
        detail = None
        if items:
            detail = self._change_sets.propose(
                user,
                workspace_id,
                origin="propagation",
                scope=scope,
                title=f"{title}: {summary}",
                items=[
                    ProposedItem(
                        key=item.key,
                        object_type=item.object_type,
                        operation=item.operation,
                        object_id=item.object_id,
                        label=f"[{LAYER_TITLES[layer]}] {item.label}",
                        payload=item.payload,
                        base_values=item.base_values,
                        depends_on=item.depends_on,
                        is_conflict=item.is_conflict,
                    )
                    for layer, item in items
                ],
            )
        self._notifications.notify(
            self._workspaces.reviewer_ids(workspace_id),
            kind="impact_alert",
            message=_alert(summary, impacts, detail is not None),
            workspace_id=workspace_id,
            ref_type="change_set" if detail is not None else None,
            ref_id=detail.change_set.id if detail is not None else None,
        )
        return PropagationResult(detail, impacts)


def _alert(summary: str, impacts: Sequence[Impact], proposed: bool) -> str:
    groups: list[str] = []
    for layer in LAYERS:
        named = [i.label for i in impacts if i.layer == layer]
        if not named:
            continue
        shown = ", ".join(named[:NAMED_PER_LAYER])
        more = f" and {len(named) - NAMED_PER_LAYER} more" if len(named) > NAMED_PER_LAYER else ""
        groups.append(f"{LAYER_TITLES[layer]}: {shown}{more}")
    impacted = "; ".join(groups) if groups else "nothing downstream"
    action = (
        "Review the propagation Change Set."
        if proposed
        else "Nothing can be proposed; fix them by hand (they show as validation errors)."
    )
    return f"{summary}. Impacted: {impacted}. {action}"
