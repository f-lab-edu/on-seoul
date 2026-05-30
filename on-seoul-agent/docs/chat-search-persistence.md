# Chat Search Persistence — 운영 가이드

> 챗봇 검색 채널별 질의·결과 적재 시스템. `chat_search_queries` + `chat_search_results` 두 테이블에 메시지 1건당 채널별 입력/출력을 기록하여 운영·디버깅·평가·가중치 튜닝의 근거 데이터로 활용한다.

---

## 1. 개요

`on-seoul-agent` 의 그래프 종단부에서 `search_persist_node` 가 `AgentState.search_channels` 를 두 개의 테이블에 일괄 적재한다.

| 테이블 | 의미 | 카디널리티 |
|---|---|---|
| `chat_search_queries` | **input** — 무엇으로 검색했는가 (임베딩 텍스트 / SQL keyword / BM25 토큰 / 좌표) | 메시지당 채널 수 (1~10) |
| `chat_search_results` | **output** — 채널이 반환한 시설 순위 | 메시지당 채널×top_k (수십~수백) |

두 테이블은 `(message_id, channel)` 키로 묶여 있으며, `kind` 컬럼은 분석 그룹화를 위해 양쪽에 동일하게 denormalize 된다.

**적재 시점**: `cache_write_node → search_persist_node → trace_node` 종단 체인의 중간 단계. trace 와 마찬가지로 **best-effort** 로 동작하여 적재 실패가 사용자 응답을 막지 않는다.

**격리 대상**:

- `chat_agent_traces` (intent / node_path / elapsed_ms / error) — 실행 메타데이터. 본 시스템과 직교.
- `chat_messages` — 사용자/봇 발화 원문. 본 테이블은 `message_id` 로만 논리 FK 참조 (cross-DB).

---

## 2. 스키마

### 2-1. `chat_search_queries`

| 컬럼 | 타입 | 의미 |
|---|---|---|
| `id` | BIGSERIAL PK | 시퀀스 ID |
| `message_id` | BIGINT NOT NULL | `chat_messages.id` (cross-DB 논리 FK) |
| `kind` | VARCHAR(8) NOT NULL | `sql` / `vector` / `bm25` / `rrf` / `map` / `final` (CHECK 화이트리스트) |
| `channel` | VARCHAR(32) NOT NULL | 세부 채널 (`sql`, `vector_a`, `hyde_vector` 등). CHECK 미적용 |
| `query_text` | TEXT NULL | 사람-읽기 가능한 단일 표현. `rrf` / `final` 은 `NULL` |
| `parameters` | JSONB | 구조화 파라미터 |
| `created_at` | TIMESTAMPTZ DEFAULT NOW() | 적재 시각 |

**제약**: `UNIQUE (message_id, channel)`, `CHECK (kind IN (...))`

**인덱스**: `(message_id)`, `(message_id, channel)`

### 2-2. `chat_search_results`

| 컬럼 | 타입 | 의미 |
|---|---|---|
| `id` | BIGSERIAL PK | |
| `message_id` | BIGINT NOT NULL | |
| `kind` | VARCHAR(8) NOT NULL | queries 와 동일 값 (denormalize) |
| `channel` | VARCHAR(32) NOT NULL | queries 와 동일 채널 |
| `rank` | SMALLINT NOT NULL | 1-based 순위 |
| `service_id` | VARCHAR(255) NOT NULL | `public_service_reservations.service_id` |
| `score` | DOUBLE PRECISION NULL | 채널 native 점수 (similarity / bm25_score / rrf_score / distance_m) |
| `meta` | JSONB | 채널별 부가 정보 (`intent_label`, `embedding_text` 등) |
| `created_at` | TIMESTAMPTZ DEFAULT NOW() | |

**제약**: `UNIQUE (message_id, channel, rank)`, `CHECK (rank >= 1)`, `CHECK (kind IN (...))`

**인덱스**: `(message_id)`, `(message_id, channel)`, `(service_id)`, `(message_id, kind)`

---

## 3. kind · channel 매핑

`kind` 는 안정적인 6종 화이트리스트, `channel` 은 Phase 진화에 따라 자유로이 확장된다.

| kind | 채널 (Phase 진화) | 의미 |
|---|---|---|
| `sql` | `sql` | 정형 필터 기반 조회 |
| `vector` | `vector` (Phase 1) → `vector_a` / `vector_b` / `vector_c` (Phase 2) · `hyde_vector` (Phase 3) | pgvector 유사도 검색 |
| `bm25` | `bm25` | ParadeDB 전문 검색 |
| `rrf` | `rrf` (Phase 2+) | 다중 채널 Reciprocal Rank Fusion |
| `map` | `map` | 좌표 + 반경 기반 지도 검색 |
| `final` | `final` | hydration · dedup · top_k 절단 후 **실제 사용자 노출 목록** |

### `query_text` 값 유형

