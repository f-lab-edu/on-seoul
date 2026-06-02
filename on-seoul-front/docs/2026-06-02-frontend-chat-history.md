# 프론트엔드 — 대화이력 관리 기능 구현 가이드

이 문서는 `on-seoul` 프론트엔드 구현 에이전트가 **대화이력 관리 화면**을 개발할 때 참고하는 작업 가이드입니다. 백엔드(`on-seoul-api` chat BC)에 추가된 대화방 목록 조회 / 메시지 이력 조회 / 대화방 삭제 API를 기반으로, 사용자가 자신의 과거 대화를 다시 확인하고 정리하는 흐름을 정의합니다.

> **Note:** 백엔드의 대화이력 REST 컨트롤러(`ChatHistoryController`)는 구현·테스트 완료되어 있습니다 (§9 협업 체크리스트 참조).
>
> **Breaking change:** 챗봇 질의 엔드포인트 경로가 **`POST /query` → `POST /api/chat/query`** 로 변경되었습니다. 기존 채팅 화면의 호출 경로를 먼저 수정해야 합니다 (§4.1, §8).
>
> **정본**: `on-seoul-api/chat/adapter/in/web/ChatHistoryController.java`, 동일 패키지의 응답 DTO(`RoomSummaryResponse`, `RoomListResponse`, `ChatHistoryResponse`, `MessageResponse`), `on-seoul-api/docs/superpowers/plans/2026-06-02-chat-history-management.md`.

---

## 1. 기능 개요

대화이력 관리란, 로그인 사용자가 AI 챗봇과 나눈 과거 대화를 다시 열어 보고 필요 없는 대화를 정리할 수 있는 기능입니다. 다음 3가지로 구성됩니다.

1. **대화방 목록 조회** — 내 대화방을 최근 활동 순으로 페이지네이션 조회
2. **대화방 상세(이력) 조회** — 특정 대화방의 메시지(USER/ASSISTANT)를 순서대로 전체 조회
3. **대화방 삭제** — 개별 대화방을 soft delete (서버는 보존, 사용자에게는 비노출)

대화방(`ChatRoom`)은 사용자가 챗봇에 첫 질문을 보낼 때 자동 생성되며, 같은 방에서 후속 질문을 이어가면 메시지가 누적됩니다. 프론트엔드의 역할은 **사용자가 자신의 대화방을 목록·상세로 탐색하고 삭제할 수 있는 UI** 를 제공하는 것입니다.

### 사용자 시나리오

1. 사용자가 챗봇에 첫 질문을 보내면 대화방이 생성되고, `final` 이벤트의 `title`(자동 생성 제목)을 받습니다.
2. 사용자가 대화이력 화면에 진입해 자신의 대화방 목록을 최근 활동 순으로 확인합니다.
3. 대화방을 선택하면 해당 방의 전체 메시지(질문/답변)를 시간 순으로 봅니다.
4. 이어서 대화하고 싶으면 그 방의 `roomId`로 후속 질의를 보냅니다.
5. 필요 없는 대화방을 삭제하면 목록에서 사라지고, 이후 그 방의 상세 조회는 404가 됩니다.

---

## 2. 도메인 용어

프론트엔드 코드와 UI 텍스트에서 백엔드 도메인 용어와 일관성을 유지합니다.

| 용어 | 영문 | 설명 |
|---|---|---|
| 대화방 | ChatRoom | 한 번의 대화 세션. 사용자에게 귀속되며 메시지의 부모. 애그리거트 루트 |
| 메시지 | ChatMessage | 대화방 안의 질문 또는 답변 1건. `seq` 순서로 정렬 |
| 역할 | Role | 메시지 작성 주체. `USER`(사용자 질문) 또는 `ASSISTANT`(챗봇 답변) |
| 제목 | Title | 대화방 식별용 라벨. 첫 질문 기반으로 AI가 자동 생성(`titleGenerated`). 최대 200자 |
| 커서 | Cursor | 목록 페이지네이션 위치 토큰. 불투명 문자열(§4.2) |
| 삭제 | Soft delete | 물리 삭제가 아닌 `deletedAt` 마킹. 삭제된 방은 목록/상세에서 제외 |

---

## 3. 화면 구성

다음 화면을 구현합니다. 라우트와 화면명은 프로젝트 컨벤션에 맞춰 조정합니다.

### 3.1 대화이력 목록 (`/chat/history` 등)

사용자의 대화방을 **최근 활동 순(updated_at DESC)** 으로 리스트로 표시합니다.

- 각 대화방 항목 표시
  - 제목(`title`) — 최대 200자, 길면 말줄임
  - 마지막 활동 시각(`updatedAt`) — 상대시간(예: "3일 전") + 절대시간 병기 권장
  - **삭제** 버튼 (행 액션 또는 스와이프)
