"use client";

import type { FormEvent } from "react";

import { useForgotPassword } from "../api/passwordReset";

export function ForgotPasswordPage() {
  const forgot = useForgotPassword();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    forgot.mutate(String(form.get("email") ?? ""));
  }

  return (
    <section className="page login">
      <form className="login-form" onSubmit={onSubmit} aria-labelledby="forgot-title">
        <h2 id="forgot-title">Reset your password</h2>
        {forgot.isSuccess ? (
          <p role="status">
            If an account uses that email, a link to choose a new password is on its way.
            It works once, for 30 minutes. If no email arrives, ask an administrator: when
            DAWAM cannot send email, they can give you the link.
          </p>
        ) : (
          <>
            <p>Enter your account&apos;s email and DAWAM will send you a reset link.</p>
            <label>
              Email
              <input name="email" type="email" autoComplete="username" required />
            </label>
            {forgot.isError && (
              <p className="login-error" role="alert">
                {forgot.error.message}
              </p>
            )}
            <button type="submit" disabled={forgot.isPending}>
              Send reset link
            </button>
          </>
        )}
        <a href="/login">Back to sign in</a>
      </form>
    </section>
  );
}
