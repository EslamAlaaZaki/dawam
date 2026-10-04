"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { useAcceptInvitation, useInvitationLink } from "../api/invitations";
import { Loading } from "../shell/Loading";
import { useFragmentToken } from "./fragmentToken";

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
    content = <Loading />;
  } else if (invalid) {
    content = (
      <p role="alert">
        This invitation link is invalid, used, revoked or expired. Ask an admin for a new
        one.
      </p>
    );
  } else if (link.isError) {
    content = <p role="alert">Could not check the invitation: {link.error.message}</p>;
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
        <label>
          Display name
          <input name="display_name" autoComplete="name" required maxLength={200} />
        </label>
        <label>
          Password
          <input name="password" type="password" autoComplete="new-password" required />
        </label>
        <label>
          Confirm password
          <input name="confirm" type="password" autoComplete="new-password" required />
        </label>
        <p className="form-hint">
          At least 10 characters, and not a common password. A few unrelated words make a
          good one.
        </p>
        {problem && (
          <p className="login-error" role="alert">
            {problem}
          </p>
        )}
        <button type="submit" disabled={accept.isPending}>
          Join DAWAM
        </button>
      </>
    );
  }

  return (
    <section className="page login">
      <form className="login-form" onSubmit={onSubmit} aria-labelledby="invitation-title">
        <h2 id="invitation-title">Accept your invitation</h2>
        {content}
      </form>
    </section>
  );
}
