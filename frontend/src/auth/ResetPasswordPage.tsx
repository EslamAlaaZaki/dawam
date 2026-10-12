"use client";

import { useState, type FormEvent } from "react";

import { ApiError } from "../api/client";
import { useResetPassword } from "../api/passwordReset";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { AuthCard, AuthError, AuthField, AuthHint, authLinkClasses } from "./AuthFrame";
import { useFragmentToken } from "./fragmentToken";
import { ErrorText } from "../admin/parts";

export function ResetPasswordPage() {
  const token = useFragmentToken();
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
        <AuthHint className="text-center">
          <a href="/login" className={authLinkClasses}>
            Sign in
          </a>
        </AuthHint>
      </>
    );
  } else if (
    !token ||
    (reset.error instanceof ApiError && reset.error.code === "invalid_reset_token")
  ) {
    content = (
      <>
        <ErrorText>
          This reset link is invalid, used or expired. Links work once, for 30 minutes.
        </ErrorText>
        <AuthHint className="text-center">
          <a href="/forgot-password" className={authLinkClasses}>
            Ask for a new link
          </a>
        </AuthHint>
      </>
    );
  } else {
    content = (
      <>
        <AuthField id="reset-password" label="New password">
          <Input
            id="reset-password"
            name="password"
            type="password"
            autoComplete="new-password"
            minLength={10}
            required
          />
        </AuthField>
        <AuthField id="reset-confirm" label="Repeat the new password">
          <Input id="reset-confirm" name="confirm" type="password" autoComplete="new-password" required />
        </AuthField>
        {mismatch && <AuthError>The two passwords are different.</AuthError>}
        {reset.isError && <AuthError>{reset.error.message}</AuthError>}
        <Button type="submit" variant="primary" size="lg" disabled={reset.isPending}>
          Set new password
        </Button>
      </>
    );
  }

  return (
    <AuthCard title="Choose a new password" titleId="reset-title" onSubmit={onSubmit}>
      {content}
    </AuthCard>
  );
}
