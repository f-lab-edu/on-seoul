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
│   ├── question_search.py         # 예상 질문 임베딩 검색, service_id별 dedup (Track C)
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
│   ├── database.py                # async SQLAlchemy 세션 (PostgreSQL 접속)
│   └── rrf.py                     # 가중 RRF (reciprocal_rank_fusion)
├── scripts/
│   └── embed_metadata.py          # 시설 메타데이터 → 임베딩 → pgvector 적재 (배치)
└── middleware/
    └── metrics.py                 # 응답시간 측정
```

### 주요 설계 사항
**pgvector 단일 인스턴스**: 별도 벡터DB(Qdrant) 대신 PostgreSQL pgvector 확장을 사용한다. 1000건 미만의 데이터에서 별도 인프라를 운영할 이유가 없고, SQL 조회와 벡터 검색이 동일 DB에서 가능하므로 복합 질의(벡터 → SQL 필터 조합)가 단순해진다.

**tool 3종 분리**: `sql_search`, `vector_search`, `map_search`는 각각 입력 파라미터와 반환 형태가 다르므로 별도 tool로 분리한다. Router Agent가 의도에 따라 적절한 tool을 선택하고, 결과는 Answer Agent에서 통합 가공한다.

**fallback_link를 별도 tool에서 제거**: URL 미존재 시 서울시 공공예약 메인 링크로 대체하는 로직은 Answer Agent 내부에서 조건 분기로 처리한다. 별도 tool로 분리할 만큼 복잡하지 않다.

**Triple-Track 임베딩 + RRF**: `service_embeddings` 통합 테이블에 Track A(identity) / Track B(summary) / Track C(question) 세 종류 임베딩을 적재하고, 4채널(Track A/B/C + BM25) RRF(Reciprocal Rank Fusion)로 결합한다. Phase 1은 비가중치 baseline. Phase 3에서 `vector_sub_intent` 분류 정확도 ≥ 80% 검증 후 가중치 활성화 예정.

---

## API Service (Spring Boot)
> 인증, 데이터 수집, 변경 이력 관리, 알림 발송, 대화 이력을 담당한다.

헥사고날 아키텍처(Ports & Adapters)를 **Bounded Context별 수직 분할**한 Gradle 멀티모듈 구성이다.  
각 BC(user / chat / collection / notification)가 자신의 `domain` / `application` / `port` / `adapter`를 **독립적으로 보유**하는 수직 구조를 형성된다. BC 간 결합은 `bootstrap`에서만 조립하며, 도메인 경계가 모듈 경계와 일치한다.

```
on-seoul-api/
├── common/         # 전역 예외(ErrorCode, OnSeoulApiException), 공용 유틸. 모든 모듈 의존 가능
├── user/           # 인증 BC — OAuth2 로그인, JWT(Access/Refresh), 사용자 프로필
├── chat/           # 채팅 BC — ChatRoom/ChatMessage, AI 서비스 SSE 릴레이
├── collection/     # 수집 BC — 서울 Open API 수집, ServiceChangeLog, 임베딩 동기화
├── notification/   # 알림 BC — 구독, Knock 발송(SMS/이메일), 템플릿 생성, 시점 트리거
├── bootstrap/      # Web API 부트스트랩 — OnSeoulApiApplication + SecurityConfig 조합
├── docs/           # ADR, 구현 목록
└── schema/         # DB 마이그레이션 스크립트
```

각 BC 모듈 내부는 동일한 수직 구조를 따른다.

```
<bc>/
├── domain/         # 순수 도메인 (프레임워크 무의존)
├── application/    # 유스케이스 구현체 (spring-tx만 허용)
├── port/
│   ├── in/         # 인바운드 포트 — UseCase 인터페이스
│   └── out/        # 아웃바운드 포트 — LoadXxxPort, SaveXxxPort 등
└── adapter/
    ├── in/         # REST 컨트롤러, Security 필터, @Scheduled 스케줄러
    └── out/        # JPA, Redis, WebClient(서울 Open API·AI 서비스·Knock) 어댑터
```

### 모듈 의존 관계

```
bootstrap
  ├── user         ──▶ common
  ├── chat         ──▶ common, collection
  ├── collection   ──▶ common
  └── notification ──▶ common
```

### 주요 설계 사항

**인증**: OAuth2 소셜 로그인으로 사용자를 식별하고 JWT(Access/Refresh)를 발급한다. Refresh Token은 Redis에 저장해 회전(rotation)·강제 만료를 제어한다. (user BC)

**데이터 수집**: 스케줄러가 주기적으로 서울 Open API를 수집하고, 기존 데이터와 비교해 변경을 감지·기록한다. (collection BC)

**챗봇 SSE 릴레이 & 책임 분리**: AI 서비스의 스트리밍 응답을 프론트엔드로 릴레이하며, 클라이언트가 끊겨도 최종 답변을 저장한다. 대화 이력과 추론 trace는 저장 책임을 분리한다. (chat BC)

**모듈간 의존**: adapter → application → domain 단방향으로 의존하도록 설계한다. ArchUnit으로 검증한다.