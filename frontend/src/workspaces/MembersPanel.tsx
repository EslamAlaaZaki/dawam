"use client";

import { UserPlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type ComponentProps } from "react";

import {
  useAddMember,
  useChangeMemberRole,
  useLeaveWorkspace,
  useMembers,
  useRemoveMember,
  useTransferOwnership,
  type Member,
} from "../api/members";
import { useMe } from "../api/queries";
import {
  ROLE_LABELS,
  allows,
  type Workspace,
  type WorkspaceRole,
} from "../api/workspaces";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import { EntityTable, type EntityColumn } from "../components/entity/EntityTable";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Actions,
  NativeSelect,
  OkText,
  h3Classes,
  labelClasses,
  linkClasses,
} from "./parts";

const ROLES: WorkspaceRole[] = ["owner", "editor", "viewer"];

/**
 * Who belongs to the Workspace (stories 30-34). Every member sees the list and can
 * leave; owners also add people by email (or invite them), change roles, remove
 * members and transfer ownership. The server refuses leaving it without an owner.
 */
export function MembersPanel({ workspace }: { workspace: Workspace }) {
  const members = useMembers(workspace.id);
  const me = useMe();
  const manages = allows(workspace, "workspace.manage_members");
  const transfers = allows(workspace, "workspace.transfer_ownership");
  const [adding, setAdding] = useState(false);
  // An invitation whose email could not be sent: the note stays until the page changes.
  const [unsentTo, setUnsentTo] = useState<string | null>(null);

  const columns: EntityColumn<Member>[] = [
    {
      header: "Name",
      className: "font-medium",
      cell: (member) => (
        <>
          {member.display_name}
          {member.user_id === me.data?.id ? " (you)" : ""}
          {!member.is_active && " — deactivated"}
        </>
      ),
    },
    {
      header: "Email",
      className: "text-muted-foreground",
      cell: (member) => member.email,
    },
    { header: "Role", cell: (member) => (
        <Badge variant={member.role === "owner" ? "accent" : "neutral"}>
          {ROLE_LABELS[member.role]}
        </Badge>
      ),
    },
  ];

  return (
    <section aria-labelledby="members-title" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id="members-title" className={h3Classes}>
          Members
        </h3>
        {manages && (
          <Button className="ml-auto" onClick={() => setAdding(true)}>
            <UserPlus aria-hidden />
            Add member
          </Button>
        )}
      </div>
      {unsentTo && (
        <OkText>
          {unsentTo} is invited, but the email could not be sent: an admin can
          copy the link from{" "}
          <Link href="/admin/email/links" className={linkClasses}>
            Links to share
          </Link>
          .
        </OkText>
      )}
      <EntityTable
        rows={members.data}
        rowKey={(member) => member.user_id}
        rowLabel={(member) => member.display_name}
        columns={columns}
        actions={
          manages
            ? (member) => (
                <MemberActions
                  workspaceId={workspace.id}
                  member={member}
                  isMe={member.user_id === me.data?.id}
                  transfers={transfers}
                />
              )
            : undefined
        }
        isPending={members.isPending}
        error={members.error}
        loadErrorPrefix="Could not load the members:"
        emptyMessage="No members."
      />
      {allows(workspace, "workspace.leave") && (
        <LeaveWorkspace workspaceId={workspace.id} />
      )}
      {manages && (
        <AddMemberDialog
          workspaceId={workspace.id}
          open={adding}
          onOpenChange={setAdding}
          onUnsent={setUnsentTo}
        />
      )}
    </section>
  );
}

function AddMemberDialog({
  workspaceId,
  open,
  onOpenChange,
  onUnsent,
}: {
  workspaceId: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUnsent: (email: string | null) => void;
}) {
  const add = useAddMember(workspaceId);

  function onSubmit(form: FormData) {
    add.mutate(
      {
        email: String(form.get("email") ?? "").trim(),
        role: String(form.get("role")) as WorkspaceRole,
      },
      {
        onSuccess: (outcome) => {
          onUnsent(null);
          if (outcome.outcome === "added") {
            toast.success(
              `${outcome.member.display_name} is now ${withArticle(outcome.member.role)}.`,
            );
          } else if (outcome.delivery === "sent") {
            toast.success(`Invitation emailed to ${outcome.invitation.email}.`);
          } else {
            onUnsent(outcome.invitation.email);
          }
          onOpenChange(false);
        },
      },
    );
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) add.reset();
        onOpenChange(next);
      }}
      title="Add member"
      description="Someone without an account gets an invitation to join DAWAM and this Workspace."
      submitLabel="Add member"
      onSubmit={onSubmit}
      pending={add.isPending}
      error={add.error}
    >
      <label className={labelClasses}>
        Email
        <Input name="email" type="email" autoComplete="off" required />
      </label>
      <label className={labelClasses}>
        Role
        <RoleSelect name="role" defaultValue="editor" />
      </label>
    </EntityFormDialog>
  );
}

