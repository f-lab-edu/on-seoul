import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  AUTH_LOGOUT_EVENT,
  __resetApiClientForTests,
  apiClient,
} from "./api-client";

const BASE = "https://api.test.local";

type FetchCall = { url: string; init: RequestInit | undefined };

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

let calls: FetchCall[];

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = BASE;
  calls = [];
  __resetApiClientForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiClient", () => {
  it("정상 GET 요청은 응답 JSON을 반환한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        return jsonResponse(200, { id: 1, nickname: "vito" });
      }),
    );

    const data = await apiClient.get<{ id: number; nickname: string }>("/auth/me");

    expect(data).toEqual({ id: 1, nickname: "vito" });
    expect(calls).toHaveLength(1);
    expect(calls[0].url).toBe(`${BASE}/auth/me`);
    expect(calls[0].init?.credentials).toBe("include");
  });

  it("401 발생 시 refresh 후 원요청을 1회 재시도한다", async () => {
    let firstMe = true;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (url.endsWith("/auth/me")) {
          if (firstMe) {
            firstMe = false;
            return new Response(null, { status: 401 });
          }
          return jsonResponse(200, { id: 1 });
        }
        if (url.endsWith("/auth/refresh")) {
          return new Response(null, { status: 204 });
        }
        return new Response(null, { status: 500 });
      }),
    );

    const data = await apiClient.get<{ id: number }>("/auth/me");

    expect(data).toEqual({ id: 1 });
    const refreshCalls = calls.filter((c) => c.url.endsWith("/auth/refresh"));
    expect(refreshCalls).toHaveLength(1);
    const meCalls = calls.filter((c) => c.url.endsWith("/auth/me"));
    expect(meCalls).toHaveLength(2);
  });

  it("동시 401 5건이 와도 refresh는 정확히 1번만 호출된다", async () => {
    const failedFirst = new Set<string>();
    let refreshCount = 0;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init?: RequestInit) => {
        calls.push({ url, init });
        if (url.endsWith("/auth/refresh")) {
          refreshCount += 1;
          // refresh를 약간 지연시켜 single-flight를 검증.
          await new Promise((r) => setTimeout(r, 10));
          return new Response(null, { status: 204 });
        }
        const key = url;
        if (!failedFirst.has(key)) {
          failedFirst.add(key);
          return new Response(null, { status: 401 });
        }
        return jsonResponse(200, { url });
      }),
    );

    const results = await Promise.all([
      apiClient.get("/a"),
      apiClient.get("/b"),
      apiClient.get("/c"),
      apiClient.get("/d"),
      apiClient.get("/e"),
    ]);

    expect(results).toHaveLength(5);
    expect(refreshCount).toBe(1);
  });

  it("refresh 자체가 401이면 auth:logout 이벤트를 발행하고 에러를 throw한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/auth/refresh")) {
          return new Response(null, { status: 401 });
        }
        return new Response(null, { status: 401 });
      }),
    );

    const listener = vi.fn();
    window.addEventListener(AUTH_LOGOUT_EVENT, listener);

    await expect(apiClient.get("/auth/me")).rejects.toBeInstanceOf(ApiError);
    expect(listener).toHaveBeenCalledTimes(1);

    window.removeEventListener(AUTH_LOGOUT_EVENT, listener);
  });

  it("refresh 요청에는 _retry 가드가 적용되어 무한루프를 차단한다", async () => {
    let refreshCount = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/auth/refresh")) {
          refreshCount += 1;
          return new Response(null, { status: 401 });
        }
        return new Response(null, { status: 401 });
      }),
    );

    await expect(apiClient.post("/auth/refresh")).rejects.toBeInstanceOf(ApiError);
    expect(refreshCount).toBe(1);
  });

  it("절대 URL(http://...)은 거부한다 — 외부 오리진에 쿠키 노출 방지", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(apiClient.get("https://evil.example.com/steal")).rejects.toThrow(
      /must start with "\/"/,
    );
    await expect(apiClient.get("http://evil.example.com/steal")).rejects.toThrow(
      /must start with "\/"/,
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("DELETE 204 응답은 undefined를 반환한다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(null, { status: 204 })),
    );

    const result = await apiClient.delete("/items/1");
    expect(result).toBeUndefined();
  });
});