| 채널 | query_text 형태 | 예시 |
|---|---|---|
| `sql` | SqlAgent 가 추출한 keyword (없으면 NULL) | `"수영장"` |
| `vector*` | Router 또는 VectorAgent 가 정제한 임베딩 문장 | `"아이와 함께 가기 좋은 체험 공간"` |
| `bm25` | 변별력 있는 토큰 join (stopword 제거 후) | `"체험 수영 강습"` |
| `map` | 좌표 + 반경의 사람-읽기 가능한 단일 표현 | `"lat=37.5665,lng=126.9780,r=2000m"` |
| `rrf` / `final` | **NULL** (집계/병합 채널 — 원본 검색 미수행) | — |

### `parameters` 값 유형

| 채널 | 주요 키 |
|---|---|
| `sql` | `max_class_name`, `area_name`, `service_status`, `keyword`, `top_k` |
| `vector*` | `top_k`, `min_similarity`, `max_class_name`, `area_name`, `service_status` |
| `bm25` | `tokens` (list), `top_k` |
| `map` | `lat`, `lng`, `radius_m`, `top_k` |
| `rrf` | `source_channels` (list), `weights`, `k_constant` |
| `final` | `source_channels`, `hydration_applied` |

---

## 4. 분석 쿼리 6종

### ① 특정 메시지의 전체 검색 흐름

```sql
-- 무엇으로 검색해서 무엇이 나왔는지 한 눈에 본다.
SELECT q.kind, q.channel, q.query_text, q.parameters,
       r.rank, r.service_id, r.score, r.meta
FROM   chat_search_queries q
LEFT   JOIN chat_search_results r USING (message_id, channel)
WHERE  q.message_id = $1
ORDER  BY q.channel, r.rank NULLS FIRST;
```

`LEFT JOIN` 은 0건 결과(`results` 부재) 채널도 함께 보여준다.

### ② kind별 평균 결과 수 (recall 진단)

```sql
-- vector 계열 일괄 진단. 채널별 평균 hits 수가 비정상적으로 낮으면 recall 부족 신호.
SELECT kind, channel, AVG(cnt)::numeric(6,2) AS avg_hits, COUNT(*) AS sample
FROM (
    SELECT message_id, kind, channel, COUNT(*) AS cnt
    FROM   chat_search_results
    WHERE  created_at >= NOW() - INTERVAL '7 days'
    GROUP  BY message_id, kind, channel
) t
GROUP BY kind, channel
ORDER BY kind, channel;
```

### ③ RRF가 끌어올린 시설 (Phase 2)

```sql
-- 단일 채널에는 보이지 않았지만 RRF 병합 후 상위로 올라온 service_id.
-- 가중치 튜닝 회귀 측정용.
WITH rrf AS (
    SELECT message_id, service_id
    FROM   chat_search_results
    WHERE  channel = 'rrf' AND rank <= 10
),
vec_a AS (
    SELECT message_id, service_id
    FROM   chat_search_results
    WHERE  channel = 'vector_a' AND rank <= 10
)
SELECT COUNT(*) AS rrf_only_count
FROM   rrf
LEFT   JOIN vec_a USING (message_id, service_id)
WHERE  vec_a.service_id IS NULL;
```

### ④ surfaced 됐지만 final 에서 떨어진 시설 (hydration miss / dedup 진단)

```sql
-- 검색에서는 상위 surfacing 됐지만 최종 사용자 노출에서 빠진 시설.
-- hydration 시 원본이 사라졌거나 dedup 에서 우선순위가 낮았던 경우 진단.
SELECT service_id, COUNT(*) AS surfaced_count
FROM   chat_search_results r
WHERE  kind IN ('vector', 'rrf', 'bm25')
  AND  rank <= 10
  AND  NOT EXISTS (
        SELECT 1 FROM chat_search_results f
        WHERE  f.message_id = r.message_id
          AND  f.channel    = 'final'
          AND  f.service_id = r.service_id
      )
GROUP BY service_id
ORDER BY surfaced_count DESC
LIMIT  20;
```

### ⑤ 0건 결과 질의 — recall 부족 / stopword 과적용 진단

```sql
-- 검색했는데 결과가 없었던 채널을 찾는다.
-- queries 에는 행이 있지만 results 에 매칭이 없는 케이스.
SELECT q.kind, q.channel, q.query_text, q.parameters, q.created_at
FROM   chat_search_queries q
LEFT   JOIN chat_search_results r USING (message_id, channel)
WHERE  r.id IS NULL
  AND  q.kind IN ('sql', 'vector', 'bm25')
  AND  q.created_at >= NOW() - INTERVAL '24 hours'
ORDER  BY q.created_at DESC;
```

### ⑥ 특정 임베딩 텍스트 검색 (튜닝 / 디버깅)

```sql
-- 특정 키워드로 임베딩된 적이 있는지. refined_query 튜닝의 사후 진단.
SELECT message_id, channel, query_text, created_at
FROM   chat_search_queries
WHERE  kind = 'vector'
  AND  query_text ILIKE '%수영장%'
ORDER  BY created_at DESC
LIMIT  50;
```

---

## 5. 노드 동작 명세

### `search_persist_node` 행위

