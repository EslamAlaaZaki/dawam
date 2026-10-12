"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setNonce } from "get-nonce";
import { ThemeProvider } from "next-themes";
import { useState, type ReactNode } from "react";

import { createApiClient } from "../api/client";
import { ApiClientContext } from "../api/context";
import { Toaster } from "../components/ui/sonner";

/**
 * The API client and the query cache, one per browser tab; the theme (light, dark or
 * the system's, stored in the browser) and the Toasts.
 *
 * `nonce` is the request's Content-Security-Policy nonce (see src/proxy.ts): it lets
 * the theme's inline script run before the page paints, and the `<style>` the dialogs
 * and menus add while open (through get-nonce) apply.
 *
 * The theme leaves <html>'s `color-scheme` alone: pages not redesigned yet are light
 * only, so dark form controls there would be wrong. Redesigned parts set it themselves.
 */
export function Providers({ nonce, children }: { nonce?: string; children: ReactNode }) {
  const [apiClient] = useState(() => createApiClient());
  const [queryClient] = useState(() => new QueryClient());
  if (nonce) {
    setNonce(nonce);
  }
  return (
    <ApiClientContext.Provider value={apiClient}>
      <QueryClientProvider client={queryClient}>
        <ThemeProvider
          attribute="class"
          defaultTheme="system"
          enableSystem
          enableColorScheme={false}
          nonce={nonce}
        >
          {children}
          <Toaster />
        </ThemeProvider>
      </QueryClientProvider>
    </ApiClientContext.Provider>
  );
}
