/**
 * SSE 라인 파서 — `data: {...}\n\n` 묶음을 JSON 객체로 yield.
 * 잘못된 청크(파싱 실패, keepalive 등)는 조용히 건너뛴다.
 * 호출 측에서는 `as SseEvent` 캐스팅 후 discriminated union switch로 처리한다.
 */
export async function* parseSseStream(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  signal?: AbortSignal,
): AsyncGenerator<unknown> {
  const decoder = new TextDecoder();
  let buffer = "";

  try {
    while (true) {
      if (signal?.aborted) return;

      const { value, done } = await reader.read();
      if (done) {
        // 마지막 잔여 청크 처리.
        if (buffer.length > 0) {
          const remaining = extractData(buffer);
          if (remaining !== null) {
            try {
              yield JSON.parse(remaining);
            } catch {
              // ignore
            }
          }
        }
        return;
      }

      buffer += decoder.decode(value, { stream: true });

      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const data = extractData(chunk);
        if (data === null) continue;
        try {
          yield JSON.parse(data);
        } catch {
          // 잘못된 JSON 청크는 무시 (백엔드 keepalive 코멘트 등).
        }
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // 이미 해제된 reader면 무시.
    }
  }
}

function extractData(chunk: string): string | null {
  const lines = chunk.split("\n");
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (dataLines.length === 0) return null;
  const joined = dataLines.join("\n").trim();
  return joined.length > 0 ? joined : null;
}
