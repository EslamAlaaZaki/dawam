"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useMe } from "../api/queries";
import { Loading } from "../shell/Loading";
import { loginPath } from "./returnPath";

const CHANGE_PASSWORD = "/change-password";

/**
 * Renders `children` for a signed-in user; sends anyone else to `/login`, and back after.
 * A user who must replace a temporary password is kept on `/change-password` until then.
 */
export function RequireSignIn({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const anonymous = me.data === null;
  const here = search ? `${pathname}?${search}` : pathname;
  const mustChangePassword = me.data?.must_change_password === true;
  const misplaced = Boolean(me.data) && mustChangePassword !== (pathname === CHANGE_PASSWORD);

  useEffect(() => {
    if (anonymous) {
      router.replace(loginPath(here));
    } else if (misplaced) {
      router.replace(mustChangePassword ? CHANGE_PASSWORD : "/");
    }
  }, [anonymous, here, misplaced, mustChangePassword, router]);

  if (me.isPending) {
    return <Loading />;
  }
  if (me.isError) {
    return (
      <p className="page" role="alert">
        Could not check your session: {me.error.message}
      </p>
    );
  }
  if (anonymous || misplaced) {
    return null;
  }
  return children;
}
