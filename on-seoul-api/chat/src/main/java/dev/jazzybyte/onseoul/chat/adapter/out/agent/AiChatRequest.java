package dev.jazzybyte.onseoul.chat.adapter.out.agent;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonProperty;
import com.fasterxml.jackson.databind.JsonNode;

import java.util.List;

@JsonInclude(JsonInclude.Include.NON_NULL)
record AiChatRequest(
        @JsonProperty("room_id") long roomId,
        @JsonProperty("message_id") long messageId,
        @JsonProperty("message") String message,
        @JsonProperty("lat") Double lat,
        @JsonProperty("lng") Double lng,
        @JsonProperty("history") List<Turn> history,
        // ── 멀티턴 carryover — nested 전면 전환 ──
        // 직전 ASSISTANT의 working_set 봉투(opaque)를 단일 prev_working_set 객체로 회신한다.
        // null이면 @JsonInclude(NON_NULL)로 생략되어 AI가 현행 동작(폴백)으로 처리한다(하위호환).
        // 기존 평면 carryover(prev_entities/prev_intent/prev_reasoning)는 이 nested 봉투로 흡수되었다.
        @JsonProperty("prev_working_set") JsonNode prevWorkingSet,
        // 새 대화방 생성 첫 턴 여부(roomId 부재로 API가 판단). true면 AI가 방 제목을 생성한다.
        // primitive boolean이라 @JsonInclude(NON_NULL)와 무관하게 true/false 모두 항상 명시 전송된다.
        // AI 측 기본값은 false(미전송 시 제목 미생성)이나 API는 매 요청 명시 전송한다(계약 고정).
        @JsonProperty("title_needed") boolean titleNeeded
) {
    record Turn(
            @JsonProperty("role") String role,
            @JsonProperty("content") String content
    ) {}
}
