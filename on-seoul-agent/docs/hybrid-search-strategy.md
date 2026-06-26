# 하이브리드 검색 전략

## 개요

서울시 공공데이터 AI 분석 서비스(on-seoul)의 시맨틱 검색(pgvector) + BM25(pg_search) 결합 검색 시스템 설계 및 운영 기준을 정리한다.

검색은 **4채널**로 구성된다. `service_embeddings` 테이블에 `row_kind` 디스크리미네이터로 트랙을 분리하고, `VectorAgent`가 4채널을 병렬 실행한 뒤 가중 RRF(Reciprocal Rank Fusion)로 결합한다.

> **ANALYTICS intent 제외**: ANALYTICS intent는 RRF/벡터 검색 경로와 무관한 별도 정형 집계 경로다 — `analytics_search` 도구가 GROUP BY COUNT / DISTINCT 쿼리를 직접 실행하며 임베딩, RRF 가중치, hydration 단계를 거치지 않는다.

---

## 결론

| 채널 | 담당 도구 | 트랙 | 역할 |
|---|---|---|---|
| Track A | `vector_search(row_kind='identity')` | 시설 신원 텍스트 | 지역, 분류, 시설명 기반 식별 검색. post-filter 적용 가능 |
| Track B | `vector_search(row_kind='summary')` | LLM 구조화 요약 | 요금, 운영시간, 시설 특성 등 세부 의미 검색 |
| Track C | `question_search` | HyQE 예상 질문 | 사용자 질문 패턴과 직접 매칭. service_id별 최고 rank dedup |
| BM25 | `bm25_search` | `row_kind='identity'` partial index | 정확한 명칭, 고유명사 키워드 매칭 |
| 최종 순위 | `core/rrf.py` | — | 4채널 결과를 가중 RRF로 통합 |

---

## 배경

### 인프라 스택

ParadeDB(`paradedb/paradedb:latest`) 단일 이미지에서 두 확장이 함께 동작한다. 벡터 검색과 BM25를 PostgreSQL 한 곳에서 처리해, 별도 검색엔진을 두고 동기화하는 것보다 인프라 관리 비용과 복잡도를 낮추려는 선택이다.

- **벡터 검색:** `vector` (pgvector) 0.8.1 — 임베딩 벡터 저장 + 코사인 유사도 검색
- **키워드 검색:** `pg_search` 0.23.4 — BM25 인덱싱 + Lindera 한국어 토크나이저

---

## 데이터 모델

기준 테이블은 `service_embeddings`다. **row-per-vector 통합 구조**로, 각 시설은 `row_kind`에 따라 최대 `1 + 1 + N`행(identity 1 + summary 1 + question N)이 같은 테이블에 들어간다.

```sql
CREATE TABLE service_embeddings (
    id            BIGSERIAL PRIMARY KEY,
    service_id    VARCHAR(255)  NOT NULL,
    row_kind      VARCHAR(16)   NOT NULL,  -- 'identity' | 'summary' | 'question'
    idx           SMALLINT      NOT NULL DEFAULT 0,  -- question row 순번. 그 외 0
    service_name  TEXT          NOT NULL,  -- 모든 row에 복제 (디버깅, JOIN 비용 절감)
    embedding_text TEXT         NOT NULL,  -- 실제 임베딩에 사용된 텍스트 (품질 감사용)
    embedding     vector(768),
    metadata      JSONB,                   -- identity row에만 존재. extracted 키 포함
    intent_label  VARCHAR(32),             -- question row에만 존재. semantic|detail|keyword
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (service_id, row_kind, idx),
    CHECK (row_kind IN ('identity', 'summary', 'question')),
    CHECK ((row_kind = 'question') = (intent_label IS NOT NULL))
);
```

### 컬럼 역할

| 컬럼 | identity | summary | question |
|---|---|---|---|
| `embedding_text` | `{area} {max_class} {min_class} {service_name} {place_name}` | LLM 추출 요약 1문장 | HyQE 예상 질문 1건 |
| `metadata` | `{extracted: {fee, hours, ...}}` + 검색 필터 필드 | NULL | NULL |
| `intent_label` | NULL | NULL | `semantic` / `detail` / `keyword` |
| `idx` | 0 | 0 | 0 ~ N-1 |

