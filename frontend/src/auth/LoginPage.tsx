"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, type FormEvent } from "react";

import { useRegistration } from "../api/account";
import { useMe, useSignIn } from "../api/queries";
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
    <section className="page login">
      <form className="login-form" onSubmit={onSubmit} aria-labelledby="login-title">
        <h2 id="login-title">Sign in</h2>
        <label>
          Email
          <input name="email" type="email" autoComplete="username" required />
        </label>
        <label>
          Password
          <input name="password" type="password" autoComplete="current-password" required />
        </label>
        {signIn.isError && (
          <p className="login-error" role="alert">
            {signIn.error.message}
          </p>
        )}
        <button type="submit" disabled={signIn.isPending}>
          Sign in
        </button>
        {registrationOpen && (
          <p className="form-hint">
            New here? <Link href="/signup">Create an account</Link>
          </p>
        )}
      </form>
    </section>
  );
}
