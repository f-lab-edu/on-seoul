package dev.jazzybyte.onseoul.chat.port.out;

/**
 * AI 서비스 SSE 이벤트 1건을 표현한다.
 *
 * <p>AI 이벤트 JSON의 파싱(계약 해석)은 adapter/out/agent의 책임이다. application은
 * {@link #raw}를 프론트로 그대로 relay하고, {@link #finalAnswer}가 존재하면 그 값만 저장한다.
 *
 * @param raw         프론트로 relay할 원본 SSE data(JSON 문자열) — 변경 없이 그대로 전달
 * @param finalAnswer final 이벤트에서 추출한 답변 텍스트. final이 아니면 null.
 *                    answer 키가 비어 있으면 빈 문자열.
 */
public record AiStreamEvent(String raw, String finalAnswer) {

    public static AiStreamEvent relay(String raw) {
        return new AiStreamEvent(raw, null);
    }

    public static AiStreamEvent finalEvent(String raw, String answer) {
        return new AiStreamEvent(raw, answer == null ? "" : answer);
    }

    public boolean isFinal() {
        return finalAnswer != null;
    }
}