---

## 인덱스 전략

### 시맨틱 검색용: HNSW (단일, 전체 row_kind 대상)

```sql
CREATE INDEX idx_service_embeddings_hnsw
    ON service_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
```

모든 row_kind가 단일 HNSW 인덱스 하나에 공존한다. 트랙마다 인덱스를 따로 두지 않고 하나로 합쳐 추가 인덱스 비용을 최소화하기 위해서다. `vector_search`, `question_search`는 `WHERE row_kind = :row_kind` 조건을 더해 트랙별 partial 쿼리를 실행한다. 전체 row를 대상으로 HNSW가 동작한 뒤 바깥에서 row_kind로 거르는 post-filter 전략이다.

#### `SET LOCAL hnsw.ef_search`

`vector_search`, `question_search`는 벡터 쿼리 실행 직전 같은 트랜잭션에 `SET LOCAL hnsw.ef_search = :ef_search`(`hnsw_ef_search`, 기본 100)를 발급한다. HNSW Index Scan이 채택될 경우, ANN 후보 큐 크기(`ef_search`)가 `scan_k`(기본 50)보다 작으면 `LIMIT :scan_k` 후보를 다 채우지 못해 recall이 누락될 수 있다. `ef_search >= scan_k`를 보장하면 HNSW 경로에서도 exact KNN과 동등한 recall을 얻는다. `SET LOCAL`은 현재 트랜잭션에만 적용되어 커넥션 풀에 누수되지 않는다.

현 데이터 규모(트랙당 수천 행)에서는 planner가 비용상 exact Seq Scan을 택할 수 있으며, 이 경우 결과는 exact KNN이라 recall이 완전하다. 데이터가 늘면 동일 쿼리에서 planner가 HNSW Index Scan으로 자연히 전환하고, 그때 위 `ef_search` 설정이 recall을 보전한다. 즉 `ef_search` 발급은 향후 HNSW 전환 시점의 정확성을 미리 보장하는 장치이며, 현 규모에서 별도의 속도 이득을 전제하지 않는다.

### BM25 검색용: partial BM25 인덱스 (`row_kind='identity'`)

```sql
CREATE INDEX idx_service_embeddings_bm25
    ON service_embeddings
    USING bm25 (id, service_name, metadata)
    WITH (
      key_field = 'id',
      text_fields = '{
        "service_name": {"tokenizer": {"type": "korean_lindera"}}
      }',
      json_fields = '{
        "metadata": {"tokenizer": {"type": "korean_lindera"}}
      }'
    )
    WHERE row_kind = 'identity';
```

summary/question row의 텍스트가 BM25 IDF를 오염시키지 않도록 identity row만 색인한다. `korean_lindera`는 KoDic 사전 기반 형태소 분석을 수행한다.

### Hydration/재적재용: service_id B-tree

```sql
CREATE INDEX idx_service_embeddings_service_id
    ON service_embeddings (service_id);
```

---

## 임베딩 전략

### 모델

- 모델: `models/gemini-embedding-2-preview`
- 차원: 768 (코사인 유사도 기준)

차원은 768과 1536을 두고 검토했다. 같은 모델에서 `output_dimensionality`만 바꿔 1536을 낼 수 있으므로(차원 절단 방식), 차원 선택은 모델 교체 없이 재임베딩만으로 바꿀 수 있다. 현재 색인 대상이 수백~1천 건 미만으로 작아 768로도 의미 해상도가 충분하고, 저장 공간, HNSW 인덱스 메모리, 검색 지연을 낮게 유지할 수 있다. 우선 768로 서비스하며 recall@k, MRR을 관찰하고, 데이터가 늘어 768의 표현력이 병목이 되면 1536으로 전체 재임베딩(재구축)한다.

### 3-트랙 임베딩 파이프라인

`scripts/embed_metadata.py` 오케스트레이터가 `scripts/tracks/` 모듈을 호출하여 시설당 세 트랙을 순서대로 적재한다.

