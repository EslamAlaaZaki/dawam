"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type ComponentProps, type FormEvent } from "react";

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
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import { Loading } from "../shell/Loading";
import {
  Actions,
  ErrorText,
  Hint,
  NativeSelect,
  OkText,
  TableCard,
  formCardClasses,
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

  return (
    <section aria-labelledby="members-title" className="flex flex-col gap-3">
      <h3 id="members-title" className={h3Classes}>
        Members
      </h3>
      {manages && <AddMemberForm workspaceId={workspace.id} />}
      {members.isPending ? (
        <Loading />
      ) : members.isError ? (
        <ErrorText>
          Could not load the members: {members.error.message}
        </ErrorText>
      ) : (
        <TableCard>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead scope="col">Name</TableHead>
                <TableHead scope="col">Email</TableHead>
                <TableHead scope="col">Role</TableHead>
                {manages && <TableHead scope="col">Actions</TableHead>}
              </TableRow>
            </TableHeader>
            <TableBody>
              {members.data.map((member) => (
                <MemberRow
                  key={member.user_id}
                  workspaceId={workspace.id}
                  member={member}
                  isMe={member.user_id === me.data?.id}
                  manages={manages}
                  transfers={transfers}
                />
              ))}
            </TableBody>
          </Table>
        </TableCard>
      )}
      {allows(workspace, "workspace.leave") && (
        <LeaveWorkspace workspaceId={workspace.id} />
      )}
    </section>
  );
}

function AddMemberForm({ workspaceId }: { workspaceId: string }) {
  const add = useAddMember(workspaceId);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    add.mutate(
      {
        email: String(data.get("email") ?? "").trim(),
        role: String(data.get("role")) as WorkspaceRole,
      },
      {
        onSuccess: (outcome) => {
          form.reset();
          if (outcome.outcome === "added") {
            toast.success(
              `${outcome.member.display_name} is now ${withArticle(outcome.member.role)}.`,
            );
          } else if (outcome.delivery === "sent") {
            toast.success(`Invitation emailed to ${outcome.invitation.email}.`);
          }
        },
      },
    );
  }

  const result = add.data;
  return (
    <form
      className={formCardClasses}
      onSubmit={onSubmit}
      aria-label="Add member"
    >
      <label className={labelClasses}>
        Email
        <Input name="email" type="email" autoComplete="off" required />
      </label>
      <label className={labelClasses}>
        Role
        <RoleSelect name="role" defaultValue="editor" />
      </label>
      <Hint>
        Someone without an account gets an invitation to join DAWAM and this
        Workspace.
      </Hint>
      <Actions>
        <Button type="submit" variant="primary" disabled={add.isPending}>
          Add member
        </Button>
      </Actions>
      {add.isError && <ErrorText>{add.error.message}</ErrorText>}
      {result?.outcome === "invited" &&
        result.delivery === "link_for_admin" && (
          <OkText>
            {result.invitation.email} is invited, but the email could not be
            sent: an admin can copy the link from{" "}
            <Link href="/admin/email/links" className={linkClasses}>
              Links to share
            </Link>
            .
          </OkText>
        )}
    </form>
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

function MemberRow({
  workspaceId,
  member,
  isMe,
  manages,
  transfers,
}: {
  workspaceId: string;
  member: Member;
  isMe: boolean;
  manages: boolean;
  transfers: boolean;
}) {
  const changeRole = useChangeMemberRole(workspaceId);
  const remove = useRemoveMember(workspaceId);
  const transfer = useTransferOwnership(workspaceId);
  const error = changeRole.error ?? remove.error ?? transfer.error;
  const name = `${member.display_name}${isMe ? " (you)" : ""}`;

  return (
    <TableRow aria-label={member.display_name}>
      <TableCell className="font-medium">
        {name}
        {!member.is_active && " — deactivated"}
      </TableCell>
      <TableCell className="text-sand-11">{member.email}</TableCell>
      <TableCell>
        {manages ? (
          <RoleSelect
            aria-label={`Role of ${member.display_name}`}
            className="h-8 w-auto"
            value={member.role}
            disabled={changeRole.isPending}
            onChange={(event) =>
              changeRole.mutate({
                userId: member.user_id,
                role: event.target.value as WorkspaceRole,
              })
            }
          />
        ) : (
          ROLE_LABELS[member.role]
        )}
      </TableCell>
      {manages && (
        <TableCell>
          {!isMe && (
            <Actions>
              <ConfirmButton
                label="Remove"
                confirm={`Remove ${member.display_name}`}
                disabled={remove.isPending}
                onConfirm={() => remove.mutate(member.user_id)}
              />
              {transfers && member.role !== "owner" && (
                <ConfirmButton
                  label="Transfer ownership"
                  confirm={`Make ${member.display_name} owner, and become an editor`}
                  disabled={transfer.isPending}
                  onConfirm={() => transfer.mutate(member.user_id)}
                />
              )}
            </Actions>
          )}
          {error && <ErrorText>{error.message}</ErrorText>}
        </TableCell>
      )}
    </TableRow>
  );
}

function LeaveWorkspace({ workspaceId }: { workspaceId: string }) {
  const leave = useLeaveWorkspace(workspaceId);
  const router = useRouter();
  return (
    <Actions>
      <ConfirmButton
        label="Leave Workspace"
        confirm="Leave this Workspace"
        disabled={leave.isPending}
        onConfirm={() =>
          leave.mutate(undefined, { onSuccess: () => router.push("/") })
        }
      />
      {leave.isError && <ErrorText>{leave.error.message}</ErrorText>}
    </Actions>
  );
}

/** A button that asks once more before acting. */
function ConfirmButton({
  label,
  confirm,
  disabled,
  onConfirm,
}: {
  label: string;
  confirm: string;
  disabled: boolean;
  onConfirm: () => void;
}) {
  const [asking, setAsking] = useState(false);
  if (!asking) {
    return (
      <Button
        type="button"
        size="sm"
        disabled={disabled}
        onClick={() => setAsking(true)}
      >
        {label}
      </Button>
    );
  }
  return (
    <>
      <Button
        type="button"
        size="sm"
        variant="primary"
        disabled={disabled}
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        {confirm}
      </Button>
      <Button type="button" size="sm" onClick={() => setAsking(false)}>
        Cancel
      </Button>
    </>
  );
}
