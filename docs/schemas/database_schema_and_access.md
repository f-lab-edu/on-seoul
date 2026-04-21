# DB 구조 설계

## 현재 구조

| DB | 역할 | 비고 |
|---|---|---|
| `on_data` | 정형 데이터 전체 (수집, 사용자, 채팅, 알림) | API 서비스 전담 |
| `on_ai` | 벡터 검색 + 에이전트 실행 메타 | AI 서비스 전담 |

### on_data 내부 도메인

```
on_data
├── 수집      data_source_catalog, collection_history, service_change_log, public_service_reservations
├── 사용자    users
├── 채팅      chat_rooms, chat_messages
└── 알림      notification_subscriptions, notification_log
```

### on_ai 내부 도메인

```
on_ai
├── 벡터 검색    service_embeddings
└── 에이전트     chat_agent_traces
```

### 계정 구성

| 계정 | 접근 DB | 권한 | 사용 서비스 |
|---|---|---|---|
| `on_data_app` | `on_data` | CRUD | API 서비스 |
| `on_data_reader` | `on_data` | SELECT | AI 서비스 (sql_search, map_search tool) |
| `on_ai_app` | `on_ai` | CRUD | AI 서비스 |

### on_ai 분리 근거

pgvector HNSW 인덱스 빌드/리빌드 시 CPU/메모리를 집중 사용한다. 정형 데이터 조회에 영향을 주지 않기 위해 DB 수준으로 격리한다.

`chat_agent_traces`를 `on_ai`에 두는 이유: AI 서비스가 LangGraph 실행 완료 후 직접 INSERT해야 한다. `on_data`에 두면 AI 서비스에 쓰기 권한을 별도로 부여해야 하고 서비스 경계가 흐려진다.

---

## 추후 분리 기준

현재 `on_data`에 도메인이 혼재하지만 MVP 규모에서는 문제없다. 아래 신호가 관측되면 PostgreSQL schema 분리 또는 DB 분리를 검토한다.

- 수집 배치 실행 중 API 응답 지연이 발생할 때
- 테이블 수가 20개를 넘어 스키마 관리가 복잡해질 때
- 서비스별 독립 배포가 필요해질 때