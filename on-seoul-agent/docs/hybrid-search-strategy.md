# 하이브리드 검색 전략

## 개요

> 서울시 공공데이터 AI 분석 서비스(on-seoul)의 시맨틱 검색(pgvector) + BM25(pg_search) 결합 검색 시스템 설계 및 운영 기준을 정리한다.

---

## 결론

|검색 유형|담당 도구|역할|
|---|---|---|
|의미 기반, 부분 키워드, 동의어|pgvector + Gemini embedding|의미가 비슷한 시설 탐색|
|정확한 키워드 매칭, 고유명사|pg_search BM25 + korean_lindera|정확한 명칭/희귀어 매칭|
|최종 순위 결정|RRF (Reciprocal Rank Fusion)|두 결과 통합|

---

## 배경

### 인프라 스택

ParadeDB(`paradedb/paradedb:latest`) 단일 이미지에서 두 확장이 함께 동작한다.

- **벡터 검색:** `vector` (pgvector) 0.8.1 - 임베딩 벡터 저장 + 코사인 유사도 검색
- **키워드 검색:**`pg_search` 0.23.4  BM25 인덱싱 + Lindera 한국어 토크나이저

---

## 데이터 모델

기준 테이블은 `service_embeddings`다. `embed_metadata.py` 배치가 `public_service_reservations`의 시설 메타데이터를 임베딩하여 적재한다.

```sql
CREATE TABLE service_embeddings (
    id          BIGSERIAL PRIMARY KEY,
    service_id  VARCHAR(255)  NOT NULL UNIQUE,
    service_name TEXT          NOT NULL,
    metadata    JSONB,
    embedding   vector(768),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);
```

- `embedding` — 시맨틱 검색 (pgvector)
- `service_name`, `metadata` — BM25 검색 (pg_search)
- `service_id` — 결과 조인 키

---

## 인덱스 전략

### 시맨틱 검색용: HNSW

```sql
CREATE INDEX idx_service_embeddings_hnsw
    ON service_embeddings
    USING hnsw (embedding vector_cosine_ops);
```

데이터 적재 후 추가한다. HNSW는 빌드 비용이 크지만 검색 속도가 빠르다. 예약서비스의 *메타데이터는 일 단위로 갱신되므로 비용을 감수하고 도입할 가치가 있다.*

### BM25 검색용: bm25 인덱스

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
    );
