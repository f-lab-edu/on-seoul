import { AppHeader } from "@/components/layout/app-header";
import { AuthGate } from "@/components/layout/auth-gate";

/**
 * 챗봇 영역 셸. 인증 판단은 클라이언트 AuthGate에 위임한다.
 *
 * 서버 컴포넌트(layout)는 렌더 중 쿠키를 쓸 수 없어 토큰 회전(refresh)을 처리할 수 없다.
 * 따라서 과거의 서버 가드(`/auth/me` 401 → redirect)는 silent refresh를 가로채
 * 만료된 access_token + 유효한 refresh_token 사용자를 무조건 /login으로 보냈다.
 * refresh는 /auth 경로로 직접 호출하는 클라이언트(api-client)에서만 가능하므로,
 * 인증을 AuthGate + useAuth로 일원화한다(CLAUDE.md A.4).
 */
export default function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <div className="mx-auto flex h-dvh max-w-3xl flex-col">
      <AppHeader />
      <div className="flex flex-1 flex-col overflow-hidden">
        <AuthGate>{children}</AuthGate>
      </div>
    </div>
  );
}
