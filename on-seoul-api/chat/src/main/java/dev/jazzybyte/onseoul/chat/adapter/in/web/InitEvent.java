package dev.jazzybyte.onseoul.chat.adapter.in.web;

import com.fasterxml.jackson.annotation.JsonProperty;

/**
 * SSE named event {@code init}의 data 페이로드. AI 스트림 토큰보다 먼저 1회 emit되어
 * 프론트가 즉시 roomId를 알고 URL 전환/스레딩을 시작할 수 있게 한다.
 *
 * @param roomId  이번 응답이 귀속되는 방 ID(신규/기존 모두 항상 전송)
 * @param created 이번 질의로 새로 만들어진 방이면 true, 기존 방이면 false
 */
public record InitEvent(
        @JsonProperty("room_id") long roomId,
        @JsonProperty("created") boolean created
) {}