```

`korean_lindera`는 KoDic 사전 기반으로 한국어 형태소 분석을 수행한다. `metadata` JSONB는 `json_fields`로 별도 처리한다.

---

## 토크나이저 선택: korean_lindera

### 동작 방식

- "강남 근처 무료 문화행사" → "강남", "근처", "무료", "문화", "행사"
- "서울시 공원 입장료" → "서울시", "공원", "입장료"
- "한강공원 따릉이 대여소" → "한강공원", "따릉이", "대여소"

`서울시`, `한강공원`은 KoDic 사전에 고유명사로 등록되어 있어 단일 토큰으로 유지된다. `문화행사`는 합성어로 분리된다.

### 한계와 대응

`서울` 단독 검색 시 `서울시`, `서울역` 매칭이 불가능하다. 이는 BM25의 토큰 정확 매칭 특성상 정상 동작이며, 다음 두 가지 방식으로 보완한다.

1. **하이브리드 구조에서 시맨틱이 보완** `서울` 입력 시 시맨틱 검색이 `서울시 공원`, `서울역 인근 시설` 등 의미적으로 관련된 문서를 찾아준다.
2. **접두사 검색으로 명시적 확장**
    
    ```sql
    SELECT * FROM service_embeddings WHERE service_name @@@ '서울*';
    ```
    

### 커스텀 사전 적용 검토

`pg_search`에서 `korean_lindera`의 KoDic 사전은 Rust 바이너리에 내장되어 있어 런타임 교체가 불가능하다. 도메인 용어("따릉이", "한강공원" 등) 추가가 필요하면 사전 자체가 이미 KoDic이라 다수의 서울 지명/공공시설 용어가 포함되어 있다. 추가가 필요한 용어에 대해서는 AI 서비스의 에이전트(또는 tool)에서 쿼리 확장으로 처리한다.

---

## 임베딩 전략

### 현황

`gemini-embedding-2-preview`를 사용한다. `output_dimensionality=768`으로 설정한다.

- 모델: `models/gemini-embedding-2-preview`
- 차원: 768
- 거리 함수: 코사인 유사도 (`<=>`)

**768차원을 채택한 이유**: Gemini `text-embedding-004`는 MRL(Matryoshka Representation Learning) 구조로 768 / 1536 / 3072차원을 모두 지원한다. 첫 768차원이 가장 밀도 높은 의미 정보를 담으며, 이 프로젝트의 임베딩 문서(시설명·카테고리·지역 조합, 수십 단어 수준)에서는 768이 의미 손실 없이 충분하다. 저장 공간과 HNSW 검색 속도 모두 1536 대비 유리하다. 품질 평가(Phase 15) 결과에 따라 1536으로 올릴 수 있다.

> 차원을 변경하면 `service_embeddings` 테이블 재생성과 전체 재인덱싱이 필요하다.

### 벡터화 대상 및 임베딩 텍스트 구성

전체 시설 목록 조회는 SQL이 담당하고, 챗봇의 자연어 의미 검색을 위해서만 시설 정보를 벡터화한다.

- **유사 시설 검색:** 시설명 + 카테고리 + 지역 → "강남구 체육시설 테니스장"
- **의미 기반 매칭** 행사 설명 텍스트 → "어린이 대상 문화강좌..."
- **가이드 답변:** FAQ / 예약 안내 문서 → "접수 방법, 취소 정책..."

임베딩 입력은 단순 컬럼값이 아니라 **검색 의도에 맞춰 재구성한 텍스트**다. 예를 들어 `service_name`만 임베딩하면 "강남 체육시설"이라는 질의가 "강남구 테니스장 1면"과 매칭되지 않는다. 카테고리/지역 정보를 함께 합쳐 의미 공간을 풍부하게 만든다.

```python
# embed_metadata.py 의 임베딩 텍스트 구성 예시
embedding_text = f"{area_name} {category} {service_name}"
# 예: "강남구 체육시설 테니스장 마루공원 1면"
```

`metadata` JSONB에는 원본 정보를 그대로 저장하여 BM25 검색과 결과 표시에 활용한다.

---

## 조회 전략 — SQL vs 벡터 vs 하이브리드 분기

질의 유형에 따라 다른 도구를 사용한다. Router Agent가 사용자 의도를 분류해 적절한 도구를 선택한다.

|질의 유형|예시|조회 방식|
|---|---|---|
|상태/날짜 필터형|"지금 접수 중인 수영장 알려줘"|**SQL** — `status = 'OPEN'`|
|지역 필터형|"강남구 체육시설 목록"|**SQL** — `area_nm = '강남구'`|
|의미/맥락형|"어린이랑 같이 갈 만한 문화행사"|**벡터 검색** — 임베딩 유사도|
|고유명사형|"따릉이 대여소"|**BM25** — 정확 매칭|
|복합형|"미세먼지 심할 때 실내 시설 추천"|**벡터 → SQL 필터** 조합|
|가이드 문의형|"예약 취소 어떻게 해?"|**벡터** — FAQ 문서 검색|

벡터 검색이 필요한 모든 케이스에서 BM25를 함께 실행하여 RRF로 결합하는 것이 기본이다. 단, 고유명사형(따릉이 등)은 BM25가 명확히 우세하므로 시맨틱을 후보 확장 용도로만 활용한다.

---

## 운영 고려사항

### 배치 임베딩 적재

`embed_metadata.py`가 신규/변경된 시설 메타데이터를 Gemini API로 임베딩하여 `service_embeddings`에 upsert한다. BM25 인덱스는 PostgreSQL 트랜잭션 내에서 자동 갱신되므로 별도 작업이 불필요하다.

### 인덱스 빌드 순서

1. 데이터 적재 (임베딩 포함)
2. HNSW 인덱스 생성 (`embedding` 컬럼)
3. BM25 인덱스 생성 (`service_name`, `metadata`)

데이터 적재 전에 인덱스를 만들면 적재 속도가 느려진다.

### 검색 성능 측정 지표

- **MRR (Mean Reciprocal Rank)**: 정답이 몇 번째에 나왔는지
- **nDCG@10**: 상위 10개 결과의 순위 품질
- **Latency**: p50, p95, p99
- **Zero-result rate**: 결과 없음 비율

`chat_messages` 테이블의 USER/ASSISTANT 페어에서 위 지표를 산출한다.

---

## 검색 시나리오별 동작

### 시나리오 1: 의미/맥락형 — 벡터 우세

**쿼리**: "어린이랑 같이 갈 만한 문화행사" **Router 판단**: 의미/맥락형 → 벡터 + BM25 하이브리드

|검색기|결과 품질|이유|
|---|---|---|
|시맨틱|높음|"어린이", "함께 갈 만한" 같은 맥락 의도를 임베딩이 포착|
|BM25|중간|"어린이", "문화", "행사" 토큰 매칭은 되지만 "같이 갈 만한"의 의도는 못 잡음|
|하이브리드|높음|시맨틱이 주도, BM25가 정확 토큰 매칭으로 정밀도 보완|

### 시나리오 2: 상태/날짜 필터형 — SQL 단독

**쿼리**: "지금 접수 중인 수영장 알려줘" **Router 판단**: 정형 조건 → SQL 단독

```sql
SELECT * FROM public_service_reservations
WHERE status = 'OPEN' AND service_name LIKE '%수영장%'
  AND now() BETWEEN reservation_start_at AND reservation_end_at;
