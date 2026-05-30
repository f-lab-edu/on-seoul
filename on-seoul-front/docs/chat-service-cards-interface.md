# Chat SSE `final` — `service_cards` 인터페이스 명세

> **대상 변경**: `POST /chat/stream` 의 SSE `final` 이벤트 payload 에 `service_cards: ServiceCard[]` 필드 추가.
> **목적**: 프론트가 LLM 자연어 답변(`answer`) 을 파싱해 카드 UI 를 구성하던 방식을 폐기하고, 백엔드가 정규화한 구조화 배열을 직접 소비한다.
>
> **정본**: `on-seoul-agent/schemas/state.py` `AgentState.service_cards`, `on-seoul-agent/agents/answer_agent.py` `_normalize()`, `on-seoul-agent/routers/chat.py` final payload.

---

## 1. 변경 요약

| 항목 | 변경 전 | 변경 후 |
|---|---|---|
| `final` 이벤트 payload | `message_id`, `answer`, `intent`, `title`, `cache_hit` | 위 5개 + **`service_cards: ServiceCard[]`** |
| 카드 UI 데이터 출처 | `answer` 자연어 파싱 | `service_cards` 배열 직접 사용 |
| 최대 카드 수 | (LLM 자유) | **5개 고정** (백엔드 `_DISPLAY_LIMIT`) |

`answer` 텍스트와 `service_cards` 배열은 **동일한 결과 집합의 두 표현**이다. 답변 본문에 "외 N건" 표기가 들어 있으면 실제 결과는 5건보다 많고 (`service_cards.length === 5`), 그렇지 않으면 `service_cards.length` 가 전체 건수다.

---

## 2. `final` 이벤트 새 스키마

### 2.1 Payload 구조

```typescript
type SseFinalEvent = {
  type: "final";
  message_id: number;
  answer: string;            // 자연어 답변 (카드 텍스트 포함)
  intent: "SQL_SEARCH" | "VECTOR_SEARCH" | "MAP" | "FALLBACK" | null;
  title: string | null;      // 첫 메시지일 때만 채워짐
  cache_hit: boolean;        // 캐시 히트 여부 (UI 표시는 자유)
  service_cards: ServiceCard[];  // 신규 — 항상 배열, 빈 결과면 []
};
```

- `service_cards` 는 **`null` 이 절대 오지 않는다**. 결과 없음 / AnswerAgent 미실행 / 구버전 캐시 envelope 모두 백엔드 응답 직렬화 단계에서 `[]` 로 정규화된다.
- 최대 길이는 **5**. 정확한 컷오프 정책은 §4 참조.

### 2.2 `ServiceCard` 필드 정의

```typescript
type ServiceCard = {
  service_id: string;            // 서울시 공공서비스 예약 service_id (안정 식별자)
  service_name: string | null;   // 시설/프로그램명 (예: "서남센터 테니스장2번")
  area_name: string | null;      // 자치구 (예: "강서구")
  place_name: string | null;     // 장소명 (예: "서남물재생센터")
  max_class_name: string | null; // 대분류 (예: "체육시설")
  min_class_name: string | null; // 소분류 (예: "테니스장")
  service_status: ServiceStatus | null;  // §3 참조
  payment_type: string | null;   // 요금 구분 (예: "유료", "무료")
  target_info: string | null;    // 대상 (예: "어르신", "전체")
  receipt_start_dt: string | null;  // 접수 시작 (§5 — ISO 8601 문자열)
  receipt_end_dt: string | null;    // 접수 종료
  service_url: string;           // §6 — 항상 문자열, 빈 적이 없음
};
```

#### 필드별 상세

| 필드 | 비어 있을 수 있나? | 표시 권장 |
|---|---|---|
| `service_id` | 아니오 | 키 용도, UI 미노출 |
| `service_name` | DB 누락 시 가능 | 카드 헤드라인 |
| `area_name` / `place_name` | 가능 | "{area_name} {place_name}" 조합, 둘 다 비면 라인 생략 |
| `max_class_name` / `min_class_name` | 가능 | "{max} > {min}", 한쪽만 있으면 있는 쪽만 |
| `service_status` | 가능 | 상태 칩 (§3 색상 매핑) |
| `payment_type` / `target_info` | 가능 | "요금: {payment_type} / 대상: {target_info}", 둘 다 비면 라인 생략 |
| `receipt_start_dt` / `receipt_end_dt` | 가능 | "접수: {start} ~ {end}", §5 포맷 변환 권장 |
| `service_url` | **항상 채워짐** | "바로가기" 버튼/링크 |