#### Track A — identity (항상 적재)

```python
embedding_text = f"{area_name} {max_class_name} {min_class_name} {service_name} {place_name}"
# 예: "강남구 체육시설 테니스장 마루공원 1면"
```

지역, 분류, 시설명 조합. 검색 필터(`max_class_name`, `area_name`, `service_status`)는 이 row의 `metadata`에 저장되며 post-filter에서만 사용한다.

#### Track B — summary (LLM 추출 성공 시 적재)

`detail_content`를 사전 정제("3. 상세내용" ~ "4. 주의사항" 구간 추출)한 뒤 LLM으로 구조화 추출한다. `extracted.summary` 1문장이 Track B의 임베딩 입력이 된다.

```python
# llm/extractor.py → ExtractedMetadata
extracted.fee            # "평일 5천원, 주말 1만원"
extracted.operating_hours # "09:00-21:00"
extracted.summary         # "강남구 테니스장. 평일 5천원 이용 가능." ← embedding_text
```

LLM 호출 실패 시 summary row를 생성하지 않는다(Track A 클론이 되어 RRF 점수 왜곡 발생). `extraction_failed.tsv`에 로깅 후 `--retry-failed`로 재처리한다.

#### Track C — question / HyQE (Track B 성공 시 적재)

LLM이 시설당 예상 질문을 생성한다(`HYQE_QUESTIONS_PER_SERVICE`, 현재 6개). 각 질문이 question row 1건이 된다.

```python
# llm/hyqe.py → list[HyQEQuestion]
# 분포 강제: semantic 50% / detail 30% / keyword 20%
{"question_text": "강남구 테니스장 평일 요금은 얼마인가요?", "intent_label": "detail"}
{"question_text": "마루공원에서 테니스 칠 수 있는 곳",        "intent_label": "semantic"}
```

같은 service_id의 question row가 여러 개 매칭되면 `question_search`가 PARTITION BY dedup으로 최고 similarity 1건만 반환한다.

---

## 토크나이저

BM25 토큰화는 두 지점에서 일어난다.

- **색인 / 매칭 (DB)**: ParadeDB `korean_lindera`(KoDic). `service_embeddings` 색인과 `col @@@ 'token'` 쿼리 평가가 동일 토크나이저를 사용하므로 색인↔매칭이 일치한다.
- **쿼리 전처리 (Python, `tools/tokenizer.py`)**: Kiwi(kiwipiepy)로 사용자 질의에서 의미 품사(체언, 용언 어간 등)만 추출해 BM25 검색어를 선별한다(조사, 어미 제거로 노이즈 감소). 도메인 용어는 `DOMAIN_TOKENS`로 원형 보존. 미설치 시 공백 분리로 폴백하며, 고QPS에서는 `atokenize_query()`로 오프로드한다.

### 동작 예시

- "강남 근처 무료 문화행사 알려줘" → "강남", "근처", "무료", "문화행사"
- "한강공원 따릉이 대여소" → "한강공원", "따릉이", "대여소"

### 한계와 대응

`서울` 단독 검색 시 `서울시`, `서울역` 매칭이 불가능하다. 이는 BM25의 토큰 정확 매칭 특성상 정상 동작이며, 하이브리드 구조에서 Track A/B/C 시맨틱 채널이 보완한다.

토큰 목록은 BM25 쿼리 조건으로 변환하기 전에 특수문자, 예약어를 제거한다.

```python
# tools/bm25_search.py
_BM25_SPECIAL = re.compile(r'[+\-:?\\*~^(){}\[\]"]')
_BM25_RESERVED = frozenset({"AND", "OR", "NOT", "TO", "IN"})

def build_bm25_query(tokens: list[str]) -> str:
    safe = [t for t in (_BM25_SPECIAL.sub("", t) for t in tokens)
            if t and t.upper() not in _BM25_RESERVED]
    return " ".join(safe)
```

모든 토큰이 제거되면 BM25 채널을 건너뛴다.

### 도메인 공통어 stopword 필터 (BM25 전용)

