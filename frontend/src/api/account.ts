// TanStack Query hooks for the user's own account: sign-up, profile, password, sessions.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import { forgetSignedInUser, meQueryKey } from "./queries";
import type { components } from "./schema";

export type RegisterRequest = components["schemas"]["RegisterRequest"];
export type ChangePasswordRequest = components["schemas"]["ChangePasswordRequest"];

/** Whether self-registration is open: `{ open: boolean }`. */
export function useRegistration() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["registration"],
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/v1/auth/registration");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

/** Sign up; on success the new user is signed in. */
export function useRegister() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: RegisterRequest) => {
      const { data, error, response } = await client.POST("/api/v1/auth/register", { body });
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

export function useUpdateDisplayName() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (displayName: string) => {
      const { data, error, response } = await client.PATCH("/api/v1/me", {
        body: { display_name: displayName },
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

/** Change the password; the API ends every other session of the user. */
export function useChangePassword() {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (body: ChangePasswordRequest) => {
      const { error, response } = await client.POST("/api/v1/auth/password/change", { body });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
  });
}

/**
 * End every session of the user, this one included. `onSignedOut` runs before the
 * signed-in state is dropped (e.g. to leave a signed-in page first), even if the
 * component that asked is gone by then.
 */
export function useSignOutEverywhere({ onSignedOut }: { onSignedOut?: () => void } = {}) {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.POST("/api/v1/auth/logout-all");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => {
      onSignedOut?.();
      forgetSignedInUser(queryClient);
    },
  });
}
