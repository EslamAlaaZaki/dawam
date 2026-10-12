"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { useRegister, useRegistration } from "../api/account";
import { useMe } from "../api/queries";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Loading } from "../shell/Loading";
import { AuthCard, AuthError, AuthField, AuthHint, authLinkClasses } from "./AuthFrame";

/** Sign up with email, display name and password, while self-registration is open. */
export function SignUpPage() {
  const me = useMe();
  const registration = useRegistration();
  const register = useRegister();
  const router = useRouter();
  const [mismatch, setMismatch] = useState(false);
  // Signed in already, or just now: signing up fills the `me` query.
  const signedIn = Boolean(me.data);

  useEffect(() => {
    if (signedIn) {
      router.replace("/");
    }
  }, [signedIn, router]);

  if (signedIn) {
    return null;
  }
  if (registration.isPending) {
    return <Loading />;
  }
  if (registration.isError || !registration.data.open) {
    return (
      <AuthCard title="Create an account" titleId="signup-title">
        <p>
          {registration.isError
            ? `Could not check whether sign-up is open: ${registration.error.message}`
            : "Self-registration is turned off. Ask an admin for an account."}
        </p>
        <AuthHint className="text-center">
          <Link href="/login" className={authLinkClasses}>
            Sign in
          </Link>
        </AuthHint>
      </AuthCard>
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    if (password !== String(form.get("confirm") ?? "")) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    register.mutate({
      email: String(form.get("email") ?? ""),
      display_name: String(form.get("display_name") ?? ""),
      password,
    });
  }

  const problem = mismatch
    ? "The passwords do not match."
    : register.isError
      ? register.error.message
      : null;

  return (
    <AuthCard title="Create an account" titleId="signup-title" onSubmit={onSubmit}>
      <AuthField id="signup-email" label="Email">
        <Input id="signup-email" name="email" type="email" autoComplete="email" required />
      </AuthField>
      <AuthField id="signup-name" label="Display name">
        <Input id="signup-name" name="display_name" autoComplete="name" required maxLength={200} />
      </AuthField>
      <AuthField id="signup-password" label="Password">
        <Input
          id="signup-password"
          name="password"
          type="password"
          autoComplete="new-password"
          aria-describedby="signup-password-hint"
          required
        />
      </AuthField>
      <AuthField id="signup-confirm" label="Confirm password">
        <Input id="signup-confirm" name="confirm" type="password" autoComplete="new-password" required />
      </AuthField>
      <AuthHint id="signup-password-hint">
        At least 10 characters, and not a common password. A few unrelated words make a good
        one.
      </AuthHint>
      {problem && <AuthError>{problem}</AuthError>}
      <Button type="submit" variant="primary" size="lg" disabled={register.isPending}>
        Create account
      </Button>
      <AuthHint className="text-center">
        Already have an account?{" "}
        <Link href="/login" className={authLinkClasses}>
          Sign in
        </Link>
      </AuthHint>
    </AuthCard>
  );
}
