"use client";

import { useAuth } from "@/hooks/useAuth";

function FullScreen({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="flex flex-1 flex-col items-center justify-center gap-3 text-sm text-muted-foreground"
    >
      <span
        aria-hidden="true"
        className="size-6 rounded-full border-2 border-muted-foreground/30 border-t-foreground motion-safe:animate-spin"
      />
      {label}
    </div>
  );
}

/**
 * 클라이언트 인증 게이트.
 * 서버 컴포넌트 레이아웃에서는 쿠키를 회전(refresh)시킬 수 없어 SSR 가드가 silent refresh를
 * 가로채므로(서버 가드 제거됨), 인증 판단을 클라이언트로 위임한다. 401은 api-client가
 * /auth/token/refresh로 1회 갱신 후 재시도하고, 실패 시 auth:logout → /login으로 이동한다.
 *
 * - 로딩 중: 보호 콘텐츠를 렌더하지 않고 스피너만 노출(미인증 사용자에게 콘텐츠 깜빡임 방지).
 * - 미인증 확정(user 없음 & 일시 장애 아님): 리다이렉트 진행 중이므로 스피너 유지.
 * - 일시 장애(5xx/네트워크)는 미인증으로 단정하지 않고 그대로 렌더 — 각 페이지가 개별 처리.
 */
export function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, error } = useAuth();

  if (loading) return <FullScreen label="불러오는 중…" />;
  if (!user && !error) return <FullScreen label="로그인 페이지로 이동 중…" />;
  return <>{children}</>;
}
