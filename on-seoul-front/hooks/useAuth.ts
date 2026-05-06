"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AUTH_LOGOUT_EVENT, apiClient } from "@/lib/api-client";
import type { User } from "@/types/auth";

interface UseAuthResult {
  user: User | null;
  loading: boolean;
  logout: () => Promise<void>;
}

/**
 * 현재 사용자 정보 조회 + 로그아웃을 묶은 클라이언트 훅.
 * `auth:logout` 이벤트를 구독해 다른 호출에서 발생한 세션 만료에도 반응한다.
 */
export function useAuth(): UseAuthResult {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const me = await apiClient.get<User>("/auth/me");
        if (!cancelled) setUser(me);
      } catch {
        if (!cancelled) setUser(null);
        // 401은 api-client가 auth:logout 이벤트로 처리하므로 여기서는 조용히 무시.
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    const onLogout = () => {
      setUser(null);
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
    router.push("/login");
  }, [router]);

  return { user, loading, logout };
}
