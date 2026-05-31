# 하이브리드 검색 전략

## 개요

서울시 공공데이터 AI 분석 서비스(on-seoul)의 시맨틱 검색(pgvector) + BM25(pg_search) 결합 검색 시스템 설계 및 운영 기준을 정리한다.

검색은 **4채널**로 구성된다. `service_embeddings` 테이블에 `row_kind` 디스크리미네이터로 트랙을 분리하고, `VectorAgent`가 4채널을 순차 실행한 뒤 가중 RRF(Reciprocal Rank Fusion)로 결합한다.

> **ANALYTICS intent 제외**: ANALYTICS intent는 RRF/벡터 검색 경로와 무관한 별도 정형 집계 경로다 — `analytics_search` 도구가 GROUP BY COUNT / DISTINCT 쿼리를 직접 실행하며 임베딩·RRF 가중치·hydration 단계를 거치지 않는다.

---

## 결론

| 채널 | 담당 도구 | 트랙 | 역할 |
|---|---|---|---|
| Track A | `vector_search(row_kind='identity')` | 시설 신원 텍스트 | 지역·분류·시설명 기반 식별 검색. post-filter 적용 가능 |
| Track B | `vector_search(row_kind='summary')` | LLM 구조화 요약 | 요금·운영시간·시설 특성 등 세부 의미 검색 |
| Track C | `question_search` | HyQE 예상 질문 | 사용자 질문 패턴과 직접 매칭. service_id별 최고 rank dedup |
| BM25 | `bm25_search` | `row_kind='identity'` partial index | 정확한 명칭·고유명사 키워드 매칭 |
| 최종 순위 | `core/rrf.py` | — | 4채널 결과를 가중 RRF로 통합 |

---

## 배경

### 인프라 스택

ParadeDB(`paradedb/paradedb:latest`) 단일 이미지에서 두 확장이 함께 동작한다.

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
    service_name  TEXT          NOT NULL,  -- 모든 row에 복제 (디버깅·JOIN 비용 절감)
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

모든 row_kind가 단일 HNSW 인덱스에 공존한다. `vector_search`·`question_search`는 `WHERE row_kind = :row_kind` 조건을 추가하여 트랙별 partial 쿼리를 실행한다. 전체 row를 대상으로 HNSW가 동작한 뒤 외부에서 row_kind를 필터링하는 post-filter 전략이다.

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

summary·question row의 텍스트가 BM25 IDF를 오염시키지 않도록 identity row만 색인한다. `korean_lindera`는 KoDic 사전 기반 형태소 분석을 수행한다.

### Hydration·재적재용: service_id B-tree

```sql
CREATE INDEX idx_service_embeddings_service_id
    ON service_embeddings (service_id);
```

---

## 임베딩 전략

### 모델

- 모델: `models/gemini-embedding-2-preview`
- 차원: 768 (코사인 유사도 기준)

### 3-트랙 임베딩 파이프라인

`scripts/embed_metadata.py` 오케스트레이터가 `scripts/tracks/` 모듈을 호출하여 시설당 세 트랙을 순서대로 적재한다.

#### Track A — identity (항상 적재)

```python
embedding_text = f"{area_name} {max_class_name} {min_class_name} {service_name} {place_name}"
# 예: "강남구 체육시설 테니스장 마루공원 1면"
```

지역·분류·시설명 조합. 검색 필터(`max_class_name`, `area_name`, `service_status`)는 이 row의 `metadata`에 저장되며 post-filter에서만 사용한다.

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

## 토크나이저: korean_lindera

BM25 색인과 Python 쿼리 토크나이저를 동일 KoDic 기반으로 맞춰 토큰 불일치를 최소화한다.

### 동작 방식

- "강남 근처 무료 문화행사" → "강남", "근처", "무료", "문화", "행사"
- "한강공원 따릉이 대여소" → "한강공원", "따릉이", "대여소"

### 한계와 대응

`서울` 단독 검색 시 `서울시`, `서울역` 매칭이 불가능하다. 이는 BM25의 토큰 정확 매칭 특성상 정상 동작이며, 하이브리드 구조에서 Track A/B/C 시맨틱 채널이 보완한다.

### Python 레이어 쿼리 토크나이징

DB 색인 시 `korean_lindera`가 형태소 분석하는 것은 그대로 두고, BM25 쿼리 전송 전에 Python에서 `lindera-py`로 사전 토크나이징한다.