`예약`, `서울`, `서울시`, `공공`, `서비스` 등 전 문서에 걸쳐 IDF ≈ 0인 어휘는 BM25 쿼리 전에 필터링한다.

```python
# agents/vector_agent.py
_BM25_STOPWORDS: frozenset[str] = frozenset({
    "예약", "서울", "서울시", "공공", "서비스", "공공서비스",
    "접수", "신청", "이용", "안내", "시설", "프로그램",
})

bm25_tokens = [t for t in tokens if t not in _BM25_STOPWORDS]
if bm25_tokens:
    d_rows = await _safe_bm25_search(ai_session, bm25_tokens)
else:
    d_rows = []
```

---

## 조회 전략 — SQL vs 벡터 vs 하이브리드 분기

Router Agent가 사용자 의도를 분류해 적절한 도구를 선택한다.

| 질의 유형 | 예시 | 조회 방식 | 주도 채널 |
|---|---|---|---|
| 상태/날짜 필터형 | "지금 접수 중인 수영장 알려줘" | **SQL** — `status = 'OPEN'` | — |
| 지역 필터형 | "강남구 체육시설 목록" | **SQL** — `area_nm = '강남구'` | — |
| 식별형 | "응봉공원 테니스장 예약" | **벡터 하이브리드** | Track A + BM25 우세 |
| 세부정보형 | "테니스장 평일 이용료", "취소 며칠 전까지" | **벡터 하이브리드** | Track B 우세 |
| 의미/맥락형 | "어린이랑 같이 갈 만한 문화행사" | **벡터 하이브리드** | Track C + Track B 우세 |
| 고유명사형 | "따릉이 대여소" | **벡터 하이브리드** | BM25 우세 |
| 복합형 | "미세먼지 심할 때 실내 시설 추천" | **벡터 하이브리드 + post-filter** | Track A + Track B |

벡터 검색이 필요한 모든 케이스에서 4채널을 실행하고 RRF로 결합한다. 채널별 가중치는 `vector_sub_intent` 프로파일로 조정한다.

---

## VectorAgent 흐름

```
VectorAgent.search(state, ai_session)
  1. Router가 refined_query, post-filter 산출 → refine 체인 skip (중복 LLM 호출 방지)
     아니면 질의 정제 체인 호출 (_RefinedQuery)
  2. 정제된 질의 임베딩
  3. Kiwi(kiwipiepy) 토크나이징 — 의미 품사만 추출 (atokenize_query)
  4. 4채널 병렬 실행 (채널별 독립 세션 + 세마포어로 동시성 제어):
     - Track A: vector_search(row_kind='identity') + post-filter
     - Track B: vector_search(row_kind='summary')
     - Track C: question_search (PARTITION BY service_id dedup)
     - Track D: bm25_search (유효 토큰 있을 때만)
  5. _resolve_weights(vector_sub_intent) → 가중치 프로파일 결정
  6. reciprocal_rank_fusion(4채널) → (service_id, rrf_score) 리스트
  7. vector_results = [{service_id, rrf_score}] (메타데이터 only)
     hydration은 HydrationNode가 단독 담당
  8. search_channels 구성 (VECTOR_A/B/C, BM25, RRF 채널)
     → search_persist_node가 종단에서 일괄 적재
```

4채널은 채널마다 독립 세션(`ai_session_ctx()`)을 열어 `asyncio.gather`로 동시에 실행한다. asyncpg 세션 하나로는 동시 쿼리를 보낼 수 없어, 순차로 묶는 대신 세션을 따로 열어 병렬화했다. 대신 무제한 병렬은 커넥션 풀을 고갈시키므로 단일 글로벌 세마포어로 동시성을 캡한다.

- **글로벌 세마포어** (`core/concurrency.vector_global_sema`, `vector_global_concurrency=20`, 프로세스 레벨): 각 채널 실행(`_run_channel`)을 감싸 프로세스 전체에서 동시에 도는 채널 쿼리 수를 제한한다. on_ai 풀 상한(pool_size 10 + overflow 15 = 25) 안에 들도록 20으로 잡았다(≤20 < 25). 100 동시 요청 × 4채널 = 400 잠재 쿼리를 20으로 캡해 풀 버스트를 막는다. 적정값은 부하 측정 후 재산정한다.

