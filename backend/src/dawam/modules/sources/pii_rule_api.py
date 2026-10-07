"""``/workspaces/{workspace_id}/pii-rules``: the Workspace's PII rules (spec story 136).

Handlers only translate HTTP to ``PiiRuleService`` calls; the service authorizes every call
through the workspaces module's policy (owners only).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from dawam.modules.auth import CurrentUser
from dawam.modules.workspaces import WorkspaceService

from .internal.pii_rules import MAX_KEYWORD_LENGTH, MAX_KEYWORDS, MAX_PATTERN_LENGTH
from .pii_rule_service import PiiRuleService
from .snapshot_api import PiiCategory

router = APIRouter(prefix="/workspaces/{workspace_id}/pii-rules", tags=["sources"])


def pii_rule_service(request: Request) -> PiiRuleService:
    state = request.app.state
    clock = state.services.clock
    return PiiRuleService(
        state.engine, workspaces=WorkspaceService(state.engine, clock=clock), clock=clock
    )


PiiRuleServiceDep = Annotated[PiiRuleService, Depends(pii_rule_service)]

Keyword = Annotated[str, Field(max_length=MAX_KEYWORD_LENGTH)]


class CustomPiiRule(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str = Field(description="Unique per Workspace; a finding's rule is `custom:<name>`.")
    description: str
    keywords: list[str] = Field(
        description="Matched, once normalised, inside a normalised column name."
    )
    pattern: str | None = Field(description="A regex a sampled value must match in full.")
    category: PiiCategory
    confidence: float = Field(description="0.5 to 1; what a name match alone is worth.")
    created_by: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class BuiltInPiiRule(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="E.g. `national_id`.")
    category: PiiCategory
    confidence: float
    enabled: bool = Field(description="Off means this Workspace's scans skip the rule.")


class PiiRuleList(BaseModel):
    built_in: list[BuiltInPiiRule]
    custom: list[CustomPiiRule]


class PiiRuleCreate(BaseModel):
    name: str = Field(
        max_length=40,
        description="1 to 40 lower-case letters, digits and underscores, starting with a letter.",
    )
    description: str = ""
    keywords: list[Keyword] = Field(
        default_factory=list,
        max_length=MAX_KEYWORDS,
        description="Name keywords, e.g. `emp_no`; separators and case are ignored.",
    )
    pattern: str | None = Field(
        default=None,
        max_length=MAX_PATTERN_LENGTH,
        description="A regex tested on whole sampled values in value scans. "
        "At least one of keywords and pattern is required.",
    )
    category: PiiCategory
    confidence: float = Field(default=0.8, description="0.5 to 1.")


class PiiRuleUpdate(BaseModel):
    description: str | None = None
    keywords: list[Keyword] | None = Field(default=None, max_length=MAX_KEYWORDS)
    pattern: str | None = Field(
        default=None, max_length=MAX_PATTERN_LENGTH, description="An empty string removes it."
    )
    category: PiiCategory | None = None
    confidence: float | None = None


class BuiltInSwitch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool


@router.get("", operation_id="listPiiRules")
def list_pii_rules(
    workspace_id: uuid.UUID, user: CurrentUser, rules: PiiRuleServiceDep
) -> PiiRuleList:
    """Built-in rules with their on/off state, and the custom rules (owners)."""
    found = rules.list_rules(user, workspace_id)
    return PiiRuleList(
        built_in=[BuiltInPiiRule.model_validate(r) for r in found.built_in],
        custom=[CustomPiiRule.model_validate(r) for r in found.custom],
    )


@router.post("", operation_id="createPiiRule", status_code=201)
def create_pii_rule(
    workspace_id: uuid.UUID, body: PiiRuleCreate, user: CurrentUser, rules: PiiRuleServiceDep
) -> CustomPiiRule:
    """Add a custom PII rule (owners). It applies to later name and value scans. Audited."""
    return CustomPiiRule.model_validate(rules.create(user, workspace_id, **body.model_dump()))


@router.patch("/built-in/{rule_id}", operation_id="switchBuiltInPiiRule")
def switch_built_in_pii_rule(
    workspace_id: uuid.UUID,
    rule_id: str,
    body: BuiltInSwitch,
    user: CurrentUser,
    rules: PiiRuleServiceDep,
) -> BuiltInPiiRule:
    """Switch a built-in rule on or off for this Workspace (owners). Built-in rules are
    never edited. Audited."""
    return BuiltInPiiRule.model_validate(
        rules.set_built_in_enabled(user, workspace_id, rule_id, body.enabled)
    )


@router.patch("/{rule_id}", operation_id="updatePiiRule")
def update_pii_rule(
    workspace_id: uuid.UUID,
    rule_id: uuid.UUID,
    body: PiiRuleUpdate,
    user: CurrentUser,
    rules: PiiRuleServiceDep,
) -> CustomPiiRule:
    """Edit a custom PII rule (owners); a field left out stays. Audited."""
    sent = body.model_dump(exclude_unset=True)
    changes = {k: v for k, v in sent.items() if v is not None or k == "pattern"}
    return CustomPiiRule.model_validate(rules.update(user, workspace_id, rule_id, **changes))


@router.delete("/{rule_id}", operation_id="deletePiiRule", status_code=204)
def delete_pii_rule(
    workspace_id: uuid.UUID, rule_id: uuid.UUID, user: CurrentUser, rules: PiiRuleServiceDep
) -> Response:
    """Delete a custom PII rule (owners). Findings it made stay. Audited."""
    rules.delete(user, workspace_id, rule_id)
    return Response(status_code=204)
