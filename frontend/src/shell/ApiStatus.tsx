"use client";

import { useApiVersion } from "../api/queries";

export function ApiStatus() {
  const version = useApiVersion();
  if (version.isPending) {
    return <span>Connecting to the API…</span>;
  }
  if (version.isError) {
    return <span role="alert">API unavailable: {version.error.message}</span>;
  }
  return <span>API version {version.data.version}</span>;
}