### VectorSubIntent 가중치 프로파일

Router Agent가 `vector_sub_intent`(`identification` / `detail` / `semantic`)를 분류하면 `VectorAgent`가 해당 프로파일의 채널별 가중치로 RRF를 실행한다. 현재 운영값은 `rrf_unweighted_baseline=True`(비가중치, 모든 채널 1.0) + `vector_sub_intent_enabled=False`(`semantic` 단일 프로파일)다.

가중치 프로파일 수치, sub_intent 분류 기준, 단계적 활성화(Phase 1/2/3) 절차는 [RRF 결합 전략](./RRF-Strategy)에서 단일 관리한다. 수치 원본은 `core/config.py`의 `rrf_weight_profiles`다.

---

## 검색 쿼리 구조

### Track A — identity 벡터 검색 (post-filter)

```sql
-- :row_kind = 'identity', :scan_k = settings.rrf_scan_k_per_track (기본 50)
-- 쿼리 실행 직전 같은 트랜잭션에서 SET LOCAL hnsw.ef_search = :ef_search 발급
SELECT service_id, embedding_text, metadata, similarity
FROM (
    SELECT
        service_id, embedding_text, metadata,
        1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
    FROM service_embeddings
    WHERE row_kind = :row_kind
    ORDER BY embedding <=> CAST(:query_vector AS vector)
    LIMIT :scan_k
) candidates
-- min_similarity는 항상 outer 필터로 둔다. post-filter는 None이면 해당 절 생략
-- (asyncpg AmbiguousParameterError 방지).
WHERE candidates.similarity >= :min_similarity
  AND metadata->>'max_class_name' = :max_class_name
  AND metadata->>'area_name'      = :area_name
  AND metadata->>'service_status' = :service_status
ORDER BY similarity DESC
LIMIT :top_k;
```

`min_similarity`를 inner 서브쿼리 `WHERE`(`ORDER BY embedding <=> q LIMIT :scan_k`와 동거)에 두면 planner가 HNSW ANN을 포기하고 Seq Scan + Sort로 떨어진다. threshold를 outer 필터로 옮기면 inner는 순수 `ORDER BY ... LIMIT` 형태가 되어 HNSW Index Scan을 탈 수 있는 구조가 된다. `scan_k`가 `top_k`보다 충분히 커 가장 가까운 후보 안에 threshold 통과분이 모두 포함되므로 결과는 동등하다.

### Track B — summary 벡터 검색 (post-filter 미적용)

Track A와 쿼리 구조가 동일하지만 `WHERE row_kind = 'summary'`이고 post-filter 절이 없다. summary row는 `metadata`가 NULL이므로 카테고리, 자치구 필터는 Track A 채널이 담당한다.

### Track C — question 검색 (`DISTINCT ON` dedup)

```sql
SELECT * FROM (
    SELECT DISTINCT ON (service_id)
        service_id,
        embedding_text,
        intent_label,
        1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
    FROM service_embeddings
    WHERE row_kind = 'question'
    ORDER BY service_id, embedding <=> CAST(:query_vector AS vector)
) ranked
WHERE ranked.similarity >= :min_similarity
ORDER BY similarity DESC
LIMIT :top_k;
```

같은 service_id의 question row가 여러 개 매칭되더라도 `DISTINCT ON (service_id)`로 최고 유사도 1건만 RRF에 전달한다. 내부 정렬은 service_id 순이므로, 서브쿼리로 감싸 바깥에서 `similarity DESC`로 최종 정렬한다. Track A와 마찬가지로 `min_similarity`는 inner `DISTINCT ON` 서브쿼리가 아니라 outer 필터(`WHERE ranked.similarity >= :min_similarity`)로 두어, planner가 HNSW ANN을 포기하지 않도록 한다.

