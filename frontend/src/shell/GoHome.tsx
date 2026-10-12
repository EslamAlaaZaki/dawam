"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { Loading } from "./Loading";

/** Replaces the current page with the home page. */
export function GoHome() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/");
  }, [router]);
  return <Loading label="Taking you home…" />;
}
