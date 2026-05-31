"use client";

import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseMutationResult,
  type UseQueryResult,
} from "@tanstack/react-query";

import { apiClient } from "@/lib/api-client";
import type {
  CreateSubscriptionRequest,
  Subscription,
  SubscriptionsResponse,
  UpdateSubscriptionRequest,
} from "@/types/notification";

const SUBSCRIPTIONS_KEY = ["notifications", "subscriptions"] as const;

/**
 * v1 제약: 모든 mutation 페이로드의 `channels`는 ["EMAIL"]로 강제.
 * channels 존재 여부와 무관하게 항상 EMAIL로 고정하여 SMS 통과를 차단한다.
 * 명세 §1 Warning / §8.
 */
function enforceEmailOnly<T extends { channels?: string[] }>(payload: T): T {
  return { ...payload, channels: ["EMAIL"] };
}

export function useSubscriptions(): UseQueryResult<Subscription[]> {
  return useQuery({
    queryKey: SUBSCRIPTIONS_KEY,
    queryFn: async () => {
      const res = await apiClient.get<SubscriptionsResponse>(
        "/api/notifications/subscriptions",
      );
      return res.subscriptions;
    },
    staleTime: 30_000,
  });
}

export function useCreateSubscription(): UseMutationResult<
  Subscription,
  Error,
  CreateSubscriptionRequest
> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: CreateSubscriptionRequest) =>
      apiClient.post<Subscription>(
        "/api/notifications/subscriptions",
        enforceEmailOnly(payload),
      ),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SUBSCRIPTIONS_KEY });
    },
  });
}

interface UpdateContext {
  previous: Subscription[] | undefined;
}

export function useUpdateSubscription(
  id: number,
): UseMutationResult<Subscription, Error, UpdateSubscriptionRequest, UpdateContext> {
  const qc = useQueryClient();
  return useMutation<Subscription, Error, UpdateSubscriptionRequest, UpdateContext>({
    mutationFn: (payload: UpdateSubscriptionRequest) =>
      apiClient.patch<Subscription>(
        `/api/notifications/subscriptions/${id}`,
        enforceEmailOnly(payload),
      ),
    onMutate: async (payload) => {
      await qc.cancelQueries({ queryKey: SUBSCRIPTIONS_KEY });
      const previous = qc.getQueryData<Subscription[]>(SUBSCRIPTIONS_KEY);
      if (previous) {
        const safe = enforceEmailOnly(payload);
        qc.setQueryData<Subscription[]>(
          SUBSCRIPTIONS_KEY,
          previous.map((sub) =>
            sub.id === id
              ? {
                  ...sub,
                  filter: safe.filter ?? sub.filter,
                  channels: safe.channels ?? sub.channels,
                }
              : sub,
          ),
        );
      }
      return { previous };
    },
    onError: (_err, _vars, ctx) => {
      if (ctx?.previous) {
        qc.setQueryData(SUBSCRIPTIONS_KEY, ctx.previous);
      }
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: SUBSCRIPTIONS_KEY });
    },
  });
}

export function useDeleteSubscription(
  id: number,
): UseMutationResult<void, Error, void> {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      apiClient.delete<void>(`/api/notifications/subscriptions/${id}`),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: SUBSCRIPTIONS_KEY });
    },
  });
}