> 구버전은 `ROW_NUMBER()` 윈도우 함수 + `scan_k` LIMIT 조합이었으나, 윈도우 함수가 HNSW ANN 최적화를 막아 `DISTINCT ON`으로 전환했다. 전환 후 실행 계획이 단순해지고 Execution Time이 소폭 개선됐다(`tools/question_search.py` 도크스트링 참조).

### BM25 검색 (identity partial index)

pg_search 0.23.4 제약으로 bind 파라미터(`@@@ $N`), `paradedb.score`, 컬럼 OR 결합을 쓸 수 없다. 컬럼별·토큰별로 인라인 토큰 쿼리를 독립 실행하고 `ROW_NUMBER()`로 rank를 부여한 뒤 service_id 기준 MAX score로 머지한다(`tools/bm25_search.py`).

```sql
-- 컬럼당·토큰당 1쿼리. {column} ∈ {service_name, metadata}, {token}은 sanitize된 인라인 토큰.
-- AND row_kind = 'identity' 는 필수: bm25 인덱스가 WHERE row_kind='identity' partial 인덱스라
-- 이 조건이 없으면 planner가 partial 인덱스를 후보에서 제외해 Parallel Seq Scan(토큰당 ~350ms)으로
-- 떨어진다. 조건을 붙이면 ParadeDB Custom Scan(idx_service_embeddings_bm25)을 타 토큰당 ~30ms.
-- row_kind는 pg_search 인덱스 필드가 아니라 @@@ 문법 안에 넣을 수 없고, partial predicate 매칭으로 해소한다.
-- 결과 동등: 인덱스가 identity row만 색인하므로 @@@ 는 조건 유무와 무관하게 identity row만 매칭한다.
SELECT
    service_id,
    service_name,
    ROW_NUMBER() OVER () AS bm25_rank   -- bm25_score = 1.0 / rank 로 환산
FROM service_embeddings
WHERE {column} @@@ '{token}'
  AND row_kind = 'identity'
LIMIT 50;
```

### 4채널 RRF 결합

```python
# core/rrf.py
def reciprocal_rank_fusion(
    channels: dict[str, list[str]],   # {channel_name: [service_id 순위 리스트]}
    *,
    weights: dict[str, float] | None = None,  # None이면 모든 채널 1.0
    k_constant: int = 60,
) -> list[tuple[str, float]]:
    """rrf_score(sid) = Σ weight[c] / (k_constant + rank[c, sid])"""
```

`VectorAgent`가 4채널 결과를 수집한 뒤 `reciprocal_rank_fusion`을 호출한다.

```python
merged = reciprocal_rank_fusion(
    {
        "track_a": [r["service_id"] for r in a_rows],
        "track_b": [r["service_id"] for r in b_rows],
        "track_c": [r["service_id"] for r in c_rows],
        "bm25":    [r["service_id"] for r in d_rows],
    },
    weights=weights,           # None이면 비가중치 (rrf_unweighted_baseline=True)
    k_constant=settings.rrf_k_constant,  # 기본 60
)
```

### 검색 설정값 결정 기준

벡터 검색의 **가변 설정값**은 직관이 아니라 **봉인 평가셋 측정**으로 정한다. 적용 기준은 다음 세 가지다.

- **타깃 지표는 recall@k.** 이 서비스의 질의는 추천형(의미/맥락형)이 다수라 답변이 시설 카드 여러 장으로 제시된다. 정확한 1순위(MRR)보다 "적합한 답이 결과 목록에 있는가"가 사용자 경험에 직결되므로 recall@k를 우선한다.
- **회귀 게이트는 identification recall.** 식별형은 BM25, Track A가 주력이므로, 다른 값을 올리려다 식별 정확도를 떨어뜨리면 채택하지 않는다.
- **한 번에 한 값만** 바꿔 측정한다. 분포 분석(`scripts/eval/score_distribution.py`)은 가설을 만들 뿐이고, 채택은 recall 측정(`scripts/eval/run_recall.py`, post-filter 반영)이 정한다.

