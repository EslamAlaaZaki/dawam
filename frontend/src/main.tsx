import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "./App";
import { createApiClient } from "./api/client";
import { ApiClientContext } from "./api/context";
import "./index.css";

const apiClient = createApiClient();
const queryClient = new QueryClient();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ApiClientContext.Provider value={apiClient}>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ApiClientContext.Provider>
  </StrictMode>,
);
