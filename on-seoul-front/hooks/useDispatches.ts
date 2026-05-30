"use client";

import {
  useInfiniteQuery,
  type UseInfiniteQueryResult,
  type InfiniteData,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type { DispatchesResponse } from "@/types/notification";

const DISPATCHES_KEY = ["notifications", "dispatches"] as const;
const PAGE_SIZE = 20;

export function useDispatches(): UseInfiniteQueryResult<
  InfiniteData<DispatchesResponse>,
  Error
> {
  return useInfiniteQuery<
    DispatchesResponse,
    Error,
    InfiniteData<DispatchesResponse>,
    typeof DISPATCHES_KEY,
    number | null
  >({
    queryKey: DISPATCHES_KEY,
    initialPageParam: null,
    queryFn: async ({ pageParam }) => {
      const params = new URLSearchParams({ size: String(PAGE_SIZE) });
      if (pageParam !== null) params.set("cursor", String(pageParam));
      return apiClient.get<DispatchesResponse>(
        `/api/notifications/dispatches?${params.toString()}`,
      );
    },
    getNextPageParam: (last) => last.nextCursor ?? undefined,
    staleTime: 30_000,
  });
}
