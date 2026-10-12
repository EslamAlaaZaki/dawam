"use client";

import { useApiVersion } from "../api/queries";

/** The API's version, or why it cannot be reached; `dot` adds a status light before it. */
export function ApiStatus({ dot = false }: { dot?: boolean }) {
  const version = useApiVersion();
  const light = dot && (
    <i
      aria-hidden
      className={`mr-2 inline-block size-[7px] rounded-full ${
        version.isError ? "bg-red-11" : version.isPending ? "bg-sand-8" : "bg-grass-11"
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
