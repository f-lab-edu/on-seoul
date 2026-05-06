"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, AUTH_LOGOUT_EVENT, apiClient } from "@/lib/api-client";
import type { User } from "@/types/auth";

interface UseAuthResult {
  user: User | null;
  loading: boolean;
  error: string | null;
  logout: () => Promise<void>;
}

/**
 * 현재 사용자 정보 조회 + 로그아웃을 묶은 클라이언트 훅.
 * `auth:logout` 이벤트를 구독해 다른 호출에서 발생한 세션 만료에도 반응한다.
 *
 * 401(인증 만료)과 5xx/네트워크 일시 장애를 구분한다.
 * - 401만 `user = null` 처리(비로그인 상태 확정)
 * - 그 외 오류는 `user`를 변경하지 않고 `error` 메시지로 노출해 재시도 UX를 가능하게 한다.
 */
export function useAuth(): UseAuthResult {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const me = await apiClient.get<User>("/auth/me");
        if (cancelled) return;
        setUser(me);
        setError(null);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) {
          setUser(null);
          setError(null);
        } else {
          // 5xx/네트워크 장애는 비로그인으로 단정하지 않는다. 사용자 상태는 그대로 두고 에러만 노출.
          const message = e instanceof Error ? e.message : "사용자 정보를 불러오지 못했습니다.";
          setError(message);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    const onLogout = () => {
      setUser(null);
      setError(null);
      router.push("/login");
    };
    window.addEventListener(AUTH_LOGOUT_EVENT, onLogout);
    return () => {
      cancelled = true;
      window.removeEventListener(AUTH_LOGOUT_EVENT, onLogout);
    };
  }, [router]);

  const logout = useCallback(async () => {
    try {
      await apiClient.post("/auth/logout");
    } catch {
      // 서버 실패해도 클라이언트 상태는 정리해 사용자 흐름을 막지 않는다.
    }
    setUser(null);
    setError(null);
    router.push("/login");
  }, [router]);

  return { user, loading, error, logout };
}
