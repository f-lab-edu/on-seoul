## Overview

![overview](./attachments/overview.png)

---
## AI Service (FastAPI)
> 멀티에이전트 오케스트레이션과 LLM 기반 추론을 담당한다. 자연어 답변에 포함될 데이터 조회(SQL, 벡터, 지도)는 에이전트가 호출하는 tool로 구축한다.

``` 
ai-service/
├── routers/
│   └── chat.py                    # POST /chat/stream — SSE 스트리밍 진입점
├── agents/
│   ├── graph.py                   # LangGraph 워크플로우 조립 (Phase 2)
│   ├── router_agent.py            # 사용자 의도 분류 (SQL_SEARCH / VECTOR_SEARCH / MAP / FALLBACK)
│   ├── sql_agent.py               # sql_search tool 호출 → 정형 데이터 조회
│   ├── vector_agent.py            # 질의 정제 → vector_search tool 호출 → 유사도 검색
│   └── answer_agent.py            # 조회 결과 → 자연어 답변 + 시설 카드 가공, URL 미존재 시 fallback 링크 처리
├── tools/
│   ├── sql_search.py              # PostgreSQL 정형 조회 (카테고리, 상태, 지역, 날짜 필터)
│   ├── vector_search.py           # pgvector 임베딩 유사도 검색
│   └── map_search.py              # earthdistance + cube 반경 검색, GeoJSON 반환
├── llm/
│   ├── client.py                  # LLM API 호출 추상화 (Gemini / GPT)
│   └── embedder.py                # 텍스트 → 벡터 변환 (임베딩 모델 호출)
├── schemas/
│   ├── state.py                   # AgentState — LangGraph 공유 상태 정의
│   ├── events.py                  # SSE 이벤트 타입 (agent_start, tool_call, token, done 등)
│   └── chat.py                    # ChatRequest / ChatResponse
├── core/
│   ├── config.py                  # 환경변수, DB 접속 정보, LLM API 키
│   └── database.py                # async SQLAlchemy 세션 (PostgreSQL 접속)
├── scripts/
│   └── embed_metadata.py          # 시설 메타데이터 → 임베딩 → pgvector 적재 (배치)
└── middleware/
    └── metrics.py                 # 응답시간 측정
```

### 주요 설계 사항
**pgvector 단일 인스턴스**: 별도 벡터DB(Qdrant) 대신 PostgreSQL pgvector 확장을 사용한다. 1000건 미만의 데이터에서 별도 인프라를 운영할 이유가 없고, SQL 조회와 벡터 검색이 동일 DB에서 가능하므로 복합 질의(벡터 → SQL 필터 조합)가 단순해진다.

**tool 3종 분리**: `sql_search`, `vector_search`, `map_search`는 각각 입력 파라미터와 반환 형태가 다르므로 별도 tool로 분리한다. Router Agent가 의도에 따라 적절한 tool을 선택하고, 결과는 Answer Agent에서 통합 가공한다.

**fallback_link를 별도 tool에서 제거**: URL 미존재 시 서울시 공공예약 메인 링크로 대체하는 로직은 Answer Agent 내부에서 조건 분기로 처리한다. 별도 tool로 분리할 만큼 복잡하지 않다.

---

## API Service (Spring Boot)
> 인증, 데이터 수집, 변경 이력 관리, 알림 발송, 대화 이력을 담당한다.

헥사고날 아키텍처(Ports & Adapters)를 적용한 Gradle 멀티모듈 구성이다.

```
on-seoul-api/
├── common/          # 전역 예외(ErrorCode, OnSeoulApiException), 공용 유틸
├── domain/          # 순수 도메인 (프레임워크 무의존)
│   ├── model/       # 도메인 POJO — User, ChatRoom, PublicServiceReservation 등
│   └── port/
│       ├── in/      # 유스케이스 인터페이스 — SocialLoginUseCase, RefreshTokenUseCase,
│       │            #                          CollectDatasetUseCase 등
│       └── out/     # 외부 의존성 인터페이스 — LoadUserPort, SaveUserPort,
│                    #                           TokenIssuerPort, RefreshTokenStorePort,
│                    #                           SeoulDatasetFetchPort, GeocodingPort 등
├── application/     # 유스케이스 구현체 (spring-tx만 의존)
│   └── service/     # SocialLoginService, RefreshTokenService, LogoutService,
│                    # CollectDatasetService, GeocodingService, UpsertService
├── adapter/         # 모든 어댑터 (Spring/JPA/Redis/WebClient 의존 OK)
│   ├── in/
│   │   ├── web/     # AuthController, CollectionController, GlobalExceptionHandler
│   │   ├── security/# SecurityConfig, OAuth2LoginSuccessHandler,
│   │   │            # JjwtTokenIssuer(TokenIssuerPort 구현), JwtAuthenticationFilter
│   │   └── scheduler/ # CollectionScheduler (@Scheduled → CollectDatasetUseCase)
│   └── out/
│       ├── persistence/ # JPA 엔티티 + Spring Data Repository + PersistenceAdapter
│       │                # (User, ChatRoom, PublicServiceReservation,
│       │                #  ApiSourceCatalog, CollectionHistory, ServiceChangeLog)
│       ├── redis/   # RefreshTokenRedisAdapter (RefreshTokenStorePort 구현)
│       ├── seoulapi/# SeoulOpenApiAdapter (SeoulDatasetFetchPort 구현)
│       └── kakao/   # KakaoGeocodingAdapter (GeocodingPort 구현)
└── app/             # 부트스트랩 — OnSeoulApiApplication.java + application.yml
```

### 의존 방향

```
adapter ──▶ application ──▶ domain
   │                          ▲
   └──────────────────────────┘  (adapter.out이 domain.port.out 구현)
```

ArchUnit으로 두 가지 경계를 자동 검증한다: `domain`→Spring/JPA 의존 금지, `application`→`adapter` 의존 금지.

### 주요 설계 사항

**인증 흐름**: OAuth2 Code Flow(Google/Kakao) → `OAuth2LoginSuccessHandler`가 `SocialLoginUseCase`를 호출 → users upsert + JWT 발급 + Redis에 RT 저장 → JSON 응답. API 인증은 `JwtAuthenticationFilter`가 Bearer 토큰을 파싱해 SecurityContext에 주입.

**Token Rotation**: `POST /auth/token/refresh` 호출 시 기존 Refresh Token을 Redis에서 즉시 삭제하고 새 토큰 쌍을 발급한다. 탈취된 RT는 1회 사용 후 무효화된다.

**데이터 수집 흐름**: `CollectionScheduler`가 트리거되면 `CollectDatasetUseCase`(전체 갱신 + staging 비교 + diff 감지) → DB Upsert 순서로 처리. `@Transactional`은 application 서비스에서 관리한다.

**ChatController의 SSE 릴레이 역할**: 프론트엔드의 SSE 요청을 받아 AI 서비스에 전달하고, AI 서비스의 스트리밍 응답을 `SseEmitter`로 그대로 릴레이한다. 스트림 완료 후 질문과 최종 응답을 `ChatRoom`/`ChatMessage`에 저장한다.