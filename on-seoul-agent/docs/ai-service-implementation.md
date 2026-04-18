# AI 서비스 구현 목록

FastAPI + LangGraph 기반 멀티에이전트 서비스 구현 순서.  
각 Phase는 독립적으로 동작 확인 후 다음 단계로 진행한다.

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

- [x] `schemas/chat.py` — `ChatRequest` (room_id, message_id, message 필수), `ChatResponse` (answer, intent, title 포함)
- [x] `schemas/state.py` — `AgentState` (room_id·message_id 기반, title_needed 플래그, 실행 trace, 검색 결과 공유)
- [x] `schemas/events.py` — SSE 이벤트 타입 (`agent_start`, `tool_call`, `token`, `done` 등)
- [x] `schemas/trace.py` — `chat_agent_traces` JSONB 페이로드 모델 정의 (intent, node 경로, 소요시간 등)
- [x] `scripts/ddl_chat_entities.sql` — `on_ai` DB 전용 DDL (`service_embeddings` pgvector, `chat_agent_traces`) 생성

### Phase 3. LLM 클라이언트

- [x] `llm/client.py` — LLM 공급자 추상화 (Gemini / GPT 전환 가능하도록)
- [x] `llm/embedder.py` — 텍스트 → 벡터 변환
- [x] `llm/generator.py` — 프롬프트 조립 → LLM 호출 → 텍스트 반환

검증: 단순 문장 생성 / 임베딩 단위 테스트

---

## Epic 2 — `feat/agent-core`
> Phase 4–5 | LangGraph 워크플로우 `graph.invoke()` 동작 확인

### Phase 4. Agent 구현 (Multi-DB 대응)

- [ ] `agents/router_agent.py` — 의도 분류 (`SQL_SEARCH` / `VECTOR_SEARCH` / `MAP` / `FALLBACK`)
- [ ] `agents/sql_agent.py` — `on_data_reader` 계정으로 `public_service_reservations` 조회
- [ ] `agents/vector_agent.py` — `on_ai_app` 계정으로 `service_embeddings` 유사도 검색
- [ ] `agents/answer_agent.py` — 결과 요약 및 시설 카드 가공. `is_title_generated`가 false인 경우 제목 요약 생성 로직 포함.
- [ ] `agents/trace_node.py` — 실행 완료 후 `on_ai`에 `chat_agent_traces`를 저장하는 전용 노드

### Phase 5. LangGraph 워크플로우 구축

- [ ] `agents/graph.py` — 노드 등록 및 조건부 엣지(Conditional Edges) 연결
- [ ] 모든 경로의 끝에 `trace_node`를 배치하여 실행 메타데이터 자동 저장 구조화
- [ ] 워크플로우 단독 실행 테스트 (DB 연동 없이 Mocking으로 흐름 검증)

---

## Epic 3 — `feat/tools-endpoint`
> Phase 6–7 | `/chat/stream`, `/notification/template` E2E 동작 확인

### Phase 6. Tools (Domain Logic)

- [ ] `tools/sql_search.py` — 카테고리/지역/날짜 필터를 SQL Query로 변환 및 실행
- [ ] `tools/vector_search.py` — 질의 임베딩 생성 및 pgvector 유사도 검색 연동
- [ ] `tools/map_search.py` — `coord_x/y` 기준 반경 검색 (PostgreSQL earthdistance/cube 활용)

### Phase 7. API 엔드포인트

- [ ] `routers/chat.py` — `POST /chat/stream` (room_id/message_id 수신 → Graph 실행 → SSE 스트리밍)
- [ ] `routers/notification.py` — `POST /notification/template` (변경 이력 기반 알림 메시지 생성)
- [ ] `main.py`에 라우터 등록 및 전역 에러 핸들러 구성

---

## Epic 4 — `feat/infra-polish`
> Phase 8–10 | DB 연동, 배치 스크립트, 전체 테스트 통과

### Phase 8. 인프라 및 다중 DB 연동

- [ ] `core/database.py` — 두 개의 Engine/Session 정의 (`on_ai_app` CRUD용, `on_data_reader` SELECT용)
- [ ] Redis 연결 설정 (Agent 응답 캐싱 및 Rate Limiting)
- [ ] `scripts/embed_metadata.py` — `on_data`의 시설 정보를 임베딩하여 `on_ai`로 이관하는 배치 스크립트

### Phase 9. 관측가능성 (Observability)

- [ ] `middleware/metrics.py` — 요청별 지연시간 및 토큰 사용량 측정
- [ ] `chat_agent_traces` 저장 데이터 검증 (LangGraph의 `checkpoint`와 trace 정합성)

### Phase 10. 통합 테스트 및 최적화

- [ ] `pytest-asyncio` 기반 각 Agent 및 Graph 통합 테스트
- [ ] `on_data_reader` 권한 제한(SELECT only) 검증 테스트
- [ ] `/chat/stream` 시나리오별 E2E 테스트 (첫 질문 시 제목 생성 여부 포함)

---

## 주요 설계 준수 사항

1. **서비스 격리**: AI 서비스는 `on_data` DB에 절대 쓰기(INSERT/UPDATE)를 수행하지 않는다. (SELECT 권한만 사용)
2. **Trace 관리**: 에이전트 실행 메타데이터는 `on_ai` DB의 `chat_agent_traces` 테이블에 JSONB 형태로 저장하며, API 서비스의 `message_id`를 논리 참조한다.
3. **제목 생성**: 첫 메시지에 대한 제목 생성은 `Answer Agent` 단계에서 수행하거나 별도 노드에서 처리하여 응답과 함께 전달한다.

---

## 참고

- LLM 공급자 미확정 → `llm/client.py` 추상화 레이어에서 공급자 교체 가능하도록 설계
- 관리 UI 없음 → Swagger UI (`/docs`) / Postman으로 대체
- 모니터링(Prometheus, Grafana) 연계는 Phase 9 이후 별도 문서화
