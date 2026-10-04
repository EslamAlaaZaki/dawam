"use client";

import Link from "next/link";
import { type FormEvent } from "react";

import {
  useInviteUser,
  usePendingInvitations,
  useRevokeInvitation,
  type PendingInvitation,
} from "../api/invitations";
import { Loading } from "../shell/Loading";

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

/** Invite people by email, and see and revoke the invitations still pending (stories 10, 15). */
export function InvitationsPage() {
  const invitations = usePendingInvitations();

  return (
    <section className="page admin-page">
      <h2>Invitations</h2>
      <p>
        An invitation lets someone join while self-registration is off: they get a link,
        valid 7 days, to choose a display name and password.
      </p>
      <InviteForm />
      <h3>Pending invitations</h3>
      {invitations.isPending ? (
        <Loading />
      ) : invitations.isError ? (
        <p role="alert">Could not load the invitations: {invitations.error.message}</p>
      ) : invitations.data.pages[0]?.items.length === 0 ? (
        <p>No pending invitations.</p>
      ) : (
        <>
          <table className="admin-table">
            <thead>
              <tr>
                <th scope="col">Email</th>
                <th scope="col">Invited by</th>
                <th scope="col">Sent</th>
                <th scope="col">Expires</th>
                <th scope="col">Actions</th>
              </tr>
            </thead>
            <tbody>
              {invitations.data.pages.flatMap((page) =>
                page.items.map((invitation) => (
                  <InvitationRow key={invitation.id} invitation={invitation} />
                )),
              )}
            </tbody>
          </table>
          {invitations.hasNextPage && (
            <button
              type="button"
              onClick={() => void invitations.fetchNextPage()}
              disabled={invitations.isFetchingNextPage}
            >
              Load more
            </button>
          )}
        </>
      )}
    </section>
  );
}

function InviteForm() {
  const invite = useInviteUser();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const email = String(new FormData(form).get("email") ?? "").trim();
    invite.mutate(email, { onSuccess: () => form.reset() });
  }

  const sent = invite.data;
  return (
    <form className="admin-form" onSubmit={onSubmit} aria-label="Invite user">
      <label>
        Email
        <input name="email" type="email" autoComplete="off" required />
      </label>
      <div className="admin-actions">
        <button type="submit" disabled={invite.isPending}>
          Send invitation
        </button>
      </div>
      {invite.isError && (
        <p className="login-error" role="alert">
          {invite.error.message}
        </p>
      )}
      {sent?.delivery === "sent" && (
        <p className="form-ok">Invitation emailed to {sent.invitation.email}.</p>
      )}
      {sent?.delivery === "link_for_admin" && (
        <p className="form-ok">
          The invitation for {sent.invitation.email} could not be emailed: copy its link
          from <Link href="/admin/email/links">Links to share</Link> and pass it on.
        </p>
      )}
    </form>
  );
}

function InvitationRow({ invitation }: { invitation: PendingInvitation }) {
  const revoke = useRevokeInvitation();
  return (
    <tr aria-label={invitation.email}>
      <td>{invitation.email}</td>
      <td>{invitation.invited_by.display_name}</td>
      <td>{when(invitation.created_at)}</td>
      <td>{when(invitation.expires_at)}</td>
      <td>
        <div className="admin-actions">
          <button
            type="button"
            className="secondary"
            disabled={revoke.isPending}
            onClick={() => revoke.mutate(invitation.id)}
          >
            Revoke
          </button>
        </div>
        {revoke.isError && <p role="alert">{revoke.error.message}</p>}
      </td>
    </tr>
  );
}
