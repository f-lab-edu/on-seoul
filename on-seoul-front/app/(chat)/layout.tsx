import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { UnauthorizedError, serverGet } from "@/lib/server-api";
import type { User } from "@/types/auth";

/**
 * 챗봇 영역 인증 가드.
 * 서버에서 `/auth/me`를 호출해 401이면 즉시 /login으로 보낸다.
 * 쿠키는 Next.js cookies() API로 forward — 클라이언트가 토큰을 보유하지 않는 규약을 유지한다.
 */
async function requireUser(): Promise<void> {
  const jar = await cookies();
  const access = jar.get("access_token");
  // refresh_token은 Path=/auth 전용 — /auth/me 호출에 포함하지 않는다.
  const cookieHeader = access ? `access_token=${access.value}` : "";

  try {
    await serverGet<User>("/auth/me", cookieHeader);
  } catch (e) {
    if (e instanceof UnauthorizedError) {
      redirect("/login");
    }
    throw e;
  }
}

export default async function ChatLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  await requireUser();
  return <>{children}</>;
}