| 설정 | 값 | 근거 |
|---|---|---|
| `vector_min_similarity_*` (3트랙 공통) | **0.65** | 하한을 0.55 / 0.60 / 0.65 / 0.70으로 스윕한 결과 0.65가 정점(역U자). semantic recall@10이 0.65에서 최고, 0.70에서 급락. identification recall은 전 구간 1.0 유지. 원본 휴리스틱 0.60 대비 recall@k 상승. |
| `vector_track_top_k` | **10** | 후보 깊이 확대는 recall을 올릴 때만 채택. post-filter를 적용한 측정에서 깊이 10/scan 50과 깊이 30/scan 100의 recall@10이 동일 → 확대의 recall 이득이 확인되지 않아 원본 깊이 유지. |
| `rrf_scan_k_per_track` | **50** | 위와 동일하게 scan 확대의 recall 이득 미확인. `top_k`보다 충분히 커 post-filter 탈락 완충 역할은 유지한다. |

이 값들은 고정이 아니다. 운영 중 기대와 다른 결과(엣지 케이스)가 발견되면 해당 질의를 회귀 테스트 평가셋에 추가하고, 갱신된 평가셋의 측정 결과에 따라 위 설정값을 조정한다.

---

## 원본 데이터 Hydration

`service_embeddings`는 의미 검색 인덱스로만 사용한다. `VectorAgent`는 `vector_results`에 `{service_id, rrf_score}` 메타데이터만 채우고, **HydrationNode**가 `service_id`를 키로 `public_service_reservations` 원본 테이블에서 최신 값을 조회한다. 임베딩 시점에 스냅샷된 값이 stale 상태로 답변에 들어가는 것을 막기 위함이다.

### 컬럼 책임 분리

| 용도 | 출처 | 컬럼 예시 |
|---|---|---|
| 시맨틱 검색 | `service_embeddings.embedding` | `embedding_text` (트랙별 상이) |
| 키워드 검색 (BM25) | `service_embeddings` (identity partial) | `service_name`, `metadata` JSONB |
| post-filter | `service_embeddings.metadata` (identity) | `max_class_name`, `area_name`, `service_status` |
| 답변 표시 (Hydration) | `public_service_reservations` | `service_status`, `receipt_*_dt`, `service_url` 등 자주 바뀌는 모든 컬럼 |

**원칙:** `metadata`는 검색 후처리(post-filter)에만 쓰고, 사용자에게 노출되는 표시 값은 항상 원본 테이블에서 가져온다.

### 누락/실패 처리

| 상황 | 처리 |
|---|---|
| 임베딩엔 있지만 원본 테이블에 service_id 미존재 | 결과에서 제외 |
| 원본 행이 soft-delete (`deleted_at IS NOT NULL`) | 결과에서 제외 |
| HydrationNode 예외 (DB 다운 등) | `vector_results = []`로 fallback. Self-correction이 빈 답변을 재시도로 전환 |

---

## 임베딩 ↔ 원본 동기화 정책

`embed_metadata.py --incremental`은 `service_embeddings`에 아직 없는 신규 `service_id`만 적재한다. 의미 컬럼 변경 시 재임베딩이 필요한 트리거는 다음과 같다.

| 변경된 필드 | 임베딩 재생성 | 사유 |
|---|---|---|
| `service_name`, `max_class_name`, `min_class_name`, `area_name`, `place_name`, `target_info`, `detail_content` | **필요** | 의미 공간 자체가 달라짐 |
| `service_status`, `receipt_*_dt`, `service_open_*_dt`, `service_url`, `payment_type` | 불필요 | HydrationNode가 매 답변마다 최신 값을 끌어옴 |

재임베딩은 해당 service_id의 모든 row(`row_kind` 무관)를 삭제 후 `--track all`로 재적재한다.

---

## 운영 고려사항

### 배치 임베딩 적재 순서

1. 데이터 수집 (`public_service_reservations` upsert)
2. 트랙 A/B/C 임베딩 적재 (`scripts/embed_metadata.py --all`)
3. HNSW 인덱스 빌드 (데이터 후 생성이 빠름)
4. BM25 partial 인덱스 빌드

### 주요 설정 (core/config.py)