```

벡터/BM25를 거치지 않는다. 정형 필터에는 SQL이 가장 빠르고 정확하다. 하이브리드 검색을 강제하면 오히려 노이즈가 늘어난다.

### 시나리오 3: 복합형 — 벡터 post-filter

**쿼리**: "미세먼지 심할 때 실내 시설 추천" **Router 판단**: VECTOR_SEARCH → `vector_search` 도구 호출

`vector_search`는 post-filter 전략을 기본으로 사용한다. 전체 임베딩에서 유사도 상위 `scan_k`를 먼저 뽑고, 서브쿼리 외부에서 metadata 필터를 적용한다. pgvector HNSW 인덱스는 WHERE 조건과 동시에 동작하지 않아 pre-filter를 적용하면 sequential scan으로 빠지기 때문이다.

```sql
-- $1 = 임베딩 벡터, $2 = min_similarity, scan_k = top_k × 5
SELECT service_id, service_name, metadata, similarity
FROM (
    SELECT
        service_id,
        service_name,
        metadata,
        1 - (embedding <=> $1::vector) AS similarity
    FROM service_embeddings
    WHERE 1 - (embedding <=> $1::vector) >= $2
    ORDER BY embedding <=> $1::vector
    LIMIT :scan_k          -- HNSW 인덱스 활용, 전체 대상 검색
) candidates
WHERE metadata->>'max_class_name' = :max_class_name  -- post-filter (None이면 미적용)
  AND metadata->>'area_name'      = :area_name
