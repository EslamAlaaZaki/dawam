"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { useRegister, useRegistration } from "../api/account";
import { useMe } from "../api/queries";
import { Loading } from "../shell/Loading";

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
      <section className="page login">
        <div className="login-form">
          <h2>Create an account</h2>
          <p>
            {registration.isError
              ? `Could not check whether sign-up is open: ${registration.error.message}`
              : "Self-registration is turned off. Ask an admin for an account."}
          </p>
          <Link href="/login">Sign in</Link>
        </div>
      </section>
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
    <section className="page login">
      <form className="login-form" onSubmit={onSubmit} aria-labelledby="signup-title">
        <h2 id="signup-title">Create an account</h2>
        <label>
          Email
          <input name="email" type="email" autoComplete="email" required />
        </label>
        <label>
          Display name
          <input name="display_name" autoComplete="name" required maxLength={200} />
        </label>
        <label>
          Password
          <input name="password" type="password" autoComplete="new-password" required />
        </label>
        <label>
          Confirm password
          <input name="confirm" type="password" autoComplete="new-password" required />
        </label>
        <p className="form-hint">
          At least 10 characters, and not a common password. A few unrelated words make a good
          one.
        </p>
        {problem && (
          <p className="login-error" role="alert">
            {problem}
          </p>
        )}
        <button type="submit" disabled={register.isPending}>
          Create account
        </button>
        <p className="form-hint">
          Already have an account? <Link href="/login">Sign in</Link>
        </p>
      </form>
    </section>
  );
}
