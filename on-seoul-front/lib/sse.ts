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
 *
 * 정규화 규칙(docs/chat-sse-event-catalog.md):
 * - `event:` 이름이 있는 이벤트(init/error)는 그 이름을 data JSON의 `type`으로 주입한다.
 * - `event:error`의 data는 평문 문자열이므로 `{ type: "error", message: <문자열> }`로 래핑한다.
 * - name 없는 data는 payload 키로 구분: `answer`가 있으면(`error` 동반 여부로) final/workflow_error를 type으로 주입.
 *   그 외(step 등)는 그대로 둔다(호출 측이 진행으로 흡수).
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

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    // data가 JSON이 아닌 평문 — named 이벤트면 message로 래핑(예: event:error),
    // 이름도 없으면 keepalive/주석 등으로 보고 무시.
    return eventType !== null ? { type: eventType, message: raw } : null;
  }

  if (parsed === null || typeof parsed !== "object") {
    // 따옴표 문자열 등 객체가 아닌 JSON — named면 message로 래핑.
    return eventType !== null ? { type: eventType, message: String(parsed) } : null;
  }

  const obj = parsed as Record<string, unknown>;
  if (!("type" in obj) && !("step" in obj)) {
    if (eventType !== null) {
      obj.type = eventType;
    } else if (typeof obj["answer"] === "string") {
      // 카탈로그 §4: answer 있고 error 없으면 final, error 동반이면 workflow_error.
      obj.type = obj["error"] != null ? "workflow_error" : "final";
    }
  }

  return obj;
}
