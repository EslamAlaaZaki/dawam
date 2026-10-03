import { Navigate, Outlet, useLocation } from "react-router";

import { useMe } from "../api/queries";

export interface LoginLocationState {
  /** Where to go after signing in. */
  from?: string;
}

/** Renders the nested routes for a signed-in user; sends anyone else to `/login`. */
export function RequireSignIn() {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) {
    return <p className="page">Loading…</p>;
  }
  if (me.isError) {
    return (
      <p className="page" role="alert">
        Could not check your session: {me.error.message}
      </p>
    );
  }
  if (me.data === null) {
    const state: LoginLocationState = { from: location.pathname + location.search };
    return <Navigate to="/login" replace state={state} />;
  }
  return <Outlet />;
}
