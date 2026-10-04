"use client";

import { useState, useSyncExternalStore, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { useResetPassword } from "../api/passwordReset";

// The token travels in the link's fragment (`/reset-password#token=…`), which the
// browser never sends to a server, so it stays out of proxy logs and Referer headers.
function subscribe(onChange: () => void): () => void {
  window.addEventListener("hashchange", onChange);
  return () => window.removeEventListener("hashchange", onChange);
}

function tokenFromHash(): string {
  return new URLSearchParams(window.location.hash.slice(1)).get("token") ?? "";
}

/** The token in the page's fragment; `null` while rendering on the server. */
function useResetToken(): string | null {
  return useSyncExternalStore(subscribe, tokenFromHash, () => null);
}

export function ResetPasswordPage() {
  const token = useResetToken();
  const reset = useResetPassword();
  const [mismatch, setMismatch] = useState(false);

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const confirm = String(form.get("confirm") ?? "");
    setMismatch(password !== confirm);
    if (password === confirm && token) {
      reset.mutate({ token, password });
    }
  }

  let content;
  if (token === null) {
    content = null;
  } else if (reset.isSuccess) {
    content = (
      <>
        <p role="status">
          Your password is changed, and every device that was signed in is signed out.
        </p>
        <a href="/login">Sign in</a>
      </>
    );
  } else if (
    !token ||
    (reset.error instanceof ApiError && reset.error.code === "invalid_reset_token")
  ) {
    content = (
      <>
        <p role="alert">
          This reset link is invalid, used or expired. Links work once, for 30 minutes.
        </p>
        <a href="/forgot-password">Ask for a new link</a>
      </>
    );
  } else {
    content = (
      <>
        <label>
          New password
          <input
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={10}
            required
          />
        </label>
        <label>
          Repeat the new password
          <input name="confirm" type="password" autoComplete="new-password" required />
        </label>
        {mismatch && (
          <p className="login-error" role="alert">
            The two passwords are different.
          </p>
        )}
        {reset.isError && (
          <p className="login-error" role="alert">
            {reset.error.message}
          </p>
        )}
        <button type="submit" disabled={reset.isPending}>
          Set new password
        </button>
      </>
    );
  }

  return (
    <section className="page login">
      <form className="login-form" onSubmit={onSubmit} aria-labelledby="reset-title">
        <h2 id="reset-title">Choose a new password</h2>
        {content}
      </form>
    </section>
  );
}
