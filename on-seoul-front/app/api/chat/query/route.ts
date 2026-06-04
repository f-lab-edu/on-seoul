import type { NextRequest } from "next/server";
import { z } from "zod";

/**
 * SSE 프록시 — 클라이언트가 백엔드를 직접 호출하지 않고, 본 Route Handler를 경유한다.
 * 들어온 Cookie 헤더를 그대로 백엔드에 forward (httpOnly access_token 자동 포함).
 * 백엔드가 401을 내면 그대로 401을 전달해 클라이언트의 api-client가 refresh 인터셉터를 발동시킨다.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const QueryBodySchema = z.object({
  question: z.string().min(1),
  roomId: z.number().int().positive().optional(),
  // 지도(MAP) 의도용 좌표. 추후 위치 기반 검색 UI 구현 시 클라이언트가 채워 보낸다.
  lat: z.number().optional(),
  lng: z.number().optional(),
});

/** 업스트림 비정상 상태코드를 사용자 친화 한국어 메시지로 매핑. */
function upstreamErrorMessage(status: number): string {
  // 429 CHAT_CONCURRENCY_LIMIT — 동시 생성 cap 초과.
  if (status === 429) {
    return "지금은 처리 중인 대화가 많아요. 잠시 후 다시 시도해 주세요.";
  }
  return "일시적인 오류가 발생했어요. 잠시 후 다시 시도해 주세요.";
}

/** SSE `event: error` 단일 이벤트를 담은 ReadableStream을 반환한다. */
function sseErrorStream(message: string): ReadableStream<Uint8Array> {
  const payload = JSON.stringify({ type: "error", message });
  const chunk = `event: error\ndata: ${payload}\n\n`;
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(chunk));
      controller.close();
    },
  });
}

export async function POST(req: NextRequest) {
  const baseUrl = process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!baseUrl) {
    return new Response("API_BASE_URL is not configured", { status: 500 });
  }

  // 요청 본문 검증 (zod).
  let body: string;
  try {
    const json: unknown = await req.json();
    const parsed = QueryBodySchema.parse(json);
    body = JSON.stringify(parsed);
  } catch {
    return new Response("Invalid request body", { status: 400 });
  }

  const cookie = req.headers.get("cookie") ?? "";

  // req.signal — 클라이언트가 disconnect 하면 upstream fetch도 함께 취소된다.
  const upstream = await fetch(`${baseUrl.replace(/\/$/, "")}/api/chat/query`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
      Cookie: cookie,
    },
    body,
    cache: "no-store",
    signal: req.signal,
  }).catch((err: unknown) => {
    // AbortError는 클라이언트 disconnect이므로 조용히 null 반환.
    if (err instanceof Error && err.name === "AbortError") return null;
    throw err;
  });

  // 클라이언트가 이미 끊어진 경우.
  if (upstream === null) return new Response(null, { status: 499 });

  if (upstream.status === 401) {
    return new Response("Unauthorized", { status: 401 });
  }

  if (!upstream.ok || !upstream.body) {
    // 백엔드 4xx(429 동시성)/5xx/타임아웃 — SSE error 이벤트 1건 emit 후 스트림 종료 (절대규칙 B.4).
    return new Response(sseErrorStream(upstreamErrorMessage(upstream.status)), {
      headers: {
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache, no-transform",
        Connection: "keep-alive",
        "X-Accel-Buffering": "no",
      },
    });
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