- 항목 클릭 → 상세(3.2) 진입
- **무한 스크롤 / 더 보기** — `nextCursor` 기반 페이지네이션(§4.2)
- 빈 상태: 대화방이 없으면 "아직 대화 이력이 없습니다" 안내 + 챗봇으로 이동 유도

> **정렬 주의:** 목록은 `updatedAt` 내림차순입니다. 후속 질의로 메시지가 추가되면 그 방이 목록 최상단으로 올라옵니다. 클라이언트에서 별도 재정렬하지 말고 서버 순서를 그대로 노출합니다.

### 3.2 대화방 상세 (`/chat/history/{roomId}` 등)

선택한 대화방의 전체 메시지를 시간 순(`seq` 오름차순)으로 표시합니다. 채팅 UI와 동일한 말풍선 레이아웃을 재사용할 수 있습니다.

- 헤더: 대화방 제목(`title`)
- 메시지 목록
  - `role === "USER"` → 사용자 말풍선(우측)
  - `role === "ASSISTANT"` → 챗봇 말풍선(좌측)
  - 각 메시지의 `createdAt` 표시(선택)
- **이어서 대화하기** — 이 방의 `roomId`로 `POST /api/chat/query` 호출 시 같은 방에 메시지가 누적됨(§4.1)
- 헤더 또는 상단에 **삭제** 액션 배치 가능

> **Note:** 상세 응답에는 과거 답변의 `service_cards`(시설 카드)가 **포함되지 않습니다.** 저장되는 건 메시지의 `content`(자연어 텍스트)뿐입니다. 카드 UI는 실시간 SSE `final` 이벤트에서만 제공되므로(`chat-service-cards-interface.md`), 이력 화면에서는 텍스트 본문만 렌더링합니다. 카드 복원이 필요하면 별도 백엔드 작업 협의가 필요합니다.

### 3.3 삭제 확인

대화방 삭제는 되돌릴 수 없는 사용자 경험이므로(서버는 soft delete지만 UI상 복구 동선 없음) 확인 단계를 둡니다.

- "이 대화를 삭제할까요?" 확인 다이얼로그
- 확인 시 `DELETE /api/chat/rooms/{roomId}` 호출 → 204
- 성공 시 목록에서 즉시 제거(낙관적 UI 권장, 실패 시 롤백 + 토스트)
- 상세 화면에서 삭제했다면 목록으로 복귀

---

## 4. API 계약

JWT 인증이 모든 엔드포인트에 필요합니다. 백엔드 `JwtAuthenticationFilter`가 요청 속성 `userId`를 설정한 후 컨트롤러에 전달합니다. 토큰이 없거나 무효하면 컨트롤러 진입 전/직후 `401`이 반환됩니다.

모든 시각 필드는 **ISO 8601 / UTC**(`OffsetDateTime` 직렬화, 예: `"2026-06-02T05:30:00Z"`)입니다. 백엔드 JVM·JDBC가 UTC로 고정되어 있으므로 클라이언트는 로컬 타임존으로 변환해 표시합니다.

### 4.1 챗봇 질의 (경로 변경 — Breaking)

```http
POST /api/chat/query          ← 변경 전: POST /query
Authorization: Bearer <access_token>
Content-Type: application/json
Accept: text/event-stream

{
  "roomId": 5,        // 선택 — 생략/null이면 새 대화방 생성, 값이 있으면 기존 방에 이어 붙임
  "question": "서울 문화행사 알려줘",   // 필수, 공백만 있으면 400
  "lat": 37.5665,     // 선택 — 지도(MAP) 의도에서 사용
  "lng": 126.9780     // 선택
}
```

- 응답은 **SSE 스트림**입니다. 토큰 스트리밍·`final`·`error` 이벤트 처리는 기존 채팅 구현과 동일하며, 이 변경에서 바뀌는 것은 **경로뿐**입니다.
- `final` 이벤트의 `title`은 **첫 메시지(새 방 생성 시)** 에만 채워집니다. 이 값을 목록 캐시에 반영하면 새 대화가 즉시 목록에 보이게 할 수 있습니다.
- 상세 화면의 "이어서 대화하기"는 그 방의 `roomId`를 실어 호출합니다. 타인 소유/미존재/삭제된 `roomId`를 보내면 `404 CHAT_ROOM_NOT_FOUND`(스트림 `error` 이벤트로 전달).

> SSE 이벤트 스키마와 카드 처리는 `chat-service-cards-interface.md`를 참조합니다. 본 문서는 경로 변경만 다룹니다.

### 4.2 대화방 목록 조회 (신규)

