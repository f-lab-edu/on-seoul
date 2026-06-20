# chat BC

`on-seoul-api` 채팅 바운디드 컨텍스트.
사용자 질의를 AI 서비스(`on-seoul-agent`)로 릴레이해 SSE로 스트리밍하고, 대화방·메시지 이력을 관리한다.

---

## 역할

| 역할 | 설명 |
|---|---|
| 챗봇 질의 릴레이 | `POST /api/chat/query` — AI 서비스 `/chat/stream`으로 WebClient 릴레이, 응답을 `SseEmitter`로 스트리밍 |
| 대화방 라이프사이클 | 방 생성(질의 시 자동)·목록 조회·이력 조회·삭제(soft delete) |
| 이력 저장 | 사용자 질문 + AI 최종 답변(`final.answer`)과 결과 카드(`service_cards`)·`intent`를 영속 |
| 멀티턴 맥락 | 직전 N턴 history + carryover(직전 턴 결과 엔티티)를 다음 질의에 실어 "이 곳/세번째" 같은 참조 해소 |
| 남용 방지 | per-user 동시 생성 cap + 분당 호출 RPM 제한으로 LLM 비용 보호 |

---

## 모듈 구조

```
chat/
├── domain/
│   ├── ChatRoom.java          # 대화방 애그리거트. soft delete(deletedAt), 소유자(userId)
│   ├── ChatMessage.java       # 메시지. role(USER|ASSISTANT), content, serviceCards(JSON), intent
│   ├── ChatMessageRole.java   # enum: USER | ASSISTANT
│   ├── ChatTurn.java          # AI 전달용 직전 턴 (role + content)
│   ├── Carryover.java         # 멀티턴 carryover (prevEntities, prevIntent, prevReasoning)
│   └── PrevEntity.java        # carryover 엔티티 (serviceId, label) — 정체성만, 사실필드 제외
│
├── port/
│   ├── in/   # QueryAndStreamUseCase, SendQueryUseCase, ListChatRoomsUseCase,
│   │         # GetChatMessagesUseCase, DeleteChatRoomUseCase
│   └── out/  # AiServiceStreamPort(+AiStreamEvent), ServiceCardParserPort,
│             # Load/Save/DeleteChatRoomPort(+RoomCursor), Load/SaveChatMessagePort
│
├── application/
│   ├── ChatStreamService.java       # SSE 릴레이 오케스트레이션 (disconnect 내성 저장)
│   ├── SendQueryService.java        # 방 resolve(소유자 검증)·history·carryover 조립·answer 저장
│   ├── ListChatRoomsService.java    # 목록 키셋 페이지네이션
│   ├── GetChatMessagesService.java  # 상세(메시지 전체) 조회
│   ├── DeleteChatRoomService.java   # soft delete
│   ├── ChatRoomCursor.java          # (updatedAt, id) 복합 키셋 cursor encode/decode
│   └── ChatConcurrencyGuard.java    # per-user/global 동시 생성 cap
│
└── adapter/
    ├── in/web/   # ChatController(SSE), ChatHistoryController(목록/상세/삭제) + 응답 DTO
    └── out/
        ├── agent/        # ChatAgentClient(WebClient → FastAPI), ServiceCardParser
        └── persistence/  # ChatRoom/ChatMessage JpaEntity·Repository, ChatPersistenceAdapter
```

---

## API

인증: 모든 엔드포인트는 JWT 필터가 주입한 `userId`를 사용. `userId == null`이면 `401`.

| Method | Path | 설명 |
|---|---|---|
| POST | `/api/chat/query` | 챗봇 질의 (SSE). roomId 미지정 시 새 방 생성. 직전 5턴 history + carryover 전달 |
| GET | `/api/chat/rooms` | 대화방 목록 (cursor 페이지네이션, `updated_at DESC` 키셋) |
| GET | `/api/chat/rooms/{roomId}/messages` | 대화방 메시지 이력 (`seq ASC` 전체) |
| DELETE | `/api/chat/rooms/{roomId}` | 대화방 삭제 (soft delete, 204) |

소유자 검증: 조회/삭제/질의 모두 `(roomId, userId, deleted_at IS NULL)` 동시 검증 — 불일치 시 `CHAT_ROOM_NOT_FOUND`(IDOR 차단).

---

## SSE 스트림 계약

