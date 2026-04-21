# AI 서비스 구현 목록

FastAPI + LangChain 기반 멀티에이전트 서비스 구현 순서.
각 Phase는 독립적으로 동작 확인 후 다음 단계로 진행한다.

> **MVP 방침**: MVP는 LangChain(LCEL) 으로 구현하여 체이닝·라우팅·프롬프트 구성을 빠르게 검증한다.
> 워크플로우가 안정화된 이후(Epic 5 후반) 동일 인터페이스를 유지하며 LangGraph로 전환한다.

브랜치는 아래 Epic 단위로 생성한다. Phase는 각 Epic 안의 작업 체크리스트다.

---

## Epic 1 — `feat/foundation`
> Phase 1–3 | 앱 기동, 스키마 정의, LLM 호출 단위 테스트 통과

### Phase 1. 프로젝트 초기 구성

- [x] 디렉토리 구조 생성 (`routers/`, `agents/`, `tools/`, `llm/`, `schemas/`, `core/`, `middleware/`, `scripts/`)
- [x] `main.py` → FastAPI 앱 진입점으로 전환 (`app = FastAPI()`, uvicorn 실행)
- [x] `core/config.py` — `pydantic-settings` 기반 환경변수 관리 (`.env` 로드)
- [x] `core/database.py` — async SQLAlchemy 세션 (PostgreSQL 접속)
- [x] 로깅 설정

검증: `uvicorn main:app --reload` 실행 후 `/docs` 접근 확인

### Phase 2. 스키마 및 DB 정의

- [x] `schemas/chat.py` — `ChatRequest` (room_id, message_id, message 필수), `ChatResponse` (message_id, answer, intent, title 포함)
- [x] `schemas/state.py` — `AgentState` (room_id·message_id 기반, title_needed 플래그, 실행 trace, 검색 결과 공유)
- [x] `schemas/events.py` — SSE 이벤트 타입 (`agent_start`, `tool_call`, `token`, `done` 등)
- [x] `schemas/trace.py` — `chat_agent_traces` JSONB 페이로드 모델 정의 (intent, node 경로, 소요시간 등)
- [x] `scripts/ddl_chat_entities.sql` — `on_ai` DB 전용 DDL (`service_embeddings` pgvector, `chat_agent_traces`) 생성

### Phase 3. LLM 클라이언트

- [x] `llm/client.py` — LLM 벤더 추상화 (Gemini / GPT 전환 가능하도록)
- [x] `llm/embedder.py` — 텍스트 → 벡터 변환
- [x] `llm/generator.py` — 프롬프트 조립 → LLM 호출 → 텍스트 반환

검증: 단순 문장 생성 / 임베딩 단위 테스트

---

## Epic 2 — `feat/agent-core`
> Phase 4–6 | DB 연동 · LangChain Agent 구현 · 워크플로우 구축

### Phase 4. 다중 DB 연동 + 개발용 seed 임베딩

- [ ] `core/database.py` — 두 개의 Engine/Session 정의 (`on_ai_app` CRUD용, `on_data_reader` SELECT용)
- [ ] 세션 DI (`Depends`) 로 라우터/툴에서 주입하도록 구성 
- [ ] `on_data_reader` 권한 제한(SELECT only) 검증 — `INSERT`/`UPDATE` 시도 시 권한 오류 발생 확인
- [ ] `on_ai` DDL 적용 스크립트 실행 및 연결 smoke test
- [ ] `scripts/embed_metadata.py` 최소 구현 — 100 건 seed 데이터 적재 (Agent 개발 중 `service_embeddings` 조회 가능 상태 확보)

### Phase 5. LangChain Agent 구현

- [ ] `agents/router_agent.py` — LCEL 기반 의도 분류 체인 (`SQL_SEARCH` / `VECTOR_SEARCH` / `MAP` / `FALLBACK`)
- [ ] `agents/sql_agent.py` — `on_data_reader` 계정으로 `public_service_reservations` 조회
- [ ] `agents/vector_agent.py` — `on_ai_app` 계정으로 `service_embeddings` 유사도 검색
- [ ] `agents/answer_agent.py` — 결과 요약 및 시설 카드 가공. `is_title_generated`가 false인 경우 제목 요약 생성 로직 포함
- [ ] 각 Agent 단위 테스트 (Mock LLM / Mock DB) 구현 

### Phase 6. LangChain 워크플로우 구축

- [ ] `agents/workflow.py` — `RunnableBranch` / `RunnableLambda` 로 Router → (SQL | Vector | Map | Fallback) → Answer 분기 조립
- [ ] 워크플로우 입출력을 `AgentState` 기반으로 통일 (LangGraph 전환 시 교체 용이성 확보)
- [ ] 실행 완료 훅에서 `chat_agent_traces`(intent, 경로, 소요시간 등) 적재하도록 구성
- [ ] 워크플로우 단독 실행 테스트 (DB/LLM Mocking으로 전체 흐름 검증)

---

## Epic 3 — `feat/retrieval`
> Phase 7–10 | 임베딩 파이프라인 · 검색 전략 · 파라미터 튜닝 · 품질 평가

### Phase 7. 임베딩 파이프라인