```http
GET /api/chat/rooms?cursor=<opaque>&size=20
Authorization: Bearer <access_token>
```

| 쿼리 파라미터 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `cursor` | 아니오 | (없음) | 다음 페이지 토큰. **첫 페이지는 생략**. 이전 응답의 `nextCursor`를 그대로 전달 |
| `size` | 아니오 | `20` | 페이지 크기. 서버에서 **1~100으로 clamp**. 범위 밖 값도 400이 아니라 보정됨 |

**응답 (200 OK):**

```json
{
  "rooms": [
    {
      "roomId": 12,
      "title": "서울 문화행사 알려줘",
      "titleGenerated": true,
      "createdAt": "2026-06-01T09:00:00Z",
      "updatedAt": "2026-06-02T05:30:00Z"
    }
  ],
  "nextCursor": "MTcxODg2NDIwMDAwMF8xMg=="
}
```

- `rooms`는 `updatedAt DESC, roomId DESC` 정렬(키셋). 활성 방만 포함(삭제된 방 제외).
- `nextCursor`가 `null`이면 **마지막 페이지**입니다. 더 이상 요청하지 않습니다.
- `nextCursor`는 **불투명 Base64 토큰**입니다. 클라이언트는 내용을 파싱·생성·가공하지 말고 **받은 값을 그대로** 다음 요청 `cursor`로 전달합니다.
- 잘못된(손상된) `cursor`를 보내면 `400 INVALID_INPUT`.

### 4.3 대화방 메시지 이력 조회 (신규)

```http
GET /api/chat/rooms/{roomId}/messages
Authorization: Bearer <access_token>
```

**응답 (200 OK):**

```json
{
  "roomId": 12,
  "title": "서울 문화행사 알려줘",
  "messages": [
    {
      "seq": 1,
      "role": "USER",
      "content": "서울 문화행사 알려줘",
      "createdAt": "2026-06-01T09:00:00Z"
    },
    {
      "seq": 2,
      "role": "ASSISTANT",
      "content": "이번 주 서울 문화행사를 안내해 드립니다. ...",
      "createdAt": "2026-06-01T09:00:03Z"
    }
  ]
}
```

- `messages`는 `seq` 오름차순(대화 진행 순). 페이지네이션 없이 **전체 반환**(챗봇 대화는 건수가 적음).
- `role`은 문자열 `"USER"` / `"ASSISTANT"`.
- 미존재 / 타인 소유 / 삭제된 방 → `404 CHAT_ROOM_NOT_FOUND` (소유자 외에는 존재 여부를 노출하지 않음 = IDOR 차단).

### 4.4 대화방 삭제 (신규)

```http
DELETE /api/chat/rooms/{roomId}
Authorization: Bearer <access_token>
```

**응답:** `204 No Content` (바디 없음).

- soft delete. 삭제 후 같은 방의 상세 조회(4.3)·재삭제는 `404 CHAT_ROOM_NOT_FOUND`.
- 미존재 / 타인 소유 / 이미 삭제됨 → 모두 `404 CHAT_ROOM_NOT_FOUND`.

---

## 5. 인증과 토큰 처리

`on-seoul` 인증은 OAuth2 로그인 후 JWT(Access + Refresh) 발급 방식입니다.

- Access Token: HTTP 헤더 `Authorization: Bearer <token>`
- Refresh Token: HttpOnly 쿠키(자동 전송)
- 401 응답 수신 시 토큰 재발급 후 재시도(기존 인증 흐름 재사용)
- SSE(`POST /api/chat/query`)도 동일하게 `Authorization` 헤더 필요

---

## 6. 에러 응답 처리

백엔드는 `OnSeoulApiException` 기반 표준 에러 응답을 반환합니다. **응답 바디 키는 `code` / `message`** 입니다(알림 문서의 `error` 키와 다름 — 본 BC는 `code` 사용).

```json
{ "code": "CHAT_ROOM_NOT_FOUND", "message": "대화방을 찾을 수 없습니다." }
```

주요 에러:

| `code` | HTTP | 발생 상황 | 처리 방안 |
|---|---|---|---|
| `UNAUTHORIZED` | 401 | 토큰 없음/무효 | 로그인 유도 또는 refresh 후 재시도 |
| `CHAT_ROOM_NOT_FOUND` | 404 | 미존재/타인 소유/삭제된 방 조회·삭제 | "대화방을 찾을 수 없습니다" + 목록으로 복귀 |
| `INVALID_INPUT` | 400 | 손상된 `cursor` | 커서 초기화 후 첫 페이지 재요청 |
| `잘못된 요청값` | 400 | `question` 누락/공백 등 검증 실패 | 입력 필드 에러 표시 (`message`에 상세) |

