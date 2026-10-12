"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, type FormEvent } from "react";

import { useRegistration } from "../api/account";
import { useMe, useSignIn } from "../api/queries";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { AuthCard, AuthError, AuthField, AuthHint, authLinkClasses } from "./AuthFrame";
import { safeReturnPath } from "./returnPath";

export function LoginPage() {
  const me = useMe();
  const signIn = useSignIn();
  // The sign-up link shows only while self-registration is open.
  const registrationOpen = useRegistration().data?.open === true;
  const router = useRouter();
  const from = useSearchParams().get("from");
  // Signed in already, or just now: signing in fills the `me` query.
  const signedIn = Boolean(me.data);

  useEffect(() => {
    if (signedIn) {
      router.replace(safeReturnPath(from));
    }
  }, [signedIn, from, router]);

  if (signedIn) {
    return null;
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    signIn.mutate({
      email: String(form.get("email") ?? ""),
      password: String(form.get("password") ?? ""),
    });
  }

  return (
    <AuthCard title="Sign in" titleId="login-title" onSubmit={onSubmit}>
      <AuthField id="login-email" label="Email">
        <Input id="login-email" name="email" type="email" autoComplete="username" required />
      </AuthField>
      <AuthField
        id="login-password"
        label="Password"
        aside={
          <Link href="/forgot-password" className={authLinkClasses}>
            Forgot your password?
          </Link>
        }
      >
        <Input
          id="login-password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
        />
      </AuthField>
      {signIn.isError && <AuthError>{signIn.error.message}</AuthError>}
      <Button type="submit" variant="primary" size="lg" disabled={signIn.isPending}>
        Sign in
      </Button>
      {registrationOpen && (
        <AuthHint className="text-center">
          New here?{" "}
          <Link href="/signup" className={authLinkClasses}>
            Create an account
          </Link>
        </AuthHint>
      )}
    </AuthCard>
  );
}
