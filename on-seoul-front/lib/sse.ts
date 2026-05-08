/**
 * SSE 라인 파서 — `data: {...}\n\n` 묶음을 JSON 객체로 yield.
 * `event:` 필드가 있으면 파싱한 JSON에 `type` 키로 주입한다 (data에 type이 없는 경우만).
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
        if (buffer.length > 0) {
          const parsed = extractChunk(buffer);
          if (parsed !== null) yield parsed;
        }
        return;
      }

      buffer += decoder.decode(value, { stream: true });

      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        const parsed = extractChunk(chunk);
        if (parsed !== null) yield parsed;
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

/**
 * SSE 청크에서 event·data 라인을 추출해 JSON 객체로 반환.
 * `event:` 값을 data JSON의 `type` 필드로 주입 (data에 type이 없는 경우).
 */
function extractChunk(chunk: string): unknown {
  const lines = chunk.split("\n");
  let eventType: string | null = null;
  const dataLines: string[] = [];

  for (const line of lines) {
    if (line.startsWith("event:")) {
      eventType = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }

  if (dataLines.length === 0) return null;
  const raw = dataLines.join("\n").trim();
  if (raw.length === 0) return null;

  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    // event 필드를 type으로 주입 (data에 이미 type이 있으면 우선)
    if (eventType !== null && !("type" in parsed) && !("step" in parsed)) {
      parsed.type = eventType;
    }
    return parsed;
  } catch {
    // 잘못된 JSON 청크는 무시 (백엔드 keepalive 코멘트 등).
    return null;
  }
}