> **검증 에러의 `code`는 한글 문자열 `"잘못된 요청값"`** 입니다. 코드 분기 시 이 값에 의존하지 말고 HTTP 상태(400)와 `message`로 처리하는 편이 안전합니다.

---

## 7. 타입 정의(제안)

```typescript
// types/chat-history.ts

export type ChatRole = "USER" | "ASSISTANT";

export type RoomSummary = {
  roomId: number;
  title: string;
  titleGenerated: boolean;
  createdAt: string;   // ISO 8601 (UTC)
  updatedAt: string;
};

export type RoomListResponse = {
  rooms: RoomSummary[];
  nextCursor: string | null;   // null이면 마지막 페이지
};

export type ChatMessageItem = {
  seq: number;
  role: ChatRole;
  content: string;
  createdAt: string;
};

export type ChatHistoryResponse = {
  roomId: number;
  title: string;
  messages: ChatMessageItem[];
};
```

---

## 8. UI/UX 가이드라인

- **경로 변경 우선 처리:** 기존 채팅 호출을 `/query` → `/api/chat/query`로 전부 교체합니다(전역 상수/클라이언트 한 곳에서 관리 권장). 미변경 시 챗봇 전체가 동작 불능.
- **커서는 불투명 토큰:** `nextCursor`를 절대 파싱·재조립하지 않습니다. 받은 문자열만 다음 `cursor`로 전달하고, `null`이면 페이지네이션 종료.
- **목록 순서 보존:** 서버의 `updatedAt DESC` 순서를 그대로 노출. 후속 질의로 갱신된 방은 다음 새로고침 때 상단으로 이동.
- **새 대화 즉시 반영:** 새 방 생성 시 `final.title`을 받아 목록 캐시 맨 앞에 낙관적으로 추가하면 UX가 매끄럽습니다.
- **삭제는 낙관적 + 확인:** 확인 다이얼로그 후 즉시 목록에서 제거, 실패 시 롤백 + 토스트.
- **이력에는 카드 없음:** 상세 화면은 메시지 텍스트만. `service_cards`는 실시간 SSE 전용(§3.2 Note).
- **시각 표시:** UTC 수신 → 로컬 타임존 변환. 목록은 상대시간 + 절대시간 병기.
- **빈 상태/로딩/에러:** 목록·상세 각각 빈 상태, 스켈레톤, 404/네트워크 에러 UI를 분리해 둡니다.

---

## 9. 백엔드 협업 체크리스트

- [x] 대화방 목록/상세/삭제 컨트롤러 신규 구현 — `ChatHistoryController` (GET `/rooms`, GET `/rooms/{roomId}/messages`, DELETE `/rooms/{roomId}`)
- [x] 챗봇 질의 경로 통일 — `POST /query` → `POST /api/chat/query`
- [x] soft delete (`chat_rooms.deleted_at`) + 활성 방 필터 + 키셋 인덱스
- [x] 소유자 검증(IDOR 차단) — 타인/미존재/삭제 방은 일괄 404
- [x] 목록 키셋 페이지네이션(`updatedAt DESC, id DESC`) + 불투명 커서
- [x] `:chat:test` / `:bootstrap:test` 전체 통과, QA·코드리뷰 완료
- [ ] (협의) 상세 이력에 과거 `service_cards` 복원 여부 — 현재 미저장, 텍스트만 보존
- [ ] (협의) 대화방 제목 사용자 수정 API — 이번 범위 제외(별도 작업)

---

## 10. 참고 자료

- `on-seoul-api/docs/superpowers/plans/2026-06-02-chat-history-management.md` — 백엔드 기획/설계/작업 정본
- `on-seoul-front/docs/chat-service-cards-interface.md` — SSE `final` 이벤트 / `service_cards` 카드 명세
- `on-seoul-api/chat/adapter/in/web/ChatHistoryController.java` — 엔드포인트 정본
- `docs/domain-model.md` §4 (채팅 도메인) — `ChatRoom`/`ChatMessage` 도메인 모델
- `on-seoul-api/README.md` — 챗봇 엔드포인트 표

---

## Next steps

1. 챗봇 질의 호출 경로를 `/api/chat/query`로 먼저 교체하고 기존 채팅이 정상 동작하는지 확인합니다.
2. `types/chat-history.ts` 타입을 추가하고 API 클라이언트 함수(목록/상세/삭제)를 구현합니다.
3. 대화이력 목록 → 상세 → 삭제 화면을 단계적으로 구현하고, 각 단계에서 실 API로 검증합니다.
4. "이어서 대화하기" 동선에서 기존 `roomId`로 후속 질의가 같은 방에 누적되는지 확인합니다.
