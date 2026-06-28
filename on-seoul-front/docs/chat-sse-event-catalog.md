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
event:init             ← API 서비스가 emit (항상 첫 이벤트, 1회)
event:progress         ← AI 서비스 진행 (0회 이상)
event:decision         ← AI 서비스 라우팅 결정 (0~1회)
event:sources_update   ← AI 서비스 검색 소스 갱신 (0회 이상)
event:title            ← 대화 제목 (신규 방 첫 턴만, final과 순서 무관, 미발행 가능)
...
event:final            ← AI 서비스 final (정상 종료 시 1회)
```

> **갱신(2026-06):** 실측 결과 AI 서비스 이벤트는 **이름을 유지한 채**(`event:progress`,
> `event:decision`, `event:sources_update`, `event:final`) relay된다. 프론트는 이름에
> 의존하지 말고 **payload 키로 식별**한다(§4): `answer`가 있으면 final, 그 외는 진행으로 흡수.

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
| progress | `progress` | **AI 서비스** | 0회 이상 | JSON `{ "step": string, "message": string }` |
| decision | `decision` | **AI 서비스** | 0~1회 | JSON `{ "event": "decision", "action", "routes", "user_rationale", "sources" }` |
| sources_update | `sources_update` | **AI 서비스** | 0회 이상 | JSON `{ "event": "sources_update", "sources": [...] }` |
| title | `title` | **AI 서비스** | 신규 방 첫 턴만, 0~1회 | JSON `{ "type": "title", "room_id", "title", "message_id", "query" }` |
| final | `final` | **AI 서비스** | 정상 종료 1회 | AI 서비스 final payload JSON (`answer` + `service_cards` 등, **`title` 없음**) |
| error | `error` | **API 서비스** | 오류 시 1회 | 사용자용 에러 메시지 **문자열**(JSON 아님) |

> **식별 규칙(이름 비의존)**: 이벤트는 이름을 유지한 채 relay되지만, 프론트는 SSE name이 아니라
> **payload 키/`type`**으로 식별한다(§4): `type==="title"`이면 제목, `answer` 있고 `error` 없으면
> **final**, 그 외(progress·decision·sources_update·미지 이벤트)는 전부 **진행으로 흡수**한다.
> 이렇게 하면 미문서화/신규 이벤트도 코드 변경 없이 안전하다(forward-compatible). `init`/`error`만
> 이름으로도 구분한다.

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

## 3-1. `title` 이벤트 (대화 제목)

대화 제목 생성이 답변 파이프라인과 분리(병렬화)되어 **별도 이벤트**로 도착한다. 답변이 느리거나
캐시 히트로 answer 단계를 건너뛰어도 첫 턴 제목이 빠르게 온다.

```
event:title
data:{"type":"title","room_id":63,"title":"도봉구 숲속마을 주민공동이용시설 안내","message_id":2,"query":"도봉구 숲속마을 알려줘"}
```

| 필드 | 타입 | 의미 |
|---|---|---|
| `type` | `"title"` | 식별자. **이 값으로 분기**(SSE name 비의존). |
| `room_id` | number | 제목이 귀속되는 방 ID. |
| `title` | string | 생성된 대화 제목. |
| `message_id` | number | 연관 메시지 ID. |
| `query` | string | 사용자 원본 질의(첫 메시지). |

- **순서 무관**: `final`보다 먼저/나중 모두 가능. `init` 이후 도착은 보장.
- **fail-open**: 제목 생성 실패/빈 제목이면 **미발행**. 이때 방 생성 시 백엔드가 넣은 폴백 제목
  (질문 앞 50자)을 그대로 유지한다 — 미수신을 에러로 처리하지 말 것.
- **첫 턴만**: 신규 방 첫 메시지에서만 발행. 후속 턴엔 없음.
- 프론트: `room_id` 기준으로 제목 표시(라이브 헤더) + 목록/이력 캐시 갱신(있으면).

---

## 4. step / final 이벤트 (AI 서비스 소유)

step/final의 payload 스키마 **정본은 AI 서비스**다. API 서비스는 가공 없이 그대로 relay한다.

- 정본: `on-seoul-agent/routers/chat.py` (`sse_frame`, final payload), `on-seoul-agent/schemas/state.py`.
- final payload 필드/`service_cards` 명세: `on-seoul-front/docs/chat-service-cards-interface.md` §2.

### final 식별 방법 (프론트 / API 서비스 공통)

이벤트의 **payload JSON에 `answer` 키가 있고 `error` 키가 없으면 final**이다(SSE name은
`event:final`이지만 이름에 의존하지 않고 키로 판정).

```jsonc
// final 예시 (title 필드는 별도 title 이벤트로 분리되어 더 이상 포함되지 않음)
{"message_id":84,"answer":"...","intent":"MAP","cache_hit":false,"service_cards":[]}
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
- AI 서비스 내부 오류(`workflow_error`)는 `answer`+`error` 키를 함께 가진 payload로 relay되며,
  프론트는 이를 종료 오류로 처리한다(§4). 위 `event:error`는 **API 서비스 레벨** 오류 전용이다.

---

## 6. 프론트 처리 요약

1. `event:init` 수신 → `room_id`로 URL/스레드 전환 시작. `created`로 신규/기존 분기.
2. 이벤트 수신 → payload JSON 파싱(SSE name 비의존):
   - `type==="title"` → 방 제목 표시(라이브 헤더) + 목록/이력 캐시 갱신. 종료 아님(순서 무관, 미수신 가능).
   - `answer` 있고 `error` 없음 → **final**: `service_cards`로 카드 렌더, `answer` 본문 표시.
   - `answer`+`error` → **workflow_error**: 오류 메시지 노출 후 종료.
   - 그 외(progress·decision·sources_update·미지 이벤트) → 진행 표시로 흡수.
3. `event:error` 수신 → 에러 메시지 노출 후 스트림 종료 처리.
