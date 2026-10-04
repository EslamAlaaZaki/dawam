"use client";

import Link from "next/link";

import { useMe, useSignOut } from "../api/queries";

export function SignedInUser() {
  const me = useMe();
  const signOut = useSignOut();
  if (!me.data) {
    return null;
  }
  return (
    <div className="shell-user">
      {me.data.system_role === "admin" && <Link href="/admin/settings">Admin settings</Link>}
      <Link href="/profile" className="shell-user-name">
        {me.data.display_name}
      </Link>
      <button type="button" onClick={() => signOut.mutate()} disabled={signOut.isPending}>
        Sign out
      </button>
      {signOut.isError && <span role="alert">Could not sign out: {signOut.error.message}</span>}
    </div>
  );
}
