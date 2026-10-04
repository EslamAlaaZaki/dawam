"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, type ReactNode } from "react";

import { useMe } from "../api/queries";
import { loginPath } from "./returnPath";

export function Loading() {
  return <p className="page">Loading…</p>;
}

/** Renders `children` for a signed-in user; sends anyone else to `/login`, and back after. */
export function RequireSignIn({ children }: { children: ReactNode }) {
  const me = useMe();
  const router = useRouter();
  const pathname = usePathname();
  const search = useSearchParams().toString();
  const anonymous = me.data === null;
  const here = search ? `${pathname}?${search}` : pathname;

  useEffect(() => {
    if (anonymous) {
      router.replace(loginPath(here));
    }
  }, [anonymous, here, router]);

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
  if (anonymous) {
    return null;
  }
  return children;
}