LIMIT :top_k;
```

pre-filter 대비 장점: HNSW 인덱스를 전체 데이터에 적용하므로 의미적으로 가장 가까운 결과를 놓치지 않는다. `scan_k`(`top_k × 5`, 기본 50건)를 충분히 크게 잡아 필터 탈락으로 인한 결과 부족을 완충한다.

---

## 검색 쿼리 구조

### 시맨틱 검색 (post-filter)

`vector_search` 도구가 실행하는 쿼리다. 전체 임베딩에서 유사도 상위 `scan_k`를 먼저 추출하고, 서브쿼리 외부에서 metadata 필터를 적용한다. 필터가 없으면 외부 WHERE 절은 생략된다.

```sql
-- $1 = 임베딩 벡터, $2 = min_similarity
-- scan_k = top_k × SCAN_K_MULTIPLIER (기본 50)
SELECT service_id, service_name, metadata, similarity
FROM (
    SELECT
        service_id,
        service_name,
        metadata,
        1 - (embedding <=> $1::vector) AS similarity
    FROM service_embeddings
    WHERE 1 - (embedding <=> $1::vector) >= $2
    ORDER BY embedding <=> $1::vector
    LIMIT :scan_k                      -- HNSW 인덱스로 전체 대상 검색
) candidates
-- post-filter: 필터 파라미터가 있을 때만 조건 추가
WHERE metadata->>'area_name' = :area_name
LIMIT :top_k;
```

### BM25 검색 (단독)

```sql
-- 사용자 질의 텍스트를 $1에 바인딩
SELECT
    service_id,
    service_name,
    paradedb.score(id) AS bm25_score
FROM service_embeddings
WHERE service_name @@@ $1 OR metadata @@@ $1
ORDER BY paradedb.score(id) DESC
LIMIT 50;
```

### 하이브리드 검색: RRF 결합

두 결과의 순위를 합산하여 최종 순위를 결정한다. RRF는 점수 스케일이 다른 검색 결과를 결합할 때 표준적으로 쓰는 방식이다.

```sql
-- $1 = 임베딩 벡터, $2 = 텍스트 쿼리
WITH semantic AS (
  SELECT
    service_id,
    ROW_NUMBER() OVER (ORDER BY embedding <=> $1::vector) AS rank
  FROM service_embeddings
  ORDER BY embedding <=> $1::vector
  LIMIT 50
),
keyword AS (
  SELECT
    service_id,
    ROW_NUMBER() OVER (ORDER BY paradedb.score(id) DESC) AS rank
  FROM service_embeddings
  WHERE service_name @@@ $2 OR metadata @@@ $2
  LIMIT 50
)
SELECT
  COALESCE(s.service_id, k.service_id) AS service_id,
  COALESCE(1.0 / (60 + s.rank), 0) +
  COALESCE(1.0 / (60 + k.rank), 0) AS rrf_score
FROM semantic s
FULL OUTER JOIN keyword k USING (service_id)
ORDER BY rrf_score DESC
LIMIT 10;
```

`60`은 RRF 표준 상수(k)로, 상위 결과의 영향력을 조정한다. 이 값이 작으면 1위 가중치가 강해지고, 크면 결과가 평탄해진다. 60이 일반적인 기본값이다.

---

### 그 외 채택하지 않은 대안

**pgroonga + TokenMecab**

- 도입 시도했으나 PostgreSQL 프로세스 내에서 Groonga의 libmecab 호출 시 _사전 경로 인식 실패 문제가 해결되지 않아_ 폐기했다. apt mecab(일본어 기반)과 mecab-ko 라이브러리 충돌, Groonga 플러그인의 사전 경로 하드코딩 등 디버깅 비용이 과도했다.

**PostgreSQL 내장 FTS (`tsvector`)**

- _한국어 전용 딕셔너리가 없어_ 형태소 분석이 불가능하다. `simple` 딕셔너리는 공백 기준 토크나이징만 한다. BM25 미지원.

**Elasticsearch 외부 도입**

- PostgreSQL ↔ Elasticsearch 동기화(CDC) 구조 구축 필요. 추가 인프라 구축 및 관리는 ROI 효율이 낮음. ParadeDB가 동일 BM25 알고리즘을 PostgreSQL 내부에서 제공하므로 도입 불필요하다고 판단.