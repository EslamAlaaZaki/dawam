"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";

import { createApiClient } from "../api/client";
import { ApiClientContext } from "../api/context";

/** The API client and the query cache, one per browser tab. */
export function Providers({ children }: { children: ReactNode }) {
  const [apiClient] = useState(() => createApiClient());
  const [queryClient] = useState(() => new QueryClient());
  return (
    <ApiClientContext.Provider value={apiClient}>
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    </ApiClientContext.Provider>
  );
}
