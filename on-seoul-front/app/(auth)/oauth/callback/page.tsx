"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";

/**
 * 백엔드가 Set-Cookie로 토큰을 발급하므로 프론트는 라우팅만 책임진다.
 * status=success → 홈, error=forbidden → /login?error=forbidden.
 */
export default function OAuthCallbackPage() {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    const status = params.get("status");
    const error = params.get("error");

    if (status === "success") {
      router.replace("/");
      return;
    }
    if (error === "forbidden") {
      router.replace("/login?error=forbidden");
      return;
    }
    // 알 수 없는 케이스는 로그인으로 회귀.
    router.replace("/login");
  }, [params, router]);

  return (
    <main className="flex min-h-screen items-center justify-center">
      <div
        role="status"
        aria-live="polite"
        className="flex flex-col items-center gap-3 text-sm text-muted-foreground"
      >
        <span
          aria-hidden="true"
          className="size-6 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-foreground"
        />
        로그인 처리 중…
      </div>
    </main>
  );
}
