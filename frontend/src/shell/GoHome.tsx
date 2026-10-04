"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

/** Replaces the current page with the home page. */
export function GoHome() {
  const router = useRouter();
  useEffect(() => {
    router.replace("/");
  }, [router]);
  return null;
}
