"use client";

import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";

import { useChangePassword, useSignOutEverywhere, useUpdateDisplayName } from "../api/account";
import { useMe, type Me } from "../api/queries";

/** The signed-in user's own account: display name, password and sessions. */
export function ProfilePage() {
  const me = useMe();
  if (!me.data) {
    return null; // the signed-in layout only renders this once `me` is known
  }
  return (
    <section className="page settings">
      <h2>Your profile</h2>
      <p>
        Signed in as <strong>{me.data.email}</strong>
      </p>
      <DisplayNameForm me={me.data} />
      <ChangePasswordForm />
      <SignOutEverywhere />
    </section>
  );
}

function DisplayNameForm({ me }: { me: Me }) {
  const update = useUpdateDisplayName();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    update.mutate(String(new FormData(event.currentTarget).get("display_name") ?? ""));
  }

  return (
    <section className="settings-section" aria-labelledby="display-name-title">
      <h3 id="display-name-title">Name</h3>
      <form className="settings-form" onSubmit={onSubmit}>
        <label>
          Display name
          <input
            name="display_name"
            defaultValue={me.display_name}
            autoComplete="name"
            maxLength={200}
          />
        </label>
        {update.isError && (
          <p className="login-error" role="alert">
            {update.error.message}
          </p>
        )}
        {update.isSuccess && <p className="form-ok">Display name saved.</p>}
        <button type="submit" disabled={update.isPending}>
          Save name
        </button>
      </form>
    </section>
  );
}

function ChangePasswordForm() {
  const change = useChangePassword();
  const [mismatch, setMismatch] = useState(false);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const next = String(form.get("new_password") ?? "");
    if (next !== String(form.get("confirm") ?? "")) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    change.mutate(
      { current_password: String(form.get("current_password") ?? ""), new_password: next },
      { onSuccess: () => formElement.reset() },
    );
  }

  const problem = mismatch
    ? "The new passwords do not match."
    : change.isError
      ? change.error.message
      : null;

  return (
    <section className="settings-section" aria-labelledby="password-title">
      <h3 id="password-title">Change password</h3>
      <form className="settings-form" onSubmit={onSubmit}>
        <label>
          Current password
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
        <p className="form-hint">
          At least 10 characters, and not a common password. Changing it signs you out on
          every other device.
        </p>
        {problem && (
          <p className="login-error" role="alert">
            {problem}
          </p>
        )}
        {change.isSuccess && !mismatch && (
          <p className="form-ok">Password changed. Your other sessions have been signed out.</p>
        )}
        <button type="submit" disabled={change.isPending}>
          Change password
        </button>
      </form>
    </section>
  );
}

function SignOutEverywhere() {
  const router = useRouter();
  const signOut = useSignOutEverywhere({ onSignedOut: () => router.replace("/login") });
  return (
    <section className="settings-section" aria-labelledby="sessions-title">
      <h3 id="sessions-title">Sessions</h3>
      <p className="form-hint">
        Lost a device, or signed in somewhere you shouldn&apos;t have? Sign out of every
        session, this one included.
      </p>
      {signOut.isError && (
        <p className="login-error" role="alert">
          Could not sign out everywhere: {signOut.error.message}
        </p>
      )}
      <button
        type="button"
        className="settings-danger"
        disabled={signOut.isPending}
        onClick={() => signOut.mutate()}
      >
        Sign out everywhere
      </button>
    </section>
  );
}
