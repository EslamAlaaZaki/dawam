"use client";

import { useMe, useSignOut } from "../api/queries";

export function SignedInUser() {
  const me = useMe();
  const signOut = useSignOut();
  if (!me.data) {
    return null;
  }
  return (
    <div className="shell-user">
      <span className="shell-user-name">{me.data.display_name}</span>
      <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
        Sign out
      </button>
      {signOut.isError && <span role="alert">Could not sign out: {signOut.error.message}</span>}
    </div>
  );
}
