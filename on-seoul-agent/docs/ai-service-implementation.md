# AI 서비스 구현 목록

FastAPI + LangGraph 기반 멀티에이전트 서비스 구현 순서.  
각 Phase는 독립적으로 동작 확인 후 다음 단계로 진행한다.

브랜치는 아래 Epic 단위로 생성한다. Phase는 각 Epic 안의 작업 체크리스트다.

---

## Epic 1 — `feat/foundation`
> Phase 1–3 | 앱 기동, 스키마 정의, LLM 호출 단위 테스트 통과

### Phase 1. 프로젝트 초기 구성

- [ ] 디렉토리 구조 생성 (`routers/`, `agents/`, `tools/`, `llm/`, `schemas/`, `core/`, `middleware/`, `scripts/`)
- [ ] `main.py` → FastAPI 앱 진입점으로 전환 (`app = FastAPI()`, uvicorn 실행)
- [ ] `core/config.py` — `pydantic-settings` 기반 환경변수 관리 (`.env` 로드)
- [ ] `core/database.py` — async SQLAlchemy 세션 (PostgreSQL 접속)
- [ ] 로깅 설정

검증: `uvicorn main:app --reload` 실행 후 `/docs` 접근 확인

### Phase 2. 스키마 정의

- [ ] `schemas/chat.py` — `ChatRequest` / `ChatResponse`
- [ ] `schemas/state.py` — `AgentState` (LangGraph 노드 간 공유 상태)
- [ ] `schemas/events.py` — SSE 이벤트 타입 (`agent_start`, `tool_call`, `token`, `done` 등)

### Phase 3. LLM 클라이언트

- [ ] `llm/client.py` — LLM 공급자 추상화 (Claude / GPT 전환 가능하도록)
- [ ] `llm/embedder.py` — 텍스트 → 벡터 변환
- [ ] `llm/generator.py` — 프롬프트 조립 → LLM 호출 → 텍스트 반환

검증: 단순 문장 생성 / 임베딩 단위 테스트

---

## Epic 2 — `feat/agent-core`
> Phase 4–5 | LangGraph 워크플로우 `graph.invoke()` 동작 확인

### Phase 4. Agent 구현

의존 순서: `router → sql / vector → answer`

- [ ] `agents/router_agent.py` — LLM 기반 의도 분류 (`SQL_SEARCH` / `VECTOR_SEARCH` / `MAP` / `FALLBACK`)
- [ ] `agents/sql_agent.py` — `sql_search` 도구 호출 → 정형 데이터 조회
- [ ] `agents/vector_agent.py` — 질의 정제 → `vector_search` 도구 호출 → 유사도 검색
- [ ] `agents/answer_agent.py` — 조회 결과 → 자연어 요약 + 시설 카드 가공, URL 미존재 시 fallback 링크 처리

### Phase 5. LangGraph 워크플로우 구축

- [ ] `agents/graph.py` — 노드 등록 및 엣지 연결
- [ ] `router_agent` 결과에 따른 조건 분기 (`SQL_SEARCH` / `VECTOR_SEARCH` / `MAP` / `FALLBACK`)
- [ ] 워크플로우 단독 실행 테스트 (FastAPI 없이 `graph.invoke({...})` 확인)

---

## Epic 3 — `feat/tools-endpoint`
> Phase 6–7 | `/chat/stream`, `/notification/template` Swagger에서 E2E 동작 확인

### Phase 6. Tools

- [ ] `tools/sql_search.py` — PostgreSQL 정형 조회 (카테고리, 상태, 지역, 날짜 필터)
- [ ] `tools/vector_search.py` — pgvector 유사도 검색
- [ ] `tools/map_search.py` — earthdistance + cube 반경 검색, GeoJSON 반환

### Phase 7. API 엔드포인트

- [ ] `routers/chat.py` — `POST /chat/stream` (요청 수신 → `graph.invoke` → SSE 스트리밍 응답)
- [ ] `routers/notification.py` — `POST /notification/template` (상태 변경 정보 수신 → LLM 알림 메시지 생성 → 반환)
- [ ] 라우터를 `main.py`에 등록

검증: Swagger UI에서 `/chat/stream` 질의 요청 → 자연어 응답 확인, `/notification/template` 요청 → 알림 메시지 반환 확인

---

## Epic 4 — `feat/infra-polish`
> Phase 8–10 | DB 연동, Redis 캐시, 미들웨어, 전체 테스트 통과

### Phase 8. 인프라 연동

- [ ] Redis 연결 설정 및 캐시 조회/저장 (agent 응답 캐싱)
- [ ] PostgreSQL + pgvector 연결 검증 (SQLAlchemy async)
- [ ] `scripts/embed_metadata.py` — 시설 메타데이터 → 임베딩 → pgvector 적재 (배치)

### Phase 9. 미들웨어 / 관측가능성

- [ ] `middleware/metrics.py` — 요청별 응답시간 측정
- [ ] 에이전트별 오류율 로깅

### Phase 10. 테스트

- [ ] 각 Agent 단위 테스트 (`pytest-asyncio`)
- [ ] graph 통합 테스트 (의도별 분기 시나리오)
- [ ] `/chat/stream` 엔드포인트 E2E 테스트
- [ ] `/notification/template` 엔드포인트 E2E 테스트

---

## 참고

- LLM 공급자 미확정 → `llm/client.py` 추상화 레이어에서 공급자 교체 가능하도록 설계
- 관리 UI 없음 → Swagger UI (`/docs`) / Postman으로 대체
- 모니터링(Prometheus, Grafana) 연계는 Phase 9 이후 별도 문서화
