import { useMutation } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";

/** Ask for a reset link. The API answers the same whether or not the email has an account. */
export function useForgotPassword() {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (email: string) => {
      const { error, response } = await client.POST("/api/v1/auth/password/forgot", {
        body: { email },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
  });
}

/** Set a new password with the token from a reset link. */
export function useResetPassword() {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (body: { token: string; password: string }) => {
      const { error, response } = await client.POST("/api/v1/auth/password/reset", { body });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
  });
}
