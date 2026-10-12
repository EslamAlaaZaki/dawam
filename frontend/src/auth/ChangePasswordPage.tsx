"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useChangePassword } from "../api/account";
import { meQueryKey, useSignOut } from "../api/queries";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { AuthCard, AuthError, AuthField, AuthHint } from "./AuthFrame";

/**
 * Where a user an admin created with a temporary password lands until they replace it
 * (spec §6.1): the API refuses them everything else meanwhile.
 */
export function ChangePasswordPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const change = useChangePassword();
  const signOut = useSignOut();
  const [mismatch, setMismatch] = useState(false);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = String(form.get("new_password") ?? "");
    if (next !== String(form.get("confirm") ?? "")) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    change.mutate(
      { current_password: String(form.get("current_password") ?? ""), new_password: next },
      {
        onSuccess: async () => {
          await queryClient.invalidateQueries({ queryKey: meQueryKey });
          router.replace("/");
        },
      },
    );
  }

  const problem = mismatch
    ? "The new passwords do not match."
    : change.isError
      ? change.error.message
      : null;

  return (
    <AuthCard
      title="Choose a new password"
      titleId="change-password-title"
      description="You signed in with a temporary password from an administrator. Choose your own password to continue."
      onSubmit={onSubmit}
    >
      <AuthField id="change-current" label="Temporary password">
        <Input
          id="change-current"
          name="current_password"
          type="password"
          autoComplete="current-password"
          required
        />
      </AuthField>
      <AuthField id="change-new" label="New password">
        <Input
          id="change-new"
          name="new_password"
          type="password"
          autoComplete="new-password"
          aria-describedby="change-new-hint"
          required
        />
      </AuthField>
      <AuthField id="change-confirm" label="Confirm new password">
        <Input id="change-confirm" name="confirm" type="password" autoComplete="new-password" required />
      </AuthField>
      <AuthHint id="change-new-hint">At least 10 characters, and not a common password.</AuthHint>
      {problem && <AuthError>{problem}</AuthError>}
      <Button type="submit" variant="primary" size="lg" disabled={change.isPending}>
        Change password
      </Button>
      <Button
        type="button"
        variant="ghost"
        onClick={() => signOut.mutate()}
        disabled={signOut.isPending}
      >
        Sign out
      </Button>
      {signOut.isError && <AuthError>Could not sign out: {signOut.error.message}</AuthError>}
    </AuthCard>
  );
}