| 설정 | 기본값 | 설명 |
|---|---|---|
| `rrf_k_constant` | 60 | RRF 표준 상수. 작을수록 1위 가중치 강해짐 |
| `vector_min_similarity_*` | 0.65 | 트랙별 코사인 유사도 하한 (3트랙 공통, 측정 기반 — 위 "검색 설정값 결정 기준") |
| `vector_track_top_k` | 10 | 트랙별 RRF 입력 깊이 |
| `rrf_scan_k_per_track` | 50 | 트랙별 HNSW 스캔 건수 (post-filter 탈락 완충) |
| `hnsw_ef_search` | 100 | 벡터 쿼리 직전 `SET LOCAL hnsw.ef_search`로 발급. HNSW 경로 recall 보전 위해 `ef_search >= rrf_scan_k_per_track` |
| `rrf_top_k_final` | 10 | RRF 최종 반환 건수 |
| `rrf_unweighted_baseline` | True | True이면 모든 채널 가중치 1.0 |
| `vector_sub_intent_enabled` | False | False이면 `semantic` 단일 프로파일 |
| `vector_default_sub_intent` | `"semantic"` | sub_intent 비활성 또는 분류 실패 시 기본값 |

### 검색 성능 측정 지표

- **MRR (Mean Reciprocal Rank)**: 정답이 몇 번째에 나왔는지
- **nDCG@10**: 상위 10개 결과의 순위 품질
- **recall@k (k=1,5,10)**: 봉인 평가셋 기준 (현재 19건 / 목표 80건)
- **Latency**: p50, p95, p99

평가는 `scripts/eval/run_recall.py`로 실행하며, 봉인 평가셋(`scripts/eval/eval_set_holdout.tsv`)만 사용한다. HyQE few-shot 등 어떤 프롬프트도 봉인본을 참조하지 않는다.

---

## 채택하지 않은 대안

**pgroonga + TokenMecab**

PostgreSQL 프로세스 내에서 Groonga의 libmecab 호출 시 사전 경로 인식 실패 문제가 해결되지 않아 폐기했다. apt mecab(일본어 기반)과 mecab-ko 라이브러리 충돌, Groonga 플러그인의 사전 경로 하드코딩 등 디버깅 비용이 과도했다.

**PostgreSQL 내장 FTS (`tsvector`)**

한국어 전용 딕셔너리가 없어 형태소 분석이 불가능하다. `simple` 딕셔너리는 공백 기준 토크나이징만 하며 BM25를 지원하지 않는다.

**Elasticsearch 외부 도입**

PostgreSQL ↔ Elasticsearch 동기화(CDC) 구조 구축 필요. ParadeDB가 동일 BM25 알고리즘을 PostgreSQL 내부에서 제공하므로 도입 ROI가 낮다.

**단일 경쟁 쿼리 (초기 임시)**

`DISTINCT ON (service_id)`로 모든 row_kind를 단일 쿼리로 경쟁시키는 방식은 트랙별 부분 쿼리 + 가중 RRF 도입 시 교체되었다.

**단일 UNION ALL 통합 쿼리 (안 B)**

위 "단일 경쟁 쿼리"와는 별개 안이다. 4채널을 각각 독립 세션으로 병렬 실행하는 현행 대신, 네 트랙의 부분 쿼리를 하나의 `UNION ALL` 문장으로 합쳐 DB 라운드트립을 4회에서 1회로 줄이는 방식을 검토했다. 측정(10질의, reps 3) 결과 latency는 병렬 220ms < 안 B(union) 362ms < 순차 425ms였고, 검색 품질은 세 방식이 동일했다.

병렬이 안 B보다 빠른 이유는 DB가 `UNION ALL` 브랜치를 한 세션 안에서 직렬로 실행해 채널 동시성을 잃기 때문이다. 또한 단일 문장으로 합치면 한 채널의 실패가 전체 쿼리를 실패시켜 채널별 실패 격리도 포기하게 된다. 라운드트립 감소 이득보다 동시성 상실과 격리 포기의 손실이 커 기각하고, 독립 세션 병렬을 유지한다.
