# CLAUDE.md

이 파일은 Claude Code가 on-seoul 프로젝트를 이해하고 작업할 때 참고하는 컨텍스트입니다.

## 프로젝트 개요

서울 열린데이터 광장의 공공 API로 서울시 공공서비스 예약 데이터를 수집·정제하여 챗봇 안내, 개인화 알림, 지도 기반 탐색 기능을 제공하는 AI Agent 서비스.

## 아키텍처

두 개의 독립 서비스로 구성된다.

- **on-seoul-agent** (AI Service) — FastAPI + LangGraph 기반 멀티에이전트 오케스트레이션, LLM 추론, 자연어 답변 생성
- **on-seoul-api** (API Service) — Spring Boot 기반 인증(OAuth 2.0 + JWT), 데이터 수집 스케줄링, 알림 발송, 대화 이력 관리

챗봇 흐름: 프론트엔드 → API Service (SSE 릴레이) → AI Service (에이전트 워크플로우) → PostgreSQL (SQL/벡터 조회)

## 디렉토리 구조

```
on-seoul/
├── on-seoul-agent/          # FastAPI AI 서비스 (Python 3.13+, uv)
│   ├── main.py              # FastAPI 앱 진입점
│   ├── pyproject.toml       # 의존성 관리 (uv)
│   ├── routers/             # API 엔드포인트 (POST /chat/stream SSE)
│   ├── agents/              # LangGraph 에이전트 (router, sql, vector, answer)
│   ├── tools/               # 룰베이스 도구 (sql_search, vector_search, map_search)
│   ├── llm/                 # LLM 클라이언트 추상화, 임베딩
│   ├── schemas/             # Pydantic 모델 (AgentState, SSE 이벤트, 요청/응답)
│   ├── core/                # 설정, DB 연결
│   ├── scripts/             # 배치 스크립트 (임베딩 적재 등)
│   └── middleware/          # 응답시간 측정
├── on-seoul-api/            # Spring Boot API 서비스 (Java)
│   ├── controller/          # REST 컨트롤러 (Auth, Chat, History, Notification)
│   ├── service/             # 비즈니스 로직 (수집, 변경감지, 알림)
│   ├── domain/              # JPA 엔티티
│   ├── repository/          # Spring Data JPA
│   ├── scheduler/           # 일 1회 수집 스케줄러
│   └── security/            # Spring Security (OAuth 2.0 + JWT)
└── docs/                    # 프로젝트 문서 (architecture.md)
```

## 기술 스택

| 영역 | 기술 |
|---|---|
| AI Service | Python 3.13+, FastAPI 0.135.x, LangChain, LangGraph, OpenAI |
| API Service | Java21, Spring Boot 3.5.x|
| DB (정형+벡터) | PostgreSQL + pgvector (단일 인스턴스) |
| 패키지 관리 | uv (Python), Maven/Gradle (Java) |
| 캐시 | Redis |
| 알림 | FCM (Firebase Cloud Messaging) |
| 지도 | 카카오맵 또는 네이버 지도 API |
| 테스트 | pytest + pytest-asyncio |
| 린터 | ruff |

## 에이전트 구조

```
사용자 질문 → Router Agent (의도 분류)
  ├─ SQL_SEARCH  → SQL Agent → sql_search tool → Answer Agent
  ├─ VECTOR_SEARCH → Vector Agent → vector_search tool → Answer Agent
  ├─ MAP → map_search tool → GeoJSON 반환
  └─ FALLBACK → Answer Agent (안내 메시지)
```

- **Router Agent**: 사용자 의도를 SQL_SEARCH / VECTOR_SEARCH / MAP / FALLBACK으로 분류
- **SQL Agent**: 정형 데이터 조회 (카테고리, 상태, 지역, 날짜 필터)
- **Vector Agent**: 질의 정제 후 pgvector 임베딩 유사도 검색
- **Answer Agent**: 조회 결과를 자연어 답변 + 시설 카드로 가공. URL 미존재 시 fallback 링크 처리

## 주요 설계 결정

- **pgvector 단일 인스턴스**: 1000건 미만 데이터에서 별도 벡터DB 불필요. SQL 조회와 벡터 검색을 동일 DB에서 처리
- **tool 3종 분리**: sql_search, vector_search, map_search는 입출력 형태가 다르므로 별도 tool로 분리
- **fallback_link는 Answer Agent 내부 처리**: 별도 tool로 분리할 복잡도가 아님
- **SSE 스트리밍**: API Service가 AI Service의 SSE 응답을 프론트엔드에 릴레이
- **알림 메시지 생성**: 알림 *발송*은 API Service(FCM)가 담당하지만, 메시지 *생성*은 AI Service(`POST /notification/template`)가 담당. API Service가 상태 변경 감지 후 해당 엔드포인트를 호출하여 메시지를 받아 발송

## 개발 명령어

```bash
# on-seoul-agent (Python)
cd on-seoul-agent
uv sync                              # 의존성 설치
uv run uvicorn main:app --reload     # 개발 서버 실행
uv run pytest                        # 테스트
uv run ruff check .                  # 린트
uv run ruff format .                 # 포맷

# 헬스체크
curl http://localhost:8000/health
```

## 코딩 컨벤션

- Python: ruff로 린트/포맷 통일. 타입 힌트 필수
- 비동기 우선: FastAPI 핸들러와 DB 호출은 async/await 사용 (SQLAlchemy async)
- 환경변수: pydantic-settings로 관리. `.env` 파일은 커밋하지 않음
- 커밋 메시지: 한글 허용. 변경 의도를 명확히 기술

## 수집 대상 API

서울 열린데이터 광장에서 5개 카테고리의 공공서비스 예약 데이터를 수집한다.

| API명 | 데이터셋 ID |
|---|---|
| 문화행사 공공서비스 예약 | OA-2269 |
| 체육시설 공공서비스 예약 | OA-2266 |
| 시설대관 공공서비스 예약 | OA-2267 |
| 교육 공공서비스 예약 | OA-2268 |
| 진료 공공서비스 예약 | OA-2270 |

수집 흐름: 수집이력 생성 → Open API 호출 (페이지네이션 전체 수집) → 공통 RDB 스키마 변환 → 기존 데이터 비교 → 신규/변경/유지 분류 (service_id 기준) → DB Upsert → 수집이력 결과 기록