/** A select of the Workspace roles, by their labels. */
function RoleSelect(props: ComponentProps<"select">) {
  return (
    <NativeSelect {...props}>
      {ROLES.map((role) => (
        <option key={role} value={role}>
          {ROLE_LABELS[role]}
        </option>
      ))}
    </NativeSelect>
  );
}

/** "an owner", "an editor", "a viewer". */
function withArticle(role: WorkspaceRole): string {
  return `${role === "owner" || role === "editor" ? "an" : "a"} ${ROLE_LABELS[role].toLowerCase()}`;
}

/** What an owner can do to one member; removing and transferring ask first. */
function MemberActions({
  workspaceId,
  member,
  isMe,
  transfers,
}: {
  workspaceId: string;
  member: Member;
  isMe: boolean;
  transfers: boolean;
}) {
  const remove = useRemoveMember(workspaceId);
  const transfer = useTransferOwnership(workspaceId);
  const [changing, setChanging] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [transferring, setTransferring] = useState(false);

  return (
    <>
      <div className="flex flex-wrap gap-2">
        <Button size="sm" onClick={() => setChanging(true)}>
          Change role
        </Button>
        {!isMe && (
          <Button size="sm" onClick={() => setRemoving(true)}>
            Remove
          </Button>
        )}
        {!isMe && transfers && member.role !== "owner" && (
          <Button size="sm" onClick={() => setTransferring(true)}>
            Transfer ownership
          </Button>
        )}
      </div>
      <ChangeRoleDialog
        workspaceId={workspaceId}
        member={member}
        open={changing}
        onOpenChange={setChanging}
      />
      <ConfirmDialog
        open={removing}
        onOpenChange={(next) => {
          if (!next) remove.reset();
          setRemoving(next);
        }}
        title={`Remove ${member.display_name}?`}
        description="They lose access to this Workspace."
        confirmLabel="Remove"
        pending={remove.isPending}
        error={remove.error}
        onConfirm={() =>
          remove.mutate(member.user_id, { onSuccess: () => setRemoving(false) })
        }
      />
      <ConfirmDialog
        open={transferring}
        onOpenChange={(next) => {
          if (!next) transfer.reset();
          setTransferring(next);
        }}
        title={`Make ${member.display_name} the owner?`}
        description="You become an editor."
        confirmLabel="Transfer ownership"
        pending={transfer.isPending}
        error={transfer.error}
        onConfirm={() =>
          transfer.mutate(member.user_id, {
            onSuccess: () => setTransferring(false),
          })
        }
      />
    </>
  );
}

function ChangeRoleDialog({
  workspaceId,
  member,
  open,
  onOpenChange,
}: {
  workspaceId: string;
  member: Member;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const changeRole = useChangeMemberRole(workspaceId);

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) changeRole.reset();
        onOpenChange(next);
      }}
      title={`Change role of ${member.display_name}`}
      description={member.email}
      submitLabel="Save"
      onSubmit={(form) => {
        const role = String(form.get("role")) as WorkspaceRole;
        if (role === member.role) {
          onOpenChange(false);
          return;
        }
        changeRole.mutate(
          { userId: member.user_id, role },
          { onSuccess: () => onOpenChange(false) },
        );
      }}
      pending={changeRole.isPending}
      error={changeRole.error}
    >
      <label className={labelClasses}>
        Role
        <RoleSelect name="role" defaultValue={member.role} />
      </label>
    </EntityFormDialog>
  );
}

function LeaveWorkspace({ workspaceId }: { workspaceId: string }) {
  const leave = useLeaveWorkspace(workspaceId);
  const router = useRouter();
  const [asking, setAsking] = useState(false);
  return (
    <Actions>
      <Button size="sm" disabled={leave.isPending} onClick={() => setAsking(true)}>
        Leave Workspace
      </Button>
      <ConfirmDialog
        open={asking}
        onOpenChange={(next) => {
          if (!next) leave.reset();
          setAsking(next);
        }}
        title="Leave this Workspace?"
        description="You lose access to it until an owner adds you again."
        confirmLabel="Leave Workspace"
        pending={leave.isPending}
        error={leave.error}
        onConfirm={() =>
          leave.mutate(undefined, { onSuccess: () => router.push("/") })
        }
      />
    </Actions>
  );
}
