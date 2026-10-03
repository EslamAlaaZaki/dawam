import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type Me = components["schemas"]["Me"];
export type SignInRequest = components["schemas"]["SignInRequest"];

export function useApiVersion() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["version"],
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/v1/version");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

const meQueryKey = ["me"] as const;

// Queries that hold nothing about the signed-in user and survive signing out.
const PUBLIC_QUERIES = new Set(["me", "version"]);

/** The signed-in user, or `null` when nobody is signed in (the API answers 401). */
export function useMe() {
  const client = useApiClient();
  return useQuery({
    queryKey: meQueryKey,
    queryFn: async (): Promise<Me | null> => {
      const { data, error, response } = await client.GET("/api/v1/me");
      if (response.status === 401) {
        return null;
      }
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

export function useSignIn() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (credentials: SignInRequest) => {
      const { data, error, response } = await client.POST("/api/v1/auth/login", {
        body: credentials,
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (me) => {
      queryClient.setQueryData(meQueryKey, me);
    },
  });
}

export function useSignOut() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.POST("/api/v1/auth/logout");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => {
      // Drop everything the previous user loaded, then show the signed-out state.
      queryClient.removeQueries({
        predicate: (query) => !PUBLIC_QUERIES.has(String(query.queryKey[0])),
      });
      queryClient.setQueryData(meQueryKey, null);
    },
  });
}
