import type { NextRequest } from "next/server";

/**
 * SSE 프록시 — 클라이언트가 백엔드를 직접 호출하지 않고, 본 Route Handler를 경유한다.
 * 들어온 Cookie 헤더를 그대로 백엔드에 forward (httpOnly access_token 자동 포함).
 * 백엔드가 401을 내면 그대로 401을 전달해 클라이언트의 api-client가 refresh 인터셉터를 발동시킨다.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

export async function POST(req: NextRequest) {
  const baseUrl = process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrl) {
    return new Response("API_BASE_URL is not configured", { status: 500 });
  }

  const body = await req.text();
  const cookie = req.headers.get("cookie") ?? "";

  // const upstream = await fetch(`${baseUrl.replace(/\/$/, "")}/api/query`, {
  const upstream = await fetch(`${baseUrl.replace(/\/$/, "")}/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Cookie: cookie,
    },
    body,
    cache: "no-store",
  });

  if (upstream.status === 401) {
    return new Response("Unauthorized", { status: 401 });
  }

  if (!upstream.ok || !upstream.body) {
    return new Response(`Upstream error: ${upstream.status}`, { status: 502 });
  }

  return new Response(upstream.body, {
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache, no-transform",
      Connection: "keep-alive",
      // Nginx/Vercel 등 프록시의 응답 버퍼링 방지 — 토큰이 즉시 흐르도록.
      "X-Accel-Buffering": "no",
    },
  });
}
