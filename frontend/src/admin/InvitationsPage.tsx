"use client";

import Link from "next/link";
import { MailPlus } from "lucide-react";
import { useState } from "react";

import {
  useInviteUser,
  usePendingInvitations,
  useRevokeInvitation,
  type PendingInvitation,
} from "../api/invitations";
import { ConfirmDialog } from "../components/entity/ConfirmDialog";
import { EntityFormDialog } from "../components/entity/EntityFormDialog";
import { EntityTable, type EntityColumn } from "../components/entity/EntityTable";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { toast } from "../components/ui/sonner";
import { PageHeader } from "../components/PageHeader";
import { labelClasses } from "./parts";

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

const INVITATION_COLUMNS: EntityColumn<PendingInvitation>[] = [
  { header: "Email", cell: (invitation) => invitation.email },
  { header: "Invited by", cell: (invitation) => invitation.invited_by.display_name },
  { header: "Sent", className: "text-muted-foreground", cell: (invitation) => when(invitation.created_at) },
  { header: "Expires", className: "text-muted-foreground", cell: (invitation) => when(invitation.expires_at) },
];

/** Invite people by email, and see and revoke the invitations still pending (stories 10, 15). */
export function InvitationsPage() {
  const invitations = usePendingInvitations();
  const [inviting, setInviting] = useState(false);
  const [unsent, setUnsent] = useState<string | null>(null);
  const inviteButton = (
    <Button variant="primary" onClick={() => setInviting(true)}>
      <MailPlus aria-hidden />
      Invite
    </Button>
  );

  return (
    <section className="flex flex-col gap-5">
      <PageHeader
        title="Invitations"
        description="An invitation lets someone join while self-registration is off: they get a link, valid 7 days, to choose a display name and password."
        actions={inviteButton}
      />
      {unsent && (
        <p className="text-sm text-muted-foreground">
          The invitation for {unsent} could not be emailed: copy its link from{" "}
          <Link href="/admin/email/links" className="font-medium text-foreground underline">
            Links to share
          </Link>{" "}
          and pass it on.
        </p>
      )}
      <EntityTable
        rows={invitations.data?.pages.flatMap((page) => page.items)}
        rowKey={(invitation) => invitation.id}
        rowLabel={(invitation) => invitation.email}
        columns={INVITATION_COLUMNS}
        actions={(invitation) => <InvitationActions invitation={invitation} />}
        isPending={invitations.isPending}
        error={invitations.error}
        loadErrorPrefix="Could not load the invitations:"
        emptyMessage="No pending invitations."
        count="invitations"
        hasMore={invitations.hasNextPage}
        isLoadingMore={invitations.isFetchingNextPage}
        onLoadMore={() => void invitations.fetchNextPage()}
      />
      <InviteDialog open={inviting} onOpenChange={setInviting} onUnsent={setUnsent} />
    </section>
  );
}

function InviteDialog({
  open,
  onOpenChange,
  onUnsent,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUnsent: (email: string | null) => void;
}) {
  const invite = useInviteUser();

  function onSubmit(form: FormData) {
    const email = String(form.get("email") ?? "").trim();
    invite.mutate(email, {
      onSuccess: (result) => {
        if (result.delivery === "sent") {
          onUnsent(null);
          toast.success(`Invitation emailed to ${result.invitation.email}.`);
        } else {
          onUnsent(result.invitation.email);
        }
        onOpenChange(false);
      },
    });
  }

  return (
    <EntityFormDialog
      open={open}
      onOpenChange={(next) => {
        if (!next) invite.reset();
        onOpenChange(next);
      }}
      title="Invite user"
      description="They get a link, valid 7 days, to choose a display name and password."
      submitLabel="Send invitation"
      onSubmit={onSubmit}
      pending={invite.isPending}
      error={invite.error}
    >
      <label className={labelClasses}>
        Email
        <Input name="email" type="email" autoComplete="off" required />
      </label>
    </EntityFormDialog>
  );
}

function InvitationActions({ invitation }: { invitation: PendingInvitation }) {
  const revoke = useRevokeInvitation();
  const [revoking, setRevoking] = useState(false);
  return (
    <>
      <Button size="sm" onClick={() => setRevoking(true)}>
        Revoke
      </Button>
      <ConfirmDialog
        open={revoking}
        onOpenChange={(next) => {
          if (!next) revoke.reset();
          setRevoking(next);
        }}
        title={`Revoke the invitation for ${invitation.email}?`}
        description="Their link stops working. You can invite them again later."
        confirmLabel="Revoke"
        pending={revoke.isPending}
        error={revoke.error}
        onConfirm={() => revoke.mutate(invitation.id, { onSuccess: () => setRevoking(false) })}
      />
    </>
  );
}
