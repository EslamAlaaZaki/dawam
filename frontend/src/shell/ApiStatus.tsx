"use client";

import { useApiVersion } from "../api/queries";

/** The API's version, or why it cannot be reached; `dot` adds a status light before it. */
export function ApiStatus({ dot = false }: { dot?: boolean }) {
  const version = useApiVersion();
  const light = dot && (
    <i
      aria-hidden
      className={`me-2 inline-block size-1.5 rounded-full ${
        version.isError ? "bg-danger" : version.isPending ? "bg-border-strong" : "bg-success"
      }`}
    />
  );
  if (version.isPending) {
    return <span>{light}Connecting to the API…</span>;
  }
  if (version.isError) {
    return (
      <span role="alert">
        {light}API unavailable: {version.error.message}
      </span>
    );
  }
  return (
    <span>
      {light}API version {version.data.version}
    </span>
  );
}