```
1. state.search_channels 가 빈 dict 면 INSERT 없이 즉시 종료 (node_path += "search_persist_skip")
2. 각 채널을 순회하며:
   - kind_of(channel) 로 정규 kind 결정 (등록 채널)
   - 미등록 freeform 채널은 ChannelData.kind 그대로 사용
   - queries 행 1개 + results 행 N개를 메모리에 누적
3. 동일 트랜잭션으로 두 테이블 INSERT
4. commit (성공) / rollback (실패)
5. 어느 단계든 예외는 logger.warning 으로 swallow — 그래프 진행은 계속됨
```

### 0건 결과 정책

`hits` 가 비어도 `queries` 행은 **항상** 기록한다. 0건 결과 자체가 recall 부족 / stopword 과적용 진단의 신호이기 때문.

### self-correction 재시도와 UNIQUE 제약

`(message_id, channel)` UNIQUE 위반을 피하기 위해 `retry_prep_node` 가 `search_channels = {}` 리셋 시그널을 반환한다. `search_channels_reducer` 가 이 신호를 받으면 누적된 dict 를 비운다. 다음 시도에서 새 채널 데이터가 채워지고, `search_persist_node` 는 **마지막 시도의 채널만** 보게 된다.

방어적 안전망으로 두 INSERT 모두 `ON CONFLICT DO NOTHING` 으로 묶여 있어, 만약 리셋이 누락되어도 그래프 진행이 막히지 않는다.

### cache hit 경로

`cache_check_node` 에서 hit 발생 시 검색 노드를 건너뛰지만 종단 체인의 일관성을 위해 `search_persist_node` 를 경유한다. 빈 `search_channels` 로 즉시 skip 되므로 오버헤드는 무시할 수준.

---

## 6. 보존 정책

### 적재량 추정

| Phase | 채널 수 | 메시지당 queries 행 | 메시지당 results 행 (top_k=10) |
|---|---|---|---|
| Phase 1 (현재) | `sql` 또는 `vector`+`bm25`+`final` 또는 `map` | 1~3 | 10~30 |
| Phase 2 (RRF) | `vector_a/b/c` + `bm25` + `rrf` + `final` | 6 | ~60 |
| Phase 3 (HyDE) | + `hyde_vector` | 7 | ~70 |

일일 메시지 1만 건 가정 시 Phase 2 기준:

- `chat_search_queries`: 60K rows/day
- `chat_search_results`: 600K rows/day

### 보존 권장

- **30일 보존** 이후 자정 시점 일일 회전.
- 트래픽 증가 후 `PARTITION BY RANGE (created_at)` 월별 파티셔닝 도입 검토.
- 자동 purge 는 별도 계획 (`docs/superpowers/plans/` 미정).

### 인덱스 부하

`chat_search_results` 의 `(service_id)` 인덱스는 시설 역추적용(특정 시설이 어떤 채널에서 surfaced 됐는지)이지만 카디널리티가 낮으므로 INSERT 비용은 낮다. 결과 행 폭주 시에는 우선 고려할 제거 대상.

---

## 7. PII / 개인정보

| 컬럼 | PII 위험 | 메모 |
|---|---|---|
| `query_text` | **낮음** | 사용자 원본 메시지가 아니라 Router 가 정제한 검색 문장. 단, 정제 과정에서 사용자 명사/관심사가 부분 포함될 수 있음 |
| `service_id` | 없음 | 공공 시설 식별자 |
| `meta`, `parameters` | 없음 | 검색 파라미터·내부 라벨 |

- 현재 시점에서 `query_text` 외 PII 위험 컬럼 없음.
- 사용자 원본 메시지를 `meta` 에 저장할 계획이 생기면 별도 검토 필요.
- 정책상 30일 보존 가이드와 같이 작동하여 장기 식별 위험 최소화.

---

## 8. 운영 디버깅 체크리스트

장애·이상 상황 시 다음 순서로 확인한다.

1. **적재가 0건인가?** → `chat_agent_traces` 의 `node_path` 에 `"search_persist_skip"` 또는 `"search_persist_error"` 가 있는지.
2. **에러가 잡혔는가?** → 애플리케이션 로그 `"search_persist 적재 실패"` 검색.
3. **특정 채널만 빠지는가?** → ①번 분석 쿼리로 메시지 단위 확인.
4. **재시도 후 데이터가 이상한가?** → `retry_count` 가 1인 메시지 추출 후 ①번 쿼리 → 마지막 시도 데이터만 있어야 함.
5. **UNIQUE 위반?** → `ON CONFLICT DO NOTHING` 으로 swallow 되지만 빈도가 잦다면 reducer 리셋 누락 의심.

---

## 9. 향후 확장

- **자동 회귀 분석 대시보드**: 채널별 일별 평균 결과 수, recall 부족 채널 알림.
- **Replay 도구**: 과거 message_id 의 final 결과를 가중치 변경 후 재실행 비교.
- **Partition + 자동 purge**: 30일 보존 자동화.
- **사용자 만족도 신호 결합**: `chat_messages.feedback` 과 join 하여 final 채널 품질 측정.

---

## 10. 변경 이력

| 날짜 | 변경 | 사유 |
|---|---|---|
| 2026-05-20 | 초기 작성 (Task 1-6 완료) | Phase 1 search persistence 도입 |
