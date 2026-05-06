import { cookies } from "next/headers";
import { redirect } from "next/navigation";

/**
 * 챗봇 영역 인증 가드.
 * 서버에서 `/auth/me`를 호출해 401이면 즉시 /login으로 보낸다.
 * 쿠키는 Next.js cookies() API로 forward — 클라이언트가 토큰을 보유하지 않는 규약을 유지한다.
 */
async function requireUser(): Promise<void> {
  const baseUrl = process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrl) {
    throw new Error("API_BASE_URL or NEXT_PUBLIC_API_BASE_URL is not configured");
  }

  const jar = await cookies();
  const access = jar.get("access_token");
  // refresh_token은 Path=/auth 전용 — /auth/me 호출에 포함하지 않는다.
  const cookieHeader = access ? `access_token=${access.value}` : "";

  const res = await fetch(`${baseUrl.replace(/\/$/, "")}/auth/me`, {
    method: "GET",
    headers: cookieHeader ? { cookie: cookieHeader } : undefined,
    cache: "no-store",
  });

  if (res.status === 401 || res.status === 403) {
    redirect("/login");
  }
  if (!res.ok) {
    throw new Error(`Auth check failed: ${res.status}`);
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
