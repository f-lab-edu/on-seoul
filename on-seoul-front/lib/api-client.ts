/**
 * 모든 브라우저 측 API 호출의 단일 진입점.
 * - httpOnly 쿠키 기반 인증이므로 항상 `credentials: 'include'`.
 * - 401 응답은 `/auth/refresh`를 single-flight로 1회 시도 후 원요청을 재시도한다.
 * - refresh 자체가 401이면 `auth:logout` 이벤트를 발행하고 즉시 실패.
 *
 * CLAUDE.md A-2/A-4 규칙을 만족한다.
 */

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, message: string, body: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

export const AUTH_LOGOUT_EVENT = "auth:logout";

/**
 * 환경변수 누락은 빌드/런타임 즉시 가시화한다.
 * 호출 시점마다 평가해 테스트에서 환경 주입을 허용.
 */
function getBaseUrl(): string {
  const url = process.env.NEXT_PUBLIC_API_BASE_URL;
  if (!url) {
    throw new Error("NEXT_PUBLIC_API_BASE_URL is not configured");
  }
  return url.replace(/\/$/, "");
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  /** JSON 직렬화할 본문. 미설정 시 본문 없음. */
  json?: unknown;
  /** 401 재시도 후 다시 들어온 요청을 식별. 외부에서 사용 금지. */
  _retry?: boolean;
}

let refreshInFlight: Promise<void> | null = null;

/**
 * 동시에 발생한 401들이 refresh를 1회만 호출하도록 single-flight로 묶는다.
 * refresh 자체가 401이면 즉시 logout 이벤트 발행 + 에러 throw.
 */
function refreshOnce(): Promise<void> {
  if (refreshInFlight) return refreshInFlight;

  refreshInFlight = (async () => {
    const res = await fetch(`${getBaseUrl()}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) {
      if (typeof window !== "undefined") {
        window.dispatchEvent(new Event(AUTH_LOGOUT_EVENT));
      }
      throw new ApiError(res.status, "Session refresh failed", null);
    }
  })().finally(() => {
    refreshInFlight = null;
  });

  return refreshInFlight;
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

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { json, _retry, headers, ...rest } = options;

  const init: RequestInit = {
    ...rest,
    credentials: "include",
    headers: {
      ...(json !== undefined ? { "Content-Type": "application/json" } : {}),
      ...(headers ?? {}),
    },
    body: json !== undefined ? JSON.stringify(json) : undefined,
  };

  const url = path.startsWith("http") ? path : `${getBaseUrl()}${path}`;
  const res = await fetch(url, init);

  if (res.status === 401) {
    // refresh 요청 자체가 401이거나 이미 재시도한 요청이면 즉시 실패.
    if (_retry || path === "/auth/refresh") {
      throw new ApiError(401, "Unauthorized", await safeBody(res));
    }
    await refreshOnce();
    return request<T>(path, { ...options, _retry: true });
  }

  if (!res.ok) {
    const body = await safeBody(res);
    throw new ApiError(res.status, `Request failed: ${res.status}`, body);
  }

  const body = await parseBody(res);
  return body as T;
}

async function safeBody(res: Response): Promise<unknown> {
  try {
    return await parseBody(res);
  } catch {
    return null;
  }
}

export const apiClient = {
  get<T>(path: string, options?: Omit<RequestOptions, "json" | "_retry">): Promise<T> {
    return request<T>(path, { ...options, method: "GET" });
  },
  post<T>(
    path: string,
    json?: unknown,
    options?: Omit<RequestOptions, "json" | "_retry">,
  ): Promise<T> {
    return request<T>(path, { ...options, method: "POST", json });
  },
  delete<T = void>(
    path: string,
    options?: Omit<RequestOptions, "json" | "_retry">,
  ): Promise<T> {
    return request<T>(path, { ...options, method: "DELETE" });
  },
};

/** 테스트 전용: single-flight 상태 초기화. */
export function __resetApiClientForTests(): void {
  refreshInFlight = null;
}