> **렌더링 원칙**: `null` 필드는 해당 라인 자체를 그리지 않는다 (빈 값으로 "—" 표시 금지 — 카드가 지저분해짐).

---

## 3. `service_status` 값과 색상 매핑 제안

백엔드 `service_status` 는 **자유 문자열**이며 수집 원본의 한글 라벨을 그대로 노출한다. 알려진 값:

| 값 | 의미 | 칩 색상 권장 |
|---|---|---|
| `"접수중"` | 예약 가능 | green |
| `"예약마감"` | 정원 마감 | gray |
| `"접수종료"` | 접수 기간 종료 | gray |
| `"예약일시중지"` | 일시 중지 | amber |
| `"안내중"` | 안내만, 예약 불가 | gray |

```typescript
type ServiceStatus =
  | "접수중"
  | "예약마감"
  | "접수종료"
  | "예약일시중지"
  | "안내중";
```

- 미지의 값이 올 가능성에 대비해 `string | null` 로 좁히지 말고 `ServiceStatus | string | null` 또는 폴백 라벨 처리.
- "접수중" 카드만 강조 표시(상단 정렬, 색상 강조)하는 UX 권장.

---

## 4. 컷오프 정책 — 5개 + "외 N건"

백엔드가 적용하는 규칙:

1. `_collect_results()` 가 검색 결과 전체를 평탄 dict 리스트로 모은다.
2. `display = all_results[:5]` 로 상위 5건만 LLM 컨텍스트와 `service_cards` 양쪽에 동일하게 노출.
3. `extra_count = max(0, len(all_results) - 5)` 가 0보다 크면 LLM 이 `answer` 본문 끝에 "외 N건" 을 적는다.

**프론트 처리**:
- 카드는 `service_cards` 만 그린다 (최대 5개 보장).
- `answer` 본문에 "외 N건" 텍스트가 들어 있으면 그대로 보여준다. **별도 파싱 불필요** — 텍스트 그대로 노출.
- 더 많은 결과를 보고 싶다는 사용자 요청이 오면 후속 메시지로 재검색해야 한다 (현재 페이지네이션 미지원).

---

## 5. 날짜 필드 포맷

`receipt_start_dt` / `receipt_end_dt` 는 **ISO 8601 문자열**로 직렬화된다 (예: `"2025-11-01T00:00:00"` 또는 `"2025-11-01T00:00:00+09:00"`).

- 원본 DB 컬럼은 `timestamp` 타입이며, 백엔드 SSE 직렬화 단계의 `json.dumps(..., default=str)` 를 통과한 결과.
- 시간 부분이 의미 없는 데이터가 다수이므로 카드 UI 에서는 **`YYYY-MM-DD` 로만 표시 권장**:
  ```typescript
  const formatDate = (iso: string | null): string =>
    iso ? iso.slice(0, 10) : "";
  ```
- 백엔드 LLM 프롬프트(`answer` 본문) 도 시간 부분 생략 규칙을 따른다.

**주의**: `service_open_start_dt` / `service_open_end_dt` (이용 기간) 는 **카드에 포함되지 않는다.** DB 에 비현실적 범위(예: 2021~2031) 가 다수 존재해 의도적으로 제외했다. 데이터 신뢰성 개선 시 재검토 예정 — 프론트에서 별도 요청 금지.

---

## 6. `service_url` 처리

- 항상 비어 있지 않은 문자열로 보장된다.
- 시설별 고유 URL 이 존재하면 그 값, 누락이면 `"https://yeyak.seoul.go.kr"` (전체 포털) 로 백엔드가 폴백한다.
- 프론트는 추가 분기 없이 `<a href={card.service_url} target="_blank" rel="noopener noreferrer">바로가기</a>` 로 그대로 사용 가능.

---

## 7. 빈 결과 / 에러 경로