```python
# tools/tokenizer.py
from lindera_py import Tokenizer

DOMAIN_TOKENS = {"따릉이", "한강공원", "세빛섬"}  # KoDic 미등록 도메인 용어

def tokenize_query(text: str) -> list[str]:
    tokenizer = Tokenizer.from_config({"dictionary": {"kind": "KoDic"}})
    tokens = [t.text for t in tokenizer.tokenize(text)]
    if text in DOMAIN_TOKENS:
        tokens = [text] + tokens
    return tokens
```

토큰 목록은 BM25 쿼리 조건으로 변환하기 전에 특수문자·예약어를 제거한다.

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
  1. Router가 refined_query·post-filter 산출 → refine 체인 skip (중복 LLM 호출 방지)
     아니면 질의 정제 체인 호출 (_RefinedQuery)
  2. 정제된 질의 임베딩
  3. lindera-py 토크나이징 + stopword 필터
  4. 4채널 순차 실행 (asyncpg 단일 세션 — 동시 쿼리 불가):
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

### VectorSubIntent 가중치 프로파일

Router Agent가 `vector_sub_intent`(`identification` / `detail` / `semantic`)를 분류하면 `VectorAgent`가 해당 프로파일의 채널별 가중치로 RRF를 실행한다. 현재 운영값은 `rrf_unweighted_baseline=True`(비가중치, 모든 채널 1.0) + `vector_sub_intent_enabled=False`(`semantic` 단일 프로파일)다.

가중치 프로파일 수치, sub_intent 분류 기준, 단계적 활성화(Phase 1/2/3) 절차는 [RRF 결합 전략](superpowers/plans/RRF-Strategy.md)에서 단일 관리한다. 수치 원본은 `core/config.py`의 `rrf_weight_profiles`다.

---

## 검색 쿼리 구조

### Track A — identity 벡터 검색 (post-filter)

```sql
-- :row_kind = 'identity', :scan_k = settings.rrf_scan_k_per_track (기본 50)
SELECT service_id, embedding_text, metadata, similarity
FROM (
    SELECT
        service_id, embedding_text, metadata,
        1 - (embedding <=> CAST(:query_vector AS vector)) AS similarity
    FROM service_embeddings
    WHERE row_kind = 'identity'
      AND 1 - (embedding <=> CAST(:query_vector AS vector)) >= :min_similarity
    ORDER BY embedding <=> CAST(:query_vector AS vector)
    LIMIT :scan_k
) candidates
-- post-filter: None이면 해당 절 생략 (asyncpg AmbiguousParameterError 방지)
WHERE metadata->>'max_class_name' = :max_class_name
  AND metadata->>'area_name'      = :area_name
  AND metadata->>'service_status' = :service_status
ORDER BY similarity DESC
LIMIT :top_k;
```

### Track B — summary 벡터 검색 (post-filter 미적용)

Track A와 쿼리 구조가 동일하지만 `WHERE row_kind = 'summary'`이고 post-filter 절이 없다. summary row는 `metadata`가 NULL이므로 카테고리·자치구 필터는 Track A 채널이 담당한다.

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
      AND 1 - (embedding <=> CAST(:query_vector AS vector)) >= :min_similarity
    ORDER BY service_id, embedding <=> CAST(:query_vector AS vector)
) ranked
ORDER BY similarity DESC
LIMIT :top_k;
```

같은 service_id의 question row가 여러 개 매칭되더라도 `DISTINCT ON (service_id)`로 최고 유사도 1건만 RRF에 전달한다. 내부 정렬은 service_id 순이므로, 서브쿼리로 감싸 바깥에서 `similarity DESC`로 최종 정렬한다.

> 구버전은 `ROW_NUMBER()` 윈도우 함수 + `scan_k` LIMIT 조합이었으나, 윈도우 함수가 HNSW ANN 최적화를 막아 `DISTINCT ON`으로 전환했다. 전환 후 실행 계획이 단순해지고 Execution Time이 소폭 개선됐다(`tools/question_search.py` 도크스트링 참조).

### BM25 검색 (identity partial index)

```sql
-- BM25 인덱스는 WHERE row_kind='identity' partial index
SELECT
    service_id,
    service_name,
    paradedb.score(id) AS bm25_score
FROM service_embeddings
WHERE (service_name @@@ :bm25_query OR metadata @@@ :bm25_query)
  AND row_kind = 'identity'
ORDER BY paradedb.score(id) DESC
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

### 누락·실패 처리

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
| `rrf_scan_k_per_track` | 50 | 트랙별 HNSW 스캔 건수 (post-filter 탈락 완충) |
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

**단일 경쟁 쿼리 (Phase 1 임시)**

`DISTINCT ON (service_id)`로 모든 row_kind를 단일 쿼리로 경쟁시키는 방식은 트랙별 부분 쿼리 + 가중 RRF 도입 시 교체되었다.
