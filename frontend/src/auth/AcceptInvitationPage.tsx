"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { useAcceptInvitation, useInvitationLink } from "../api/invitations";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { AuthCard, AuthError, AuthField, AuthHint } from "./AuthFrame";
import { useFragmentToken } from "./fragmentToken";
import { Loading } from "../shell/Loading";

/** Join DAWAM from an invitation link: choose a display name and password (story 10). */
export function AcceptInvitationPage() {
  const token = useFragmentToken();
  const link = useInvitationLink(token);
  const accept = useAcceptInvitation();
  const router = useRouter();
  const [mismatch, setMismatch] = useState(false);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    if (password !== String(form.get("confirm") ?? "")) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    if (token) {
      accept.mutate(
        { token, display_name: String(form.get("display_name") ?? ""), password },
        { onSuccess: () => router.replace("/") },
      );
    }
  }

  const invalid =
    token === "" ||
    [link.error, accept.error].some(
      (error) => error instanceof ApiError && error.code === "invalid_invitation",
    );

  let content;
  if (token === null || (token && link.isPending)) {
    content = <Loading className="p-0" />;
  } else if (invalid) {
    content = (
      <p role="alert" className="text-red-11">
        This invitation link is invalid, used, revoked or expired. Ask an admin for a new
        one.
      </p>
    );
  } else if (link.isError) {
    content = <p role="alert" className="text-red-11">Could not check the invitation: {link.error.message}</p>;
  } else {
    const problem = mismatch
      ? "The passwords do not match."
      : accept.isError
        ? accept.error.message
        : null;
    content = (
      <>
        <p>
          You are invited to join DAWAM as <strong>{link.data?.email}</strong>.
        </p>
        <AuthField id="invitation-name" label="Display name">
          <Input id="invitation-name" name="display_name" autoComplete="name" required maxLength={200} />
        </AuthField>
        <AuthField id="invitation-password" label="Password">
          <Input
            id="invitation-password"
            name="password"
            type="password"
            autoComplete="new-password"
            aria-describedby="invitation-password-hint"
            required
          />
        </AuthField>
        <AuthField id="invitation-confirm" label="Confirm password">
          <Input
            id="invitation-confirm"
            name="confirm"
            type="password"
            autoComplete="new-password"
            required
          />
        </AuthField>
        <AuthHint id="invitation-password-hint">
          At least 10 characters, and not a common password. A few unrelated words make a
          good one.
        </AuthHint>
        {problem && <AuthError>{problem}</AuthError>}
        <Button type="submit" variant="primary" size="lg" disabled={accept.isPending}>
          Join DAWAM
        </Button>
      </>
    );
  }

  return (
    <AuthCard title="Accept your invitation" titleId="invitation-title" onSubmit={onSubmit}>
      {content}
    </AuthCard>
  );
}
