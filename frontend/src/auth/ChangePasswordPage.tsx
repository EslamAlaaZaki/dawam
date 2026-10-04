"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useChangePassword } from "../api/account";
import { meQueryKey, useSignOut } from "../api/queries";

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
    <section className="page settings">
      <h2>Choose a new password</h2>
      <p>
        You signed in with a temporary password from an administrator. Choose your own
        password to continue.
      </p>
      <form className="settings-form" onSubmit={onSubmit}>
        <label>
          Temporary password
          <input
            name="current_password"
            type="password"
            autoComplete="current-password"
            required
          />
        </label>
        <label>
          New password
          <input name="new_password" type="password" autoComplete="new-password" required />
        </label>
        <label>
          Confirm new password
          <input name="confirm" type="password" autoComplete="new-password" required />
        </label>
        <p className="form-hint">At least 10 characters, and not a common password.</p>
        {problem && (
          <p className="login-error" role="alert">
            {problem}
          </p>
        )}
        <div className="admin-actions">
          <button type="submit" disabled={change.isPending}>
            Change password
          </button>
          <button
            type="button"
            className="secondary"
            onClick={() => signOut.mutate()}
            disabled={signOut.isPending}
          >
            Sign out
          </button>
        </div>
      </form>
    </section>
  );
}