| 시나리오 | `answer` | `service_cards` | UI 가이드 |
|---|---|---|---|
| 정상 결과 0건 | "조건에 맞는 시설을 찾지 못했습니다." | `[]` | 답변만 노출, 카드 영역 미노출 |
| 정상 결과 1~5건 | 카드 텍스트 + 마무리 안내 | 1~5 길이 | 카드 + 답변 본문 동시 노출 |
| 정상 결과 6+ 건 | 카드 텍스트 + "외 N건" + 마무리 안내 | 5 길이 | 카드 5개 + 답변 본문 |
| 캐시 히트 | (이전 답변) | (이전 카드) | `cache_hit=true` 활용은 자유 |
| `workflow_error` 이벤트 | "서비스 처리 중 오류가 발생했습니다." | `[]` (백엔드 강제) | 에러 토스트 우선 |
| `error` 이벤트 | (없음) | (없음) | 시스템 오류 토스트 |

`final` 과 `workflow_error` 는 상호 배타. 한 스트림에서 둘 중 하나만 나간다.

---

## 8. `workflow_error` 시 `service_cards` 정책

`workflow_error` payload 에서 `service_cards` 는 **항상 빈 배열 `[]`** 로 고정된다. 백엔드 라우터가 error 분기에서 명시적으로 덮어쓴다 — 에러 메시지와 부분 검색 카드가 동시에 노출되는 혼란스러운 조합을 차단하기 위함.

**프론트 처리**:
- `workflow_error` 수신 시 에러 메시지만 노출, 카드 영역은 그리지 않는다.
- 방어적 가드로 `event.type === "final"` 분기에서만 카드를 렌더링하도록 작성 권장 (백엔드가 `[]` 를 보장하지만 타입 좁히기 차원에서).

---

## 9. `types/sse-events.ts` 동기화

기존 정의 (`SseTypedEvent` 의 `final` 분기) 를 다음으로 확장:

```typescript
// types/sse-events.ts (변경 적용 후)

export type ServiceCard = {
  service_id: string;
  service_name: string | null;
  area_name: string | null;
  place_name: string | null;
  max_class_name: string | null;
  min_class_name: string | null;
  service_status: string | null;
  payment_type: string | null;
  target_info: string | null;
  receipt_start_dt: string | null;
  receipt_end_dt: string | null;
  service_url: string;
};

export type SseTypedEvent =
  | { type: "agent_start"; agent: AgentName }
  | { type: "tool_call"; tool: ToolName; args: unknown }
  | { type: "token"; delta: string }
  | { type: "done"; messageId: number }
  | {
      type: "final";
      message_id: number;
      answer: string;
      intent: "SQL_SEARCH" | "VECTOR_SEARCH" | "MAP" | "FALLBACK" | null;
      title: string | null;
      cache_hit: boolean;
      service_cards: ServiceCard[];
    }
  | {
      type: "workflow_error";
      message_id: number;
      answer: string;
      error: string;
      intent?: string;
      title?: string;
      // service_cards 도 함께 올 수 있으나 무시. §8 확정 후 갱신.
    }
  | { type: "error"; message: string };
```

`intent` / `title` / `cache_hit` 는 기존 `final` 분기에 누락돼 있던 필드도 있으므로 함께 보강 권장.

---

## 10. 마이그레이션 체크리스트

- [ ] `types/sse-events.ts` 의 `final` 분기에 `service_cards`, `intent`, `title`, `cache_hit` 추가
- [ ] `ServiceCard` 타입 export
- [ ] 채팅 메시지 컴포넌트 — `service_cards.length > 0` 일 때 카드 섹션 렌더링
- [ ] 카드 셀 컴포넌트 — null 필드 라인 생략 처리
- [ ] `service_status` 칩 색상 매핑
- [ ] `receipt_start_dt` / `receipt_end_dt` → `YYYY-MM-DD` 변환 유틸
- [ ] `service_url` 외부 링크 처리 (`target="_blank"`, `rel="noopener noreferrer"`)
- [ ] `answer` 본문은 카드와 별도 영역에 그대로 노출 (Markdown 미사용, 줄바꿈만 보존)
- [ ] `workflow_error` 분기에서 카드 렌더링 차단

---

## 11. 백엔드 변경 추적

- 정본 PR: `AGENT-7-retrieval` 브랜치
- 관련 파일:
  - `on-seoul-agent/schemas/state.py` — `AgentState.service_cards`
  - `on-seoul-agent/agents/answer_agent.py` — `_normalize()`, `answer()`
  - `on-seoul-agent/agents/nodes.py` — `CacheWriteNode` / `CacheCheckNode` payload 일관성
  - `on-seoul-agent/routers/chat.py` — final 페이로드 직렬화

스키마가 변경되면 본 문서와 `types/sse-events.ts` 를 함께 갱신할 것.
