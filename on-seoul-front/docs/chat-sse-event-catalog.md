# Chat SSE 이벤트 카탈로그 (정본)

> **대상**: `POST /api/chat/query` (API 서비스, `ChatController`) 가 프론트로 내보내는 SSE 스트림.
> **목적**: 프론트가 미러링할 SSE 이벤트의 name·data 형태·식별 방법·emit 순서를 한곳에 정리한다.
> **정합 대상**: `on-seoul-front/docs/2026-06-02-frontend-chat-history.md` §4.1, §4.3 / `on-seoul-front/docs/chat-service-cards-interface.md`.

---

## 1. 스트림 개요

```http
POST /api/chat/query
Authorization: Bearer <access_token>
Content-Type: application/json
Accept: text/event-stream
```

응답은 `text/event-stream` SSE 스트림이다. 이벤트는 다음 순서로 흐른다.

```
event:init        ← API 서비스가 emit (항상 첫 이벤트, 1회)
data:<step JSON>  ← AI 서비스 step/progress (0회 이상, name 없는 data 이벤트로 relay)
...
data:<final JSON> ← AI 서비스 final (정상 종료 시 1회)
```

에러 시:

```
event:init  (prepare 성공 후 스트림 도중 에러인 경우에만 선행됨)
event:error ← API 서비스가 emit
```

`prepare` 실패(예: `CHAT_ROOM_NOT_FOUND`)면 `init` 없이 `event:error`만 나간다.

---

## 2. 이벤트 목록

| 이벤트 | SSE name | 소유 | 발생 | data 형태 |
|---|---|---|---|---|
| init | `init` | **API 서비스** | 항상 첫 이벤트, 1회 | JSON `{ "room_id": number, "created": boolean }` |
| step/progress | (없음) | **AI 서비스** | 0회 이상 | AI 서비스 progress payload JSON (그대로 relay) |
| final | (없음) | **AI 서비스** | 정상 종료 1회 | AI 서비스 final payload JSON (그대로 relay) |
| error | `error` | **API 서비스** | 오류 시 1회 | 사용자용 에러 메시지 문자열 |

> **주의 — API 서비스의 relay 방식**: API 서비스는 AI 서비스 SSE의 **`data` 값(JSON 문자열)만** 추출해
> name 없는 data 이벤트로 프론트에 relay한다. 따라서 프론트 입장에서 step/final/workflow_error는
> 모두 **name 없는 `data:` 이벤트**로 도착하며, **payload JSON의 키로 구분**한다(아래 §4).

---

## 3. `init` 이벤트 (API 서비스 소유)

AI 서비스 호출 **전에** API 서비스가 1회 emit한다. 프론트가 답변 완료를 기다리지 않고 즉시 roomId를
확보해 URL 전환/스레딩을 시작할 수 있게 한다.

```
event:init
data:{"room_id":42,"created":true}
```

| 필드 | 타입 | 의미 |
|---|---|---|
| `room_id` | number(long) | 이번 응답이 귀속되는 방 ID. 신규/기존 모두 항상 채워진다. |
| `created` | boolean | 이번 질의로 새로 만들어진 방이면 `true`, 기존 방이면 `false`. |

- **순서 보장**: `init`은 항상 스트림의 첫 이벤트다.
- 필드명은 snake_case (final의 `message_id`와 일관). named event 사용도 기존 `error`와 일관.
- 정본 코드: `on-seoul-api/chat/adapter/in/web/InitEvent.java`, `on-seoul-api/chat/adapter/in/web/ChatController.java`.

---

## 4. step / final 이벤트 (AI 서비스 소유)

step/final의 payload 스키마 **정본은 AI 서비스**다. API 서비스는 가공 없이 그대로 relay한다.

- 정본: `on-seoul-agent/routers/chat.py` (`sse_frame`, final payload), `on-seoul-agent/schemas/state.py`.
- final payload 필드/`service_cards` 명세: `on-seoul-front/docs/chat-service-cards-interface.md` §2.

### final 식별 방법 (프론트 / API 서비스 공통)

name 없는 data 이벤트의 **payload JSON에 `answer` 키가 있고 `error` 키가 없으면 final**이다.

```jsonc
// final 예시
{"message_id":84,"answer":"...","intent":"MAP","title":null,"cache_hit":false,"service_cards":[]}
```

- `workflow_error`는 `answer`와 함께 `error` 키를 가지므로 final이 아니다(이력 저장 제외).
- step/progress payload에는 `answer` 키가 없다.

> API 서비스의 이력 저장 로직도 동일 규칙을 사용한다: `final.answer`만 ASSISTANT content로 저장하고,
> step JSON은 저장하지 않는다. `answer`가 `null`/빈 문자열이면 빈 문자열을 저장한다(카드만 있는 MAP 등).
> final 미수신 시에도 빈 문자열을 저장한다. 정본: `on-seoul-api/chat/adapter/out/agent/ChatAgentClient.java`(파싱),
> `on-seoul-api/chat/application/ChatStreamService.java`(저장).

---

## 5. `error` 이벤트 (API 서비스 소유)

```
event:error
data:일시적인 오류가 발생했습니다. 잠시 후 다시 시도해주세요.
```

- `OnSeoulApiException`이면 해당 메시지(예: `CHAT_ROOM_NOT_FOUND` 안내)를, 그 외 예외면 일반 안내 문구를 보낸다.
- data는 JSON이 아닌 **문자열**이다(기존 동작 유지).
- AI 서비스 내부의 `workflow_error`/`error`(SSE name)는 API 서비스가 name을 떼고 data로 relay하므로
  프론트에는 name 없는 data 이벤트로 도착한다. 위 `event:error`는 **API 서비스 레벨** 오류 전용이다.

---

## 6. 프론트 처리 요약

1. `event:init` 수신 → `room_id`로 URL/스레드 전환 시작. `created`로 신규/기존 분기.
2. name 없는 `data:` 이벤트 수신 → JSON 파싱:
   - `answer` 있고 `error` 없음 → **final**: `service_cards`로 카드 렌더, `answer` 본문 표시, `title`(신규 방 첫 메시지)로 목록 캐시 갱신.
   - 그 외 → step/progress 진행 표시.
3. `event:error` 수신 → 에러 메시지 노출 후 스트림 종료 처리.
