/**
 * 대화이력 `ChatMessage.content` 필드 파싱 유틸.
 *
 * 백엔드 버그: ASSISTANT 메시지의 content에 최종 답변 텍스트 대신
 * SSE 스트림 이벤트 전체가 concatenate되어 저장됨.
 * 예: `{"step":"routing","message":"..."}{"message_id":84,"answer":"...","intent":"MAP",...}`
 *
 * 이 함수는 임시 방어 로직으로, content 문자열에서 `answer` 필드를 가진
 * JSON 객체를 찾아 그 값만 반환한다.
 * - 이미 깨끗한 텍스트(JSON이 아님)는 그대로 반환.
 * - `answer`를 찾지 못하면 원본을 그대로 반환(최소 손실 원칙).
 *
 * @todo 백엔드가 content에 answer 텍스트만 저장하도록 수정되면 이 함수를 제거한다.
 */
export function extractChatContent(raw: string): string {
  if (!raw.startsWith("{")) return raw;

  // 중첩 {} 깊이를 추적해 concatenate된 JSON 객체를 하나씩 분리한다.
  let depth = 0;
  let start = 0;
  let lastAnswer: string | null = null;

  for (let i = 0; i < raw.length; i++) {
    const ch = raw[i];
    if (ch === "{") {
      if (depth === 0) start = i;
      depth++;
    } else if (ch === "}") {
      depth--;
      if (depth === 0) {
        const chunk = raw.slice(start, i + 1);
        try {
          const obj = JSON.parse(chunk) as Record<string, unknown>;
          if (typeof obj.answer === "string") {
            lastAnswer = obj.answer;
          }
        } catch {
          // 파싱 실패 청크는 무시
        }
      }
    }
  }

  return lastAnswer ?? raw;
}
