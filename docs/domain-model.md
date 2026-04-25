# 도메인 모델 / 애그리거트 다이어그램

> **대상:** on-seoul 전체 (API Service + AI Service)
> **작성일:** 2026-04-25

---

## 1. 바운디드 컨텍스트 개요

on-seoul은 네 개의 바운디드 컨텍스트로 구성된다.

| 컨텍스트 | 서비스 | 핵심 모델 |
|---|---|---|
| 인증 (Auth) | on-seoul-api | User |
| 수집 (Collection) | on-seoul-api | ApiSourceCatalog, PublicServiceReservation, CollectionHistory |
| 채팅 (Chat) | on-seoul-api | ChatRoom, ChatMessage |
| AI 에이전트 (AI Agent) | on-seoul-agent (외부, on_ai DB) | AgentState, SearchResult, ChatAgentTrace |

```mermaid
graph LR
    subgraph AUTH["인증 도메인"]
        direction TB
        User["User\n(AR)"]
    end

    subgraph COLLECTION["수집 도메인"]
        direction TB
        ApiSourceCatalog["ApiSourceCatalog\n(AR)"]
        PublicServiceReservation["PublicServiceReservation\n(AR)"]
        CollectionHistory["CollectionHistory\n(AR)"]
        ServiceChangeLog["ServiceChangeLog\n(Entity)"]
        CollectionHistory --o ServiceChangeLog
    end

    subgraph CHAT["채팅 도메인"]
        direction TB
        ChatRoom["ChatRoom\n(AR)"]
        ChatMessage["ChatMessage\n(Entity)"]
        ChatRoom --o ChatMessage
    end

    subgraph AI["AI 에이전트 도메인 (외부 서비스, on_ai DB)"]
        direction TB
        AgentWorkflow["Router → SQL/Vector/Map\n→ Answer"]
        ChatAgentTrace["ChatAgentTrace\n(AR)"]
    end

    ChatRoom -. "userId (ID 참조)" .-> User
    CollectionHistory -. "sourceId (ID 참조)" .-> ApiSourceCatalog
    ServiceChangeLog -. "serviceId (자연키 참조)" .-> PublicServiceReservation
    COLLECTION -. "SSE 스트리밍 / 알림 템플릿" .-> AI
    ChatAgentTrace -. "messageId\n(논리 참조, DB 다름)" .-> ChatMessage

    style ChatAgentTrace fill:#90EE90,stroke:#228B22
    CHAT -. "SSE 릴레이" .-> AI

    style User fill:#90EE90,stroke:#228B22
    style ApiSourceCatalog fill:#90EE90,stroke:#228B22
    style PublicServiceReservation fill:#90EE90,stroke:#228B22
    style CollectionHistory fill:#90EE90,stroke:#228B22
    style ChatRoom fill:#90EE90,stroke:#228B22
```

> **AR** = Aggregate Root (애그리거트 루트). 초록색으로 표시.
> 점선 = 바운더리를 넘는 ID 참조 (객체 참조 금지, ID만 보관).

---

## 2. 인증 도메인 (Auth)

소셜 로그인(OAuth2)으로 가입/로그인한 사용자를 관리한다. `User`가 유일한 애그리거트 루트이며, 채팅 도메인에서 `userId`로 참조된다.

```mermaid
classDiagram
    class User {
        <<AggregateRoot>>
        +Long id
        +String provider
        +String providerId
        +String email
        +String nickname
        +UserStatus status
        +OffsetDateTime createdAt
        +OffsetDateTime updatedAt
        --
        +create(SocialLoginCommand) User
        +updateProfile(email, nickname)
    }

    class UserStatus {
        <<Enumeration>>
        ACTIVE
        SUSPENDED
        DELETED
    }

    User --> UserStatus
```

**불변 조건:**
- `provider` + `providerId` 조합은 전역 유일 (복합 자연키).
- `status = SUSPENDED` 또는 `DELETED`이면 토큰 발급 및 갱신 거부.

---

## 3. 수집 도메인 (Collection)

서울 열린데이터 광장 Open API에서 공공서비스 예약 데이터를 수집·정제한다.
세 개의 애그리거트로 구성되며, 각각 독립적인 생명주기를 가진다.

