// Admin: SMTP settings, the test email, and the links DAWAM could not email.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";
import type { components } from "./schema";

export type SmtpSettings = components["schemas"]["SmtpSettingsOut"];
export type SmtpSettingsInput = components["schemas"]["SmtpSettingsIn"];
export type UndeliveredLink = components["schemas"]["UndeliveredLinkOut"];

const smtpKey = ["admin", "smtp"] as const;
const linksKey = ["admin", "undelivered-links"] as const;

/** The saved SMTP settings, or `null` while SMTP is off. */
export function useSmtpSettings() {
  const client = useApiClient();
  return useQuery({
    queryKey: smtpKey,
    queryFn: async (): Promise<SmtpSettings | null> => {
      const { data, error, response } = await client.GET("/api/v1/admin/smtp");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data ?? null;
    },
  });
}

export function useSaveSmtpSettings() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (body: SmtpSettingsInput) => {
      const { data, error, response } = await client.PUT("/api/v1/admin/smtp", { body });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
    onSuccess: (settings) => queryClient.setQueryData(smtpKey, settings),
  });
}

export function useClearSmtpSettings() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { error, response } = await client.DELETE("/api/v1/admin/smtp");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => queryClient.setQueryData(smtpKey, null),
  });
}

export function useSendTestEmail() {
  const client = useApiClient();
  return useMutation({
    mutationFn: async (to: string | null) => {
      const { data, error, response } = await client.POST("/api/v1/admin/smtp/test", {
        body: { to },
      });
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data;
    },
  });
}

export function useUndeliveredLinks() {
  const client = useApiClient();
  return useQuery({
    queryKey: linksKey,
    queryFn: async () => {
      const { data, error, response } = await client.GET("/api/v1/admin/undelivered-links");
      if (error) {
        throw new ApiError(response.status, error.error);
      }
      return data.items;
    },
  });
}

export function useDismissUndeliveredLink() {
  const client = useApiClient();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async (linkId: string) => {
      const { error, response } = await client.DELETE(
        "/api/v1/admin/undelivered-links/{link_id}",
        { params: { path: { link_id: linkId } } },
      );
      if (error) {
        throw new ApiError(response.status, error.error);
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: linksKey }),
  });
}
