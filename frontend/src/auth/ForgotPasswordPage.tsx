"use client";

import type { FormEvent } from "react";

import { useForgotPassword } from "../api/passwordReset";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { AuthCard, AuthError, AuthField, AuthHint, authLinkClasses } from "./AuthFrame";

export function ForgotPasswordPage() {
  const forgot = useForgotPassword();

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    forgot.mutate(String(form.get("email") ?? ""));
  }

  return (
    <AuthCard
      title="Reset your password"
      titleId="forgot-title"
      onSubmit={onSubmit}
      description={
        forgot.isSuccess
          ? undefined
          : "Enter your account's email and DAWAM will send you a reset link."
      }
    >
      {forgot.isSuccess ? (
        <p role="status">
          If an account uses that email, a link to choose a new password is on its way. It
          works once, for 30 minutes. If no email arrives, ask an administrator: when DAWAM
          cannot send email, they can give you the link.
        </p>
      ) : (
        <>
          <AuthField id="forgot-email" label="Email">
            <Input id="forgot-email" name="email" type="email" autoComplete="username" required />
          </AuthField>
          {forgot.isError && <AuthError>{forgot.error.message}</AuthError>}
          <Button type="submit" variant="primary" size="lg" disabled={forgot.isPending}>
            Send reset link
          </Button>
        </>
      )}
      <AuthHint className="text-center">
        <a href="/login" className={authLinkClasses}>
          Back to sign in
        </a>
      </AuthHint>
    </AuthCard>
  );
}