```mermaid
classDiagram
    direction TB

    class ApiSourceCatalog {
        <<AggregateRoot>>
        +Long id
        +String datasetId
        +String datasetName
        +String datasetUrl
        +String apiServicePath
        +boolean active
        +String tags
        +LocalDate metaUpdatedAt
        +LocalDateTime dataUpdatedAt
    }

    class CollectionHistory {
        <<AggregateRoot>>
        +Long id
        +Long sourceId
        +LocalDateTime collectedAt
        +CollectionStatus status
        +int totalFetched
        +int newCount
        +int updatedCount
        +int deletedCount
        +Integer durationMs
        +String errorMessage
        --
        +create(sourceId) CollectionHistory
        +complete(total, new, updated, deleted, durationMs)
        +fail(errorMessage, durationMs)
        +partial(...)
    }

    class ServiceChangeLog {
        <<Entity>>
        +Long id
        +String serviceId
        +Long collectionId
        +ChangeType changeType
        +String fieldName
        +String oldValue
        +String newValue
        +LocalDateTime changedAt
    }

    class CollectionStatus {
        <<Enumeration>>
        SUCCESS
        FAILED
        PARTIAL
    }

    class ChangeType {
        <<Enumeration>>
        NEW
        UPDATED
        DELETED
    }

    class PublicServiceReservation {
        <<AggregateRoot>>
        +Long id
        +String serviceId
        +String serviceGubun
        +String maxClassName
        +String minClassName
        +String serviceName
        +String serviceStatus
        +String prevServiceStatus
        +String paymentType
        +String targetInfo
        +String serviceUrl
        +String placeName
        +String areaName
        +BigDecimal coordX
        +BigDecimal coordY
        +LocalDateTime receiptStartDt
        +LocalDateTime receiptEndDt
        +LocalDateTime deletedAt
        --
        +update(updated)
        +softDelete()
        +updateCoords(x, y)
    }

    CollectionHistory --> CollectionStatus
    CollectionHistory "1" *-- "0..*" ServiceChangeLog : 수집 실행 시 생성
    ServiceChangeLog --> ChangeType
    CollectionHistory "0..*" ..> ApiSourceCatalog : sourceId (ID 참조)
    ServiceChangeLog "0..*" ..> PublicServiceReservation : serviceId 자연키 참조
```

**애그리거트별 역할:**

| 애그리거트 | 생명주기 | 불변 조건 |
|---|---|---|
| `ApiSourceCatalog` | 운영자 설정 시 생성, `active=false`로 비활성화 | `datasetId` 전역 유일 |
| `CollectionHistory` | 수집 실행마다 1개 생성, 종료 시 한 번만 결과 기록 | `durationMs != null`이면 결과 재기록 불가 |
| `PublicServiceReservation` | 최초 수집 시 INSERT, 이후 upsert. soft delete | `serviceId`(서울 API 자연키) 전역 유일 |
| `ServiceChangeLog` (CollectionHistory 내부) | CollectionHistory와 동일한 트랜잭션에서 생성 | 생성 후 불변 (audit log) |

---

## 4. 채팅 도메인 (Chat)

사용자와 AI 에이전트 간의 대화 세션과 메시지 이력을 관리한다. `ChatRoom`이 애그리거트 루트이며 `ChatMessage`의 생명주기를 통제한다.

```mermaid
classDiagram
    class ChatRoom {
        <<AggregateRoot>>
        +Long id
        +Long userId
        +String title
        +boolean titleGenerated
        +OffsetDateTime createdAt
        +OffsetDateTime updatedAt
        --
        +create(userId, title) ChatRoom
        +updateTitle(title)
    }

    class ChatMessage {
        <<Entity>>
        +Long id
        +Long roomId
        +Long seq
        +ChatMessageRole role
        +String content
        +OffsetDateTime createdAt
        --
        +create(roomId, seq, role, content) ChatMessage
    }

    class ChatMessageRole {
        <<Enumeration>>
        USER
        ASSISTANT
    }

    ChatRoom "1" *-- "0..*" ChatMessage : seq 순서로 조회
    ChatMessage --> ChatMessageRole
    ChatRoom ..> User : userId (ID 참조, 인증 도메인)
```

**불변 조건:**
- `ChatMessage.seq`는 동일 Room 내에서 단조 증가 (메시지 순서 보장).
- `ChatRoom`은 특정 `userId`에 귀속되며, 다른 사용자의 Room에 메시지 추가 불가.
- `title`은 AI가 자동 생성하거나 사용자가 직접 수정할 수 있다 (`titleGenerated` 플래그로 구분).

---

## 5. AI 에이전트 도메인 (외부 바운디드 컨텍스트)

`on-seoul-agent` (Python FastAPI)가 담당하는 별도 서비스다. 전용 DB(`on_ai`)를 가지며, API Service(`on-seoul-api`)와는 포트 인터페이스로만 통신한다.

### ChatAgentTrace 애그리거트

