import { useQuery } from "@tanstack/react-query";

import { ApiError } from "./client";
import { useApiClient } from "./context";

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
