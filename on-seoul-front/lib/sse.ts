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
  // \r\n 종결 프록시(NGINX 등) 호환을 위해 \r 제거 후 분할.
  const lines = chunk.split("\n").map((l) => l.replace(/\r$/, ""));
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

    if (!("type" in parsed) && !("step" in parsed)) {
      if (eventType !== null) {
        // event: 라인이 있으면 그 값을 type으로 주입.
        parsed.type = eventType;
      } else if ("message_id" in parsed && "answer" in parsed) {
        // event: 라인 없이 최종 응답만 오는 경우 shape으로 추론.
        // error 필드가 있으면 workflow_error, 없으면 final.
        parsed.type = parsed["error"] != null ? "workflow_error" : "final";
      }
    }

    return parsed;
  } catch {
    // 잘못된 JSON 청크는 무시 (백엔드 keepalive 코멘트 등).
    return null;
  }
}