- [ ] `scripts/embed_metadata.py` 전량 적재 — `on_data.public_service_reservations` 전체 대상 배치 실행
- [ ] 임베딩 문서 증강 — 시설명·카테고리·지역·기간·설명 등 필드를 조합한 문서 구성 전략 결정
- [ ] 청킹 전략 검토 — 단일 문서 vs 필드별 분리 임베딩 비교
- [ ] 증분 업데이트 처리 — 신규·변경 시설만 재임베딩하는 upsert 로직 구현
- [ ] HNSW 인덱스 (`embedding vector_cosine_ops`) — 데이터 적재 후 추가 예정 (현재 주석 처리)

### Phase 8. 검색 전략

- [ ] pre-filter 여부 결정 — 카테고리/지역/상태 필터를 벡터 검색 전(WHERE 절) vs 후(후처리) 중 선택 및 구현
- [ ] 하이브리드 검색 검토 — PostgreSQL full-text search(tsvector) + pgvector 결합 방식 실험
- [ ] `tools/vector_search.py` — 결정된 검색 전략 반영 (pre-filter + top-k + 하이브리드 여부)

### Phase 9. 파라미터 튜닝

- [ ] HNSW 인덱스 적용 및 파라미터 튜닝 (`m`, `ef_construction`, `ef_search`)
- [ ] top-k 및 score threshold 실험 — 서비스 응답 품질 기준으로 적정값 결정
- [ ] 재순위화(카테고리·지역·상태 부스트) 또는 MMR 로 중복 완화 여부 판단

### Phase 10. 검색 품질 평가

- [ ] 샘플 질의셋 구성 (카테고리·지역·키워드별 대표 질의 20–30건)
- [ ] Precision@k / Recall@k / MRR 측정
- [ ] Phase 8·9 전략 조합별 결과 비교 및 최종 전략 확정

---

## Epic 4 — `feat/tools-endpoint`
> Phase 11–12 | `/chat/stream`, `/notification/template` E2E 동작 확인

### Phase 11. Tools (Domain Logic)

- [ ] `tools/sql_search.py` — 카테고리/지역/날짜 필터를 SQL Query로 변환 및 실행
- [ ] `tools/map_search.py` — `coord_x/y` 기준 반경 검색 (PostgreSQL earthdistance/cube 활용)
- [ ] `tools/vector_search.py` 와 Agent 연동 검증 (Epic 3 산출물 재사용)

### Phase 12. API 엔드포인트

- [ ] `routers/chat.py` — `POST /chat/stream` (room_id/message_id 수신 → LangChain 워크플로우 실행 → SSE 스트리밍)
- [ ] `routers/notification.py` — `POST /notification/template` (변경 이력 기반 알림 메시지 생성)
- [ ] `main.py`에 라우터 등록 및 전역 에러 핸들러 구성

---

## Epic 5 — `feat/infra-polish`
> Phase 13–15 | 관측가능성, 통합 테스트, LangGraph 전환

### Phase 13. 관측가능성 (Observability)

- [ ] Redis 연결 설정 (Agent 응답 캐싱 및 Rate Limiting)
- [ ] `middleware/metrics.py` — 요청별 지연시간 및 토큰 사용량 측정
- [ ] `chat_agent_traces` 저장 데이터 검증 (라우팅 결과/도구 호출/응답 스니펫 정합성)

### Phase 14. 통합 테스트 및 최적화

- [ ] `pytest-asyncio` 기반 각 Agent 및 워크플로우 통합 테스트
- [ ] `on_data_reader` 권한 제한(SELECT only) 회귀 테스트
- [ ] `/chat/stream` 시나리오별 E2E 테스트 (첫 질문 시 제목 생성 여부 포함)

### Phase 15. LangGraph 전환 (Post-MVP)

- [ ] `agents/graph.py` — LangChain `RunnableBranch` 구조를 LangGraph `StateGraph`로 재구성
- [ ] 노드 등록 및 조건부 엣지(Conditional Edges)로 라우팅 교체
- [ ] `trace_node` 를 그래프 종단 노드로 분리하고 `checkpoint` 와의 정합성 확인
- [ ] 기존 LangChain 워크플로우와 동일 입출력 유지 (회귀 테스트 통과가 전환 기준)
- [ ] 자기 교정(Self-Correction) Agent 구현 - 그래프의 'Cycle' 기능을 활용하여 답변이 부실하면 다시 검색 노드로 돌아가는 루프를 구현

---

## 주요 설계 준수 사항

1. **서비스 격리**: AI 서비스는 `on_data` DB에 절대 쓰기(INSERT/UPDATE)를 수행하지 않는다. (SELECT 권한만 사용)
2. **Trace 관리**: 에이전트 실행 메타데이터는 `on_ai` DB의 `chat_agent_traces` 테이블에 JSONB 형태로 저장하며, API 서비스의 `message_id`를 논리 참조한다.
3. **제목 생성**: 첫 메시지에 대한 제목 생성은 `Answer Agent` 단계에서 수행하거나 별도 노드에서 처리하여 응답과 함께 전달한다.
4. **프레임워크 전환 용이성**: MVP는 LangChain 으로 구성하지만, `AgentState` 기반 입출력 규약을 유지하여 이후 LangGraph 전환 시 회귀 비용을 최소화한다.

---

## 참고

- LLM 벤더 미확정 → `llm/client.py` 추상화 레이어에서 벤더 교체 가능하도록 설계
- 관리 UI 없음 → Swagger UI (`/docs`) / Postman으로 대체
- 모니터링(Prometheus, Grafana) 연계는 Phase 13 이후 별도 문서화
