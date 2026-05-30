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
  /** 로그아웃 진행 중 여부. 버튼 disable + 진행 상태 표시에 사용. */
  loggingOut: boolean;
  logout: () => Promise<void>;
}

/**
 * 모듈 전역 캐시.
 * 같은 페이지에서 `useAuth`가 여러 컴포넌트에 마운트되어도 `/auth/me`가
 * 1회만 호출되도록 in-flight Promise를 공유한다. 401(=비로그인 확정)은
 * `cachedUser = null`로 캐싱하고, 5xx/네트워크 장애는 캐싱하지 않아 재시도가 가능하다.
 */
let cachedUser: User | null = null;
let cacheLoaded = false;
let mePromise: Promise<User | null> | null = null;

type MeError = { kind: "unauthorized" } | { kind: "transient" };

function fetchMe(): Promise<User | null> {
  if (mePromise) return mePromise;
  mePromise = apiClient
    .get<User>("/auth/me")
    .then((u) => {
      cachedUser = u;
      cacheLoaded = true;
      return u;
    })
    .catch((e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        cachedUser = null;
        cacheLoaded = true;
        const err: MeError = { kind: "unauthorized" };
        throw err;
      }
      // 5xx/네트워크 장애는 캐싱하지 않아 다음 마운트에서 재시도된다.
      const err: MeError = { kind: "transient" };
      throw err;
    })
    .finally(() => {
      mePromise = null;
    });
  return mePromise;
}

function invalidateMeCache(): void {
  cachedUser = null;
  cacheLoaded = false;
  mePromise = null;
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
  const [user, setUser] = useState<User | null>(cacheLoaded ? cachedUser : null);
  const [loading, setLoading] = useState(!cacheLoaded);
  const [error, setError] = useState<boolean>(false);
  const [loggingOut, setLoggingOut] = useState(false);

  useEffect(() => {
    let cancelled = false;

    if (cacheLoaded) {
      // 이미 다른 마운트에서 로드 완료. 추가 fetch 없이 캐시 사용.
      setUser(cachedUser);
      setError(false);
      setLoading(false);
    } else {
      (async () => {
        try {
          const me = await fetchMe();
          if (cancelled) return;
          setUser(me);
          setError(false);
        } catch (e) {
          if (cancelled) return;
          const err = e as MeError;
          if (err?.kind === "unauthorized") {
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
    }

    const onLogout = () => {
      invalidateMeCache();
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
    setLoggingOut(true);
    try {
      await apiClient.post("/auth/logout");
    } catch {
      // 서버 실패해도 클라이언트 상태는 정리해 사용자 흐름을 막지 않는다.
    } finally {
      invalidateMeCache();
      setUser(null);
      setError(false);
      setLoggingOut(false);
      router.push("/login");
    }
  }, [router]);

  return { user, loading, error, loggingOut, logout };
}

/** 테스트 전용: 모듈 전역 캐시 초기화. */
export function __resetUseAuthCacheForTests(): void {
  invalidateMeCache();
}
