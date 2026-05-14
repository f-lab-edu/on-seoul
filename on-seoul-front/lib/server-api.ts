/**
 * 서버 컴포넌트/Route Handler 전용 API 헬퍼.
 * 브라우저 측 호출은 `lib/api-client.ts`를 사용한다.
 *
 * - baseUrl 결정: 서버 전용 `API_BASE_URL` 우선, 없으면 `NEXT_PUBLIC_API_BASE_URL` fallback (로컬/Vercel 단일 도메인 케이스).
 * - cookie forward: 호출 측이 Next.js `cookies()`로 추출한 헤더를 그대로 넘긴다.
 * - 401: `UnauthorizedError`로 throw 하여 호출 측이 `redirect("/login")`을 결정.
 */

export class UnauthorizedError extends Error {
  constructor(message = "Unauthorized") {
    super(message);
    this.name = "UnauthorizedError";
  }
}

export class ServerApiError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "ServerApiError";
    this.status = status;
  }
}

export function getServerBaseUrl(): string {
  const url = process.env.API_BASE_URL ?? process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!url) {
    throw new Error("API_BASE_URL or NEXT_PUBLIC_API_BASE_URL is not configured");
  }
  return url.replace(/\/$/, "");
}

async function parseBody(res: Response): Promise<unknown> {
  if (res.status === 204) return undefined;
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) {
    try {
      return (await res.json()) as unknown;
    } catch {
      return null;
    }
  }
  const text = await res.text();
  return text.length > 0 ? text : null;
}

/**
 * 서버에서 백엔드로 GET. cookieHeader는 호출 측이 결정 (어떤 쿠키를 forward 할지).
 * 401 → UnauthorizedError. 그 외 비정상 → ServerApiError.
 */
export async function serverGet<T>(
  path: string,
  cookieHeader: string,
): Promise<T> {
  if (!path.startsWith("/")) {
    throw new Error(`serverGet path must start with "/", got: ${path}`);
  }

  const res = await fetch(`${getServerBaseUrl()}${path}`, {
    method: "GET",
    headers: cookieHeader ? { cookie: cookieHeader } : undefined,
    cache: "no-store",
  });

  if (res.status === 401 || res.status === 403) {
    throw new UnauthorizedError();
  }
  if (!res.ok) {
    throw new ServerApiError(res.status, `Request failed: ${res.status}`);
  }

  const body = await parseBody(res);
  return body as T;
}