`POST /api/chat/query`가 프론트로 내보내는 이벤트는 별도 정본 문서로 관리한다:
`on-seoul-front/docs/chat-sse-event-catalog.md`.

| 이벤트 | 소유 | 설명 |
|---|---|---|
| `event:init` | API | AI 호출 전 1회. `{room_id, created}` — 답변 완료 전 roomId 선전송(URL 전환/스레딩) |
| (name 없는 data) step | AI | 진행 상태. 그대로 relay |
| (name 없는 data) final | AI | 최종 답변. `answer`(있고 `error` 없음)로 식별. `service_cards`/`intent` 포함 |
| `event:error` | API | API 레벨 오류 (`{code, message}`) |

- **disconnect 내성**: 클라이언트가 스트림 도중 끊어도 백그라운드 구독이 AI 스트림을 끝까지 소비해 `final.answer`·`service_cards`·`intent`를 저장한다(답변 유실 방지). 자세한 설계는 `docs/superpowers/plans`(작업 완료 후 삭제) 참고.
- **이력 vs 추론 trace 책임 분리**: 대화 이력(`chat_messages` — 질문 + `final.answer`)은 chat BC(on_data)가, 추론 trace(`chat_agent_traces` — intent/node_path/elapsed_ms)는 AI 서비스(on_ai)가 적재한다.

---

## 멀티턴 carryover (W1)

직전 assistant 턴의 결과를 다음 질의에 실어 참조("이 곳/세번째/그거")를 해소한다. AI 요청 body에 추가:

| 필드 | 설명 |
|---|---|
| `prev_entities` | 직전 assistant `service_cards`에서 `{service_id, label}` (순서 보존, 최대 10, 사실필드 제외) |
| `prev_intent` | 직전 턴 분류 intent (`SQL_SEARCH`/`VECTOR_SEARCH`/`MAP`/`ANALYTICS`/`FALLBACK`). 없으면 null |
| `prev_reasoning` | 직전 턴 판단 근거 (현 단계 미사용 슬롯, 항상 null) |

사실 필드(상태·접수기간 등)는 회신하지 않는다 — AI가 재-hydrate로 최신값을 가져가 stale 데이터로 인한 오답을 막는다.

---

## 남용 방지 (보완 2계층)

채팅 API 설계 시 LLM 비용 폭주를 막기 위해 두 가지를 보완했다. **축이 달라 보완 관계**(중복 아님):

| 장치 | 위치 | 제어 축 | 동작 |
|---|---|---|---|
| **ChatConcurrencyGuard** | chat `application` | 동시 실행 수 | per-user(기본 2) + 전역(기본 50) 동시 생성 cap. 초과 시 `429 CHAT_CONCURRENCY_LIMIT`. AI 호출 전 acquire, 모든 종료 경로에서 release(멱등) |
| **RateLimitFilter** | user `adapter/in/security` | 분당 호출 횟수(RPM) | Redis ZSET + Lua 원자 스크립트 sliding window. `/api/chat/query`에 사용자당 분당 N회. 초과 시 `429 RATE_LIMIT_EXCEEDED`. Redis 장애 시 fail-open |

- 동시성 가드는 "동시에 몇 개", RPM은 "분당 몇 번"을 막는다. 순차 연타는 RPM이, 병렬 폭주는 동시성 가드가 차단한다.
- RateLimitFilter는 보안 횡단관심사라 user 모듈에 두고, 대상 경로/한도는 `app.rate-limit` 설정값으로 외부화한다(chat 모듈을 직접 의존하지 않음).

---

## 설정

```yaml
chat:
  history:
    max-turns: 5                 # AI로 전달할 직전 턴 수 (= 10 메시지)
    max-chars-per-message: 1000  # 메시지당 content 길이 캡
  concurrency:
    per-user: 2                  # 사용자당 동시 생성 cap
    global: 50                   # 전역 동시 생성 cap
    background-timeout-seconds: 120

app:
  rate-limit:
    enabled: true
    requests-per-minute: 20
    window-seconds: 60
    path: /api/chat/query
```

---

## 관련 문서

- [프로젝트 전체 구조](../../docs/architecture.md)
- [SSE 이벤트 카탈로그 (정본)](../../on-seoul-front/docs/chat-sse-event-catalog.md)
- [API 서비스 README](../README.md)
