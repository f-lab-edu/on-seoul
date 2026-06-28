/**
 * useChatStream 훅 단위 테스트.
 * Mock fetch + ReadableStream으로 SSE 이벤트(init/final/workflow_error/error) 처리·취소를 검증한다.
 * 정본: docs/chat-sse-event-catalog.md
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { __resetApiClientForTests, AUTH_LOGOUT_EVENT } from "@/lib/api-client";

import { useChatStream } from "./useChatStream";

/** name 없는 data 이벤트들 (step/final/workflow_error). */
function dataFrames(events: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const e of events) {
        controller.enqueue(encoder.encode(`data: ${e}\n\n`));
      }
      controller.close();
    },
  });
}

/** 완성된 SSE 프레임 문자열들 (named 이벤트 포함). */
function rawFrames(frames: string[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream<Uint8Array>({
    start(controller) {
      for (const f of frames) {
        controller.enqueue(encoder.encode(`${f}\n\n`));
      }
      controller.close();
    },
  });
}

function pendingStream(): {
  stream: ReadableStream<Uint8Array>;
  push: (chunk: string) => void;
  close: () => void;
} {
  const encoder = new TextEncoder();
  let ctrl!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({
    start(c) {
      ctrl = c;
    },
  });
  return {
    stream,
    push: (chunk: string) => ctrl.enqueue(encoder.encode(`data: ${chunk}\n\n`)),
    close: () => ctrl.close(),
  };
}

beforeEach(() => {
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.test.local";
  __resetApiClientForTests();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useChatStream", () => {
  it("step 진행을 trace에 쌓고 final에서 phase가 'done'으로 전환된다", async () => {
    const events = [
      JSON.stringify({ step: "routing", message: "질문을 분석하고 있습니다..." }),
      JSON.stringify({
        message_id: 84,
        answer: "안녕하세요",
        intent: "FALLBACK",
        title: null,
        cache_hit: false,
        service_cards: [],
      }),
    ];

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(dataFrames(events), { status: 200 })),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send({ question: "hi" });
    });

    await waitFor(() => {
      expect(result.current.state.phase).toBe("done");
    });
    if (result.current.state.phase !== "done") throw new Error("expected done");
    expect(result.current.state.content).toBe("안녕하세요");
    expect(result.current.state.messageId).toBe(84);
    expect(result.current.state.trace).toContain("⏳ 질문을 분석하고 있습니다...");
  });

  it("init 이벤트 수신 시 onInit 콜백이 room_id/created로 호출된다", async () => {
    const frames = [
      `event:init\ndata:${JSON.stringify({ room_id: 42, created: true })}`,
      `data:${JSON.stringify({ message_id: 1, answer: "답", intent: null, title: "방", cache_hit: false, service_cards: [] })}`,
    ];

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(rawFrames(frames), { status: 200 })),
    );

    const onInit = vi.fn();
    const { result } = renderHook(() => useChatStream({ onInit }));
    await act(async () => {
      await result.current.send({ question: "hi" });
    });

    await waitFor(() => {
      expect(result.current.state.phase).toBe("done");
    });
    expect(onInit).toHaveBeenCalledWith({ roomId: 42, created: true });
  });

  it("title 이벤트(payload type)로 onTitle 콜백이 호출되고 진행으로 흡수되지 않는다", async () => {
    const frames = [
      `event:init\ndata:${JSON.stringify({ room_id: 7, created: true })}`,
      // name 없는 data + payload type=title (이름 비의존 식별 검증)
      `data:${JSON.stringify({ type: "title", room_id: 7, title: "도봉구 시설 안내", message_id: 9, query: "도봉구 시설" })}`,
      `data:${JSON.stringify({ message_id: 9, answer: "답변", intent: null, cache_hit: false, service_cards: [] })}`,
    ];

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(rawFrames(frames), { status: 200 })),
    );

    const onTitle = vi.fn();
    const { result } = renderHook(() => useChatStream({ onTitle }));
    await act(async () => {
      await result.current.send({ question: "hi" });
    });

    await waitFor(() => {
      expect(result.current.state.phase).toBe("done");
    });
    expect(onTitle).toHaveBeenCalledWith({
      roomId: 7,
      title: "도봉구 시설 안내",
      messageId: 9,
      query: "도봉구 시설",
    });
    // title은 trace(진행)로 새지 않는다.
    if (result.current.state.phase !== "done") throw new Error("expected done");
    expect(result.current.state.trace).toEqual([]);
  });

  it("init로 roomId를 받은 뒤 retry는 같은 방으로 재전송된다(방 중복 생성 방지)", async () => {
    const bodies: string[] = [];
    // init만 오고 final 없이 종료 → '스트림 미완료' error. retry가 같은 roomId를 실어야 한다.
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        bodies.push(typeof init?.body === "string" ? init.body : "");
        return new Response(
          rawFrames([`event:init\ndata:${JSON.stringify({ room_id: 7, created: true })}`]),
          { status: 200 },
        );
      }),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send({ question: "hi" });
    });
    await waitFor(() => {
      expect(result.current.state.phase).toBe("error");
    });

    await act(async () => {
      await result.current.retry();
    });

    expect(JSON.parse(bodies[0] ?? "{}").roomId).toBeUndefined();
    expect(JSON.parse(bodies[1] ?? "{}").roomId).toBe(7);
  });

  it("workflow_error(answer+error)는 answer를 메시지로 error 전환된다", async () => {
    const events = [
      JSON.stringify({ message_id: 5, answer: "처리 중 문제가 발생했습니다.", error: "boom", intent: "FALLBACK" }),
    ];

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(dataFrames(events), { status: 200 })),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send({ question: "hi" });
    });

    await waitFor(() => {
      expect(result.current.state.phase).toBe("error");
    });
    if (result.current.state.phase !== "error") throw new Error("expected error");
    expect(result.current.state.message).toBe("처리 중 문제가 발생했습니다.");
  });

  it("event:error(평문 문자열) 수신 시 phase가 'error'로 전환된다", async () => {
    const frames = [`event:error\ndata:일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.`];

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(rawFrames(frames), { status: 200 })),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send({ question: "hi" });
    });

    await waitFor(() => {
      expect(result.current.state.phase).toBe("error");
    });
    if (result.current.state.phase !== "error") throw new Error("expected error");
    expect(result.current.state.message).toBe("일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.");
  });

  it("cancel() 호출 시 fetch가 abort되고 조기 종료된다", async () => {
    const { stream, push, close } = pendingStream();
    let receivedSignal: AbortSignal | undefined;

    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init?: RequestInit) => {
        receivedSignal = init?.signal ?? undefined;
        return new Response(stream, { status: 200 });
      }),
    );

    const { result } = renderHook(() => useChatStream());

    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.send({ question: "hi" });
      await Promise.resolve();
    });

    push(JSON.stringify({ step: "routing", message: "분석 중" }));

    await act(async () => {
      result.current.cancel();
      try {
        close();
      } catch {
        // already closed
      }
      await sendPromise;
    });

    expect(receivedSignal?.aborted).toBe(true);
  });

  it("SSE 401 시 refresh 1회 후 같은 요청을 재시도해 done으로 이어진다", async () => {
    let chatCalls = 0;
    let refreshCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/auth/token/refresh")) {
          refreshCalls += 1;
          return new Response(null, { status: 204 });
        }
        chatCalls += 1;
        if (chatCalls === 1) return new Response("Unauthorized", { status: 401 });
        return new Response(
          dataFrames([
            JSON.stringify({
              message_id: 1,
              answer: "복구됨",
              intent: null,
              cache_hit: false,
              service_cards: [],
            }),
          ]),
          { status: 200 },
        );
      }),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send({ question: "hi" });
    });

    await waitFor(() => {
      expect(result.current.state.phase).toBe("done");
    });
    expect(refreshCalls).toBe(1);
    expect(chatCalls).toBe(2);
    if (result.current.state.phase !== "done") throw new Error("expected done");
    expect(result.current.state.content).toBe("복구됨");
  });

  it("SSE 401 후 refresh도 401이면 error로 전환되고 auth:logout이 발행된다(재시도 없음)", async () => {
    let chatCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/auth/token/refresh")) {
          return new Response(null, { status: 401 });
        }
        chatCalls += 1;
        return new Response("Unauthorized", { status: 401 });
      }),
    );

    const onLogout = vi.fn();
    window.addEventListener(AUTH_LOGOUT_EVENT, onLogout);
    try {
      const { result } = renderHook(() => useChatStream());
      await act(async () => {
        await result.current.send({ question: "hi" });
      });

      await waitFor(() => {
        expect(result.current.state.phase).toBe("error");
      });
      // refresh 실패 시 채팅 요청은 1회만(재시도 안 함).
      expect(chatCalls).toBe(1);
      // refreshOnce가 로그아웃을 발행한다.
      expect(onLogout).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(AUTH_LOGOUT_EVENT, onLogout);
    }
  });

  it("refresh(204) 후에도 재시도가 401이면 로그아웃 처리된다", async () => {
    let chatCalls = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("/auth/token/refresh")) {
          return new Response(null, { status: 204 });
        }
        chatCalls += 1;
        return new Response("Unauthorized", { status: 401 });
      }),
    );

    const onLogout = vi.fn();
    window.addEventListener(AUTH_LOGOUT_EVENT, onLogout);
    try {
      const { result } = renderHook(() => useChatStream());
      await act(async () => {
        await result.current.send({ question: "hi" });
      });

      await waitFor(() => {
        expect(result.current.state.phase).toBe("error");
      });
      // 최초 + 재시도 = 2회. refresh는 성공했지만 그래도 401이라 로그아웃.
      expect(chatCalls).toBe(2);
      expect(onLogout).toHaveBeenCalledTimes(1);
    } finally {
      window.removeEventListener(AUTH_LOGOUT_EVENT, onLogout);
    }
  });

  it("HTTP 500 응답 시 phase가 'error'로 전환된다", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("boom", { status: 500 })),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send({ question: "hi" });
    });

    await waitFor(() => {
      expect(result.current.state.phase).toBe("error");
    });
  });
});
