"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiError, AUTH_LOGOUT_EVENT, apiClient } from "@/lib/api-client";
import type { User } from "@/types/auth";

interface UseAuthResult {
  user: User | null;
  loading: boolean;
  /** 사용자 정보 조회 실패 여부. 사용자에게 노출할 메시지는 호출 측에서 결정한다. */
  error: boolean;
  logout: () => Promise<void>;
}

/**
 * 현재 사용자 정보 조회 + 로그아웃을 묶은 클라이언트 훅.
 * `auth:logout` 이벤트를 구독해 다른 호출에서 발생한 세션 만료에도 반응한다.
 *
 * 401(인증 만료)과 5xx/네트워크 일시 장애를 구분한다.
 * - 401만 `user = null` 처리(비로그인 상태 확정)
 * - 그 외 오류는 `user`를 변경하지 않고 `error = true`만 노출 — 메시지는 호출 측이 한글로 결정.
 *   (raw `Error.message`는 백엔드 영문 메시지일 수 있어 사용자 노출에 부적합)
 */
export function useAuth(): UseAuthResult {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<boolean>(false);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const me = await apiClient.get<User>("/auth/me");
        if (cancelled) return;
        setUser(me);
        setError(false);
      } catch (e) {
        if (cancelled) return;
        if (e instanceof ApiError && e.status === 401) {
          setUser(null);
          setError(false);
        } else {
          // 5xx/네트워크 장애는 비로그인으로 단정하지 않는다. 사용자 상태는 그대로 두고 에러 플래그만 세움.
          setError(true);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    const onLogout = () => {
      setUser(null);
      setError(false);
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
    setError(false);
    router.push("/login");
  }, [router]);

  return { user, loading, error, logout };
}
