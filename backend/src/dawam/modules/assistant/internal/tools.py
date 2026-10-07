"""The assistant's tool registry (spec §6.16).

Tools call the same service layer as the REST API, as the requesting user. Each tool names
the policy ``Action`` that governs it, and every call is authorized through
``WorkspaceService.authorize`` (which asks ``can``) before it runs: a viewer's chat can only
use tools whose action a viewer may perform, whatever the model asks for. A tool that puts
Workspace data in front of the model also names the data-sharing level it needs.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from dawam.modules.auth import User
from dawam.modules.files import FileService
from dawam.modules.kpis import KpiService
from dawam.modules.llm import DataSharingLevel, DataSharingPolicy, ToolSpec
from dawam.modules.workspaces import Action, WorkspaceService
from dawam.platform.errors import ApiError

from .agent import ToolOutcome

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 20_000
MAX_FILE_TEXT_CHARS = 15_000


@dataclass(frozen=True)
class ToolServices:
    kpis: KpiService
    files: FileService


@dataclass(frozen=True)
class ToolContext:
    user: User
    workspace_id: uuid.UUID
    services: ToolServices


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    arguments: type[BaseModel]
    kind: Literal["read", "write"]
    action: Action
    """The policy action the call is checked against, as the requesting user."""
    run: Callable[[ToolContext, Any], Any]
    level: DataSharingLevel | None = None
    """The data-sharing level the Workspace needs for the tool to be used at all."""

    @property
    def spec(self) -> ToolSpec:
        schema = self.arguments.model_json_schema()
        schema.pop("title", None)
        return ToolSpec(self.name, self.description, schema)


class _GetObject(BaseModel):
    kind: Literal["kpi"] = Field(description="The kind of object. Only `kpi` for now.")
    id: uuid.UUID = Field(description="The object's id.")


class _ListFiles(BaseModel):
    system_id: uuid.UUID | None = Field(
        default=None,
        description="A Source System's id; leave out for the Data Warehouse's file area.",
    )


class _ReadFile(BaseModel):
    file_id: uuid.UUID


class _GenerateFile(BaseModel):
    system_id: uuid.UUID = Field(description="The Source System whose file area gets the file.")
    name: str = Field(min_length=1, max_length=200, description="File name, e.g. `notes.md`.")
    content: str = Field(max_length=200_000, description="The file's text.")


def _get_object(ctx: ToolContext, args: _GetObject) -> Any:
    return ctx.services.kpis.get(ctx.user, ctx.workspace_id, args.id)


def _list_files(ctx: ToolContext, args: _ListFiles) -> Any:
    files = ctx.services.files
    if args.system_id is None:
        return files.list_for_warehouse(ctx.user, ctx.workspace_id).items
    return files.list_for_system(ctx.user, ctx.workspace_id, args.system_id).items


def _read_file(ctx: ToolContext, args: _ReadFile) -> Any:
    text = ctx.services.files.read_text(ctx.user, ctx.workspace_id, args.file_id)
    content = text.content
    if len(content) > MAX_FILE_TEXT_CHARS:
        content = content[:MAX_FILE_TEXT_CHARS] + "\n[truncated]"
    return {"name": text.file.name, "content": content}


def _generate_file(ctx: ToolContext, args: _GenerateFile) -> Any:
    return ctx.services.files.save_generated(
        ctx.user, ctx.workspace_id, args.system_id, name=args.name, data=args.content.encode()
    )


TOOLS: tuple[Tool, ...] = (
    Tool(
        "get_object",
        "Read an object of the Workspace with its details.",
        _GetObject,
        "read",
        Action.VIEW_WORKSPACE,
        _get_object,
    ),
    Tool(
        "list_files",
        "List the files in a Source System's file area or the Data Warehouse's.",
        _ListFiles,
        "read",
        Action.VIEW_WORKSPACE,
        _list_files,
    ),
    Tool(
        "read_file",
        "Read a text file or document from a file area.",
        _ReadFile,
        "read",
        Action.VIEW_WORKSPACE,
        _read_file,
        level=DataSharingLevel.DOCUMENTS,
    ),
    Tool(
        "generate_file",
        "Save a new text file in a Source System's file area (overwrites one of the same name).",
        _GenerateFile,
        "write",
        Action.UPLOAD_FILE,
        _generate_file,
    ),
)


def _json(value: Any) -> str:
    def plain(item: Any) -> Any:
        if dataclasses.is_dataclass(item) and not isinstance(item, type):
            return dataclasses.asdict(item)
        return str(item)

    text = json.dumps(value, default=plain, ensure_ascii=False)
    return text if len(text) <= MAX_RESULT_CHARS else text[:MAX_RESULT_CHARS] + " [truncated]"


class ToolRegistry:
    """The tools of one installation, ready to be bound to a user in a Workspace."""

    def __init__(
        self,
        workspaces: WorkspaceService,
        services: ToolServices,
        tools: Sequence[Tool] = TOOLS,
    ) -> None:
        self._workspaces = workspaces
        self._services = services
        self._tools = {tool.name: tool for tool in tools}

    def bind(self, user: User, workspace_id: uuid.UUID, policy: DataSharingPolicy) -> BoundTools:
        return BoundTools(self, ToolContext(user, workspace_id, self._services), policy)

    def allowed(self, ctx: ToolContext, policy: DataSharingPolicy, tool: Tool) -> ApiError | None:
        """Why ``tool`` may not be used now, or ``None``."""
        if tool.level is not None and not policy.allows(tool.level):
            return ApiError(
                403,
                "data_sharing_level",
                f"The Workspace's data-sharing level does not allow `{tool.name}`: an owner "
                f"must raise it to `{tool.level.value}`.",
            )
        try:
            self._workspaces.authorize(ctx.user, tool.action, ctx.workspace_id)
        except ApiError as exc:
            return exc
        return None

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def all(self) -> list[Tool]:
        return list(self._tools.values())


class BoundTools:
    """The ``ToolSet`` of one run: the registry as seen by one user in one Workspace."""

    def __init__(self, registry: ToolRegistry, ctx: ToolContext, policy: DataSharingPolicy) -> None:
        self._registry = registry
        self._ctx = ctx
        self._policy = policy

    def specs(self) -> list[ToolSpec]:
        """Only the tools this user may use now; execution checks again regardless."""
        return [
            tool.spec
            for tool in self._registry.all()
            if self._registry.allowed(self._ctx, self._policy, tool) is None
        ]

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        tool = self._registry.get(name)
        if tool is None:
            return ToolOutcome(f"There is no tool called `{name}`.", "error")
        refusal = self._registry.allowed(self._ctx, self._policy, tool)
        if refusal is not None:
            return ToolOutcome(f"Refused: {refusal.message}", "refused")
        try:
            args = tool.arguments.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'arguments'}: {e['msg']}"
                for e in exc.errors()
            )
            return ToolOutcome(f"Invalid arguments for `{name}`: {problems}", "error")
        try:
            return ToolOutcome(_json(tool.run(self._ctx, args)), "ok")
        except ApiError as exc:
            status = "refused" if exc.status_code in (401, 403) else "error"
            return ToolOutcome(f"{exc.code}: {exc.message}", status)
        except Exception:
            logger.exception("assistant tool failed", extra={"tool": name})
            return ToolOutcome(f"The `{name}` tool failed.", "error")
