import type { FormEvent } from "react";
import { Navigate, useLocation } from "react-router";

import { useMe, useSignIn } from "../api/queries";
import type { LoginLocationState } from "./RequireSignIn";

export function LoginPage() {
  const me = useMe();
  const signIn = useSignIn();
  const location = useLocation();
  const from = (location.state as LoginLocationState | null)?.from ?? "/";

  if (me.data) {
    // Signed in already, or just now: signing in fills the `me` query.
    return <Navigate to={from} replace />;
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
      </form>
    </section>
  );
}
