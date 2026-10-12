"use client";

import Link from "next/link";
import { type FormEvent } from "react";

import {
  useInviteUser,
  usePendingInvitations,
  useRevokeInvitation,
  type PendingInvitation,
} from "../api/invitations";
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
import { ErrorText, PageHead, TableCard, labelClasses, EmptyState, formCardClasses, h3Classes } from "./parts";

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

/** Invite people by email, and see and revoke the invitations still pending (stories 10, 15). */
export function InvitationsPage() {
  const invitations = usePendingInvitations();

  return (
    <section className="flex flex-col gap-5">
      <PageHead
        title="Invitations"
        description="An invitation lets someone join while self-registration is off: they get a link, valid 7 days, to choose a display name and password."
      />
      <InviteForm />
      <h3 className={h3Classes}>Pending invitations</h3>
      {invitations.isPending ? (
        <Loading />
      ) : invitations.isError ? (
        <ErrorText>
          Could not load the invitations: {invitations.error.message}
        </ErrorText>
      ) : invitations.data.pages[0]?.items.length === 0 ? (
        <EmptyState>No pending invitations.</EmptyState>
      ) : (
        <>
          <TableCard>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col">Email</TableHead>
                  <TableHead scope="col">Invited by</TableHead>
                  <TableHead scope="col">Sent</TableHead>
                  <TableHead scope="col">Expires</TableHead>
                  <TableHead scope="col">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invitations.data.pages.flatMap((page) =>
                  page.items.map((invitation) => (
                    <InvitationRow
                      key={invitation.id}
                      invitation={invitation}
                    />
                  )),
                )}
              </TableBody>
            </Table>
          </TableCard>
          {invitations.hasNextPage && (
            <Button
              className="self-start"
              onClick={() => void invitations.fetchNextPage()}
              disabled={invitations.isFetchingNextPage}
            >
              Load more
            </Button>
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
    invite.mutate(email, {
      onSuccess: (result) => {
        form.reset();
        if (result.delivery === "sent") {
          toast.success(`Invitation emailed to ${result.invitation.email}.`);
        }
      },
    });
  }

  const sent = invite.data;
  return (
    <form
      className={formCardClasses}
      onSubmit={onSubmit}
      aria-label="Invite user"
    >
      <label className={labelClasses}>
        Email
        <Input name="email" type="email" autoComplete="off" required />
      </label>
      <Button
        type="submit"
        variant="primary"
        className="self-start"
        disabled={invite.isPending}
      >
        Send invitation
      </Button>
      {invite.isError && <ErrorText>{invite.error.message}</ErrorText>}
      {sent?.delivery === "link_for_admin" && (
        <p className="text-sm text-sand-11">
          The invitation for {sent.invitation.email} could not be emailed: copy
          its link from{" "}
          <Link
            href="/admin/email/links"
            className="font-medium text-sand-12 underline"
          >
            Links to share
          </Link>{" "}
          and pass it on.
        </p>
      )}
    </form>
  );
}

function InvitationRow({ invitation }: { invitation: PendingInvitation }) {
  const revoke = useRevokeInvitation();
  return (
    <TableRow aria-label={invitation.email}>
      <TableCell>{invitation.email}</TableCell>
      <TableCell>{invitation.invited_by.display_name}</TableCell>
      <TableCell className="text-sand-11">
        {when(invitation.created_at)}
      </TableCell>
      <TableCell className="text-sand-11">
        {when(invitation.expires_at)}
      </TableCell>
      <TableCell>
        <Button
          size="sm"
          disabled={revoke.isPending}
          onClick={() => revoke.mutate(invitation.id)}
        >
          Revoke
        </Button>
        {revoke.isError && <ErrorText>{revoke.error.message}</ErrorText>}
      </TableCell>
    </TableRow>
  );
}