LangGraph 에이전트 실행 메타데이터를 저장한다. `ASSISTANT` 역할의 메시지에만 생성된다.

```mermaid
classDiagram
    class ChatAgentTrace {
        <<AggregateRoot>>
        +Long id
        +Long messageId
        +JsonB trace
        +OffsetDateTime createdAt
    }

    class AgentTracePayload {
        <<ValueObject>>
        +List~String~ nodePath
        +List~ToolCallResult~ toolCalls
        +Map~String,Long~ durationMs
    }

    ChatAgentTrace *-- AgentTracePayload : trace (JSONB)
    ChatAgentTrace ..> ChatMessage : messageId\n논리 참조 (DB 다름, FK 없음)
```

**설계 결정:**

| 항목 | 내용 |
|---|---|
| DB | `on_ai` (on-seoul-agent 전용). `on_data`의 `chat_messages`와 물리 FK 불가 |
| `messageId` 참조 | 논리 참조(자연키). DB가 달라 JOIN 불가, 조회는 API Service가 message_id로 직접 요청 |
| `trace` 컬럼 | JSONB. node 경로·tool call 결과·소요 시간 등 실행 메타. 스키마 유연성이 필요해 JSONB 선택 |
| 생성 조건 | `ChatMessageRole = ASSISTANT`인 메시지에만 생성 (사용자 발화에는 생성 안 함) |
| 불변성 | 생성 후 수정 없음 (실행 결과 audit log) |

### 서비스 경계 및 포트 인터페이스

```mermaid
graph LR
    subgraph "on-seoul-api (Java, on_data DB)"
        ChatUseCase["Chat UseCase"]
        AiStreamPort["AiAgentStreamPort\n(Outbound Port)"]
        NotifyPort["AiAgentNotificationTemplatePort\n(Outbound Port)"]
        ChatUseCase --> AiStreamPort
        ChatUseCase --> NotifyPort
    end

    subgraph "on-seoul-agent (Python, on_ai DB)"
        Router["Router Agent\n의도 분류"]
        SQL["SQL Agent\n정형 조회"]
        Vector["Vector Agent\n유사도 검색"]
        Map["Map Search\n반경 검색"]
        Answer["Answer Agent\n자연어 답변 생성"]
        TraceStore["ChatAgentTrace\n(on_ai DB 저장)"]

        Router --> SQL
        Router --> Vector
        Router --> Map
        SQL --> Answer
        Vector --> Answer
        Answer --> TraceStore
    end

    AiStreamPort -. "POST /chat/stream\nSSE" .-> Router
    NotifyPort -. "POST /notification/template" .-> Answer
```

**인터페이스 계약:**

| 포트 | 방향 | 프로토콜 | 비고 |
|---|---|---|---|
| `AiAgentStreamPort` | API → AI | HTTP SSE (astream_events) | 질문 → 토큰 스트림. 완료 후 messageId 포함 |
| `AiAgentNotificationTemplatePort` | API → AI | HTTP POST/Response | 변경 이벤트 → 알림 메시지 생성 |

---

## 6. 크로스 도메인 참조 요약

```mermaid
graph LR
    ChatRoom["ChatRoom\n(채팅 도메인)"] -. "userId" .-> User["User\n(인증 도메인)"]
    CollectionHistory["CollectionHistory\n(수집 도메인)"] -. "sourceId" .-> ApiSourceCatalog["ApiSourceCatalog\n(수집 도메인)"]
    ServiceChangeLog["ServiceChangeLog\n(수집 도메인)"] -. "serviceId (자연키)" .-> PublicServiceReservation["PublicServiceReservation\n(수집 도메인)"]
```

| 참조하는 쪽 | 참조 대상 | 방식 | 이유 |
|---|---|---|---|
| `ChatRoom.userId` | `User` | ID 참조 | 채팅 ↔ 인증 도메인 경계 분리 |
| `CollectionHistory.sourceId` | `ApiSourceCatalog` | ID 참조 | 수집 이력이 소스 설정에 느슨하게 의존 |
| `ServiceChangeLog.serviceId` | `PublicServiceReservation` | 자연키(String) | 서울 API의 고유 ID. 도메인 객체 없이 참조 가능 |
| `ChatAgentTrace.messageId` | `ChatMessage` | 논리 참조 (DB 다름) | on_ai ↔ on_data 크로스 DB. 물리 FK 불가, GIN 인덱스로 조회 |

> **원칙:** 바운디드 컨텍스트 경계를 넘는 참조는 반드시 ID(또는 자연키)로만 한다. 객체 그래프를 경계 밖으로 노출하지 않는다.
