/**
 * useChatStream 훅 단위 테스트.
 * Mock fetch + ReadableStream으로 SSE 이벤트 누적·취소·에러 전환을 검증한다.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useChatStream } from "./useChatStream";

function sseChunks(events: string[]): ReadableStream<Uint8Array> {
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
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("useChatStream", () => {
  it("token 이벤트를 누적하고 done에서 phase가 'done'으로 전환된다", async () => {
    const events = [
      JSON.stringify({ type: "agent_start", agent: "router" }),
      JSON.stringify({ type: "token", delta: "안녕" }),
      JSON.stringify({ type: "token", delta: "하세요" }),
      JSON.stringify({ type: "done", messageId: 42 }),
    ];

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(sseChunks(events), { status: 200 })),
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
    expect(result.current.state.messageId).toBe(42);
    expect(result.current.state.trace).toContain("▶ router");
  });

  it("error 이벤트 수신 시 phase가 'error'로 전환된다", async () => {
    const events = [
      JSON.stringify({ type: "token", delta: "부분" }),
      JSON.stringify({ type: "error", message: "백엔드 장애" }),
    ];

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response(sseChunks(events), { status: 200 })),
    );

    const { result } = renderHook(() => useChatStream());
    await act(async () => {
      await result.current.send({ question: "hi" });
    });

    await waitFor(() => {
      expect(result.current.state.phase).toBe("error");
    });
    if (result.current.state.phase !== "error") throw new Error("expected error");
    expect(result.current.state.message).toBe("백엔드 장애");
    expect(result.current.state.content).toBe("부분");
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

    // send를 await하지 않고 진행 중에 cancel — pending 스트림으로 stream 상태 유지.
    let sendPromise!: Promise<void>;
    await act(async () => {
      sendPromise = result.current.send({ question: "hi" });
      // 이벤트 루프 한 틱 양보 (fetch 응답 처리가 시작되도록).
      await Promise.resolve();
    });

    push(JSON.stringify({ type: "token", delta: "first" }));

    await act(async () => {
      result.current.cancel();
      // 종료 확인을 위해 stream도 닫아준다 (실제 abort는 reader에 신호 전달).
      try {
        close();
      } catch {
        // already closed
      }
      await sendPromise;
    });

    expect(receivedSignal?.aborted).toBe(true);
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
