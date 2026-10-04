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
import { ROLE_LABELS, allows, type Workspace, type WorkspaceRole } from "../api/workspaces";
import { Loading } from "../shell/Loading";

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
    <section aria-labelledby="members-title" className="members">
      <h3 id="members-title">Members</h3>
      {manages && <AddMemberForm workspaceId={workspace.id} />}
      {members.isPending ? (
        <Loading />
      ) : members.isError ? (
        <p role="alert">Could not load the members: {members.error.message}</p>
      ) : (
        <table className="admin-table">
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Email</th>
              <th scope="col">Role</th>
              {manages && <th scope="col">Actions</th>}
            </tr>
          </thead>
          <tbody>
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
          </tbody>
        </table>
      )}
      {allows(workspace, "workspace.leave") && <LeaveWorkspace workspaceId={workspace.id} />}
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
      { onSuccess: () => form.reset() },
    );
  }

  const result = add.data;
  return (
    <form className="admin-form" onSubmit={onSubmit} aria-label="Add member">
      <label>
        Email
        <input name="email" type="email" autoComplete="off" required />
      </label>
      <label>
        Role
        <RoleSelect name="role" defaultValue="editor" />
      </label>
      <p className="form-hint">
        Someone without an account gets an invitation to join DAWAM and this Workspace.
      </p>
      <div className="admin-actions">
        <button type="submit" disabled={add.isPending}>
          Add member
        </button>
      </div>
      {add.isError && (
        <p className="form-error" role="alert">
          {add.error.message}
        </p>
      )}
      {result?.outcome === "added" && (
        <p className="form-ok">
          {result.member.display_name} is now {withArticle(result.member.role)}.
        </p>
      )}
      {result?.outcome === "invited" && result.delivery === "sent" && (
        <p className="form-ok">Invitation emailed to {result.invitation.email}.</p>
      )}
      {result?.outcome === "invited" && result.delivery === "link_for_admin" && (
        <p className="form-ok">
          {result.invitation.email} is invited, but the email could not be sent: an admin can
          copy the link from <Link href="/admin/email/links">Links to share</Link>.
        </p>
      )}
    </form>
  );
}

/** A select of the Workspace roles, by their labels. */
function RoleSelect(props: ComponentProps<"select">) {
  return (
    <select {...props}>
      {ROLES.map((role) => (
        <option key={role} value={role}>
          {ROLE_LABELS[role]}
        </option>
      ))}
    </select>
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
    <tr aria-label={member.display_name}>
      <td>
        {name}
        {!member.is_active && " — deactivated"}
      </td>
      <td>{member.email}</td>
      <td>
        {manages ? (
          <RoleSelect
            aria-label={`Role of ${member.display_name}`}
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
      </td>
      {manages && (
        <td>
          {!isMe && (
            <div className="admin-actions">
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
            </div>
          )}
          {error && (
            <p className="form-error" role="alert">
              {error.message}
            </p>
          )}
        </td>
      )}
    </tr>
  );
}

function LeaveWorkspace({ workspaceId }: { workspaceId: string }) {
  const leave = useLeaveWorkspace(workspaceId);
  const router = useRouter();
  return (
    <div className="form-actions">
      <ConfirmButton
        label="Leave Workspace"
        confirm="Leave this Workspace"
        disabled={leave.isPending}
        onConfirm={() => leave.mutate(undefined, { onSuccess: () => router.push("/") })}
      />
      {leave.isError && (
        <p className="form-error" role="alert">
          {leave.error.message}
        </p>
      )}
    </div>
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
      <button type="button" className="secondary" disabled={disabled} onClick={() => setAsking(true)}>
        {label}
      </button>
    );
  }
  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        {confirm}
      </button>
      <button type="button" className="secondary" onClick={() => setAsking(false)}>
        Cancel
      </button>
    </>
  );
}
