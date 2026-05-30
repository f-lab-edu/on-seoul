# 하이브리드 검색 전략

## 개요

> 서울시 공공데이터 AI 분석 서비스(on-seoul)의 시맨틱 검색(pgvector) + BM25(pg_search) 결합 검색 시스템 설계 및 운영 기준을 정리한다.

---

## 결론

|검색 유형|담당 채널|역할|
|---|---|---|
|의미 기반, 식별 메타데이터 매칭 (Track A)|`vector_search(row_kind="identity")`|시설 신원 임베딩 (서울시 자치구 체육시설 테니스장...)|
|의미 기반, 자연어 설명 매칭 (Track B)|`vector_search(row_kind="summary")`|시설 요약 임베딩 (자연어 설명 중심)|
|HyQE 질문 매칭 (Track C)|`question_search()`|예상 질문 임베딩, service_id별 dedup|
|정확 키워드 매칭|`bm25_search()`|고유명사·희귀어 BM25 매칭|
|최종 순위 결합|`core/rrf.py` RRF|4채널 결과를 Reciprocal Rank Fusion으로 통합|

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
    service_id  VARCHAR(255)  NOT NULL,
    row_kind    VARCHAR(20)   NOT NULL DEFAULT 'identity', -- 'identity' | 'summary' | 'question'
    service_name TEXT          NOT NULL,
    metadata    JSONB,
    embedding   vector(768),
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE (service_id, row_kind)
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

`pg_search`에서 `korean_lindera`의 KoDic 사전은 Rust 바이너리에 내장되어 있어 런타임 교체가 불가능하다. `user_dictionary` 지정은 SQL API 레벨에서 지원되지 않으며, 커스텀 사전을 적용하려면 ParadeDB 소스 빌드가 필요하다 ([관련 소스](https://github.com/paradedb/paradedb/blob/main/tokenizers/src/lindera.rs)).

**우회 전략: Python 레이어 쿼리 토크나이징 (Phase 14)**

DB에서 색인 시 `korean_lindera`가 자동으로 형태소 분석하는 것은 그대로 두고, **쿼리 전송 전에 Python에서 lindera-py로 사전 토크나이징**하는 방식으로 우회한다.

```python
# llm/tokenizer.py
from lindera_py import Tokenizer

DOMAIN_TOKENS = {"따릉이", "한강공원", "세빛섬"}  # KoDic 미등록 도메인 용어

def tokenize_query(text: str) -> list[str]:
    tokenizer = Tokenizer.from_config({"dictionary": {"kind": "KoDic"}})
    tokens = [t.text for t in tokenizer.tokenize(text)]
    # 도메인 용어 원문 보존: 토크나이징으로 분리된 경우 원문 추가
    if text in DOMAIN_TOKENS:
        tokens = [text] + tokens
    return tokens
```

토큰 목록을 `bm25_search` 도구에 전달하면 ParadeDB BM25 조건으로 변환한다. 이때 Tantivy 파서가 특수하게 해석하는 문자와 논리 예약어는 사전에 제거하여 의도치 않은 쿼리 동작과 인젝션을 방지한다.

```python
# tools/bm25_search.py — 토큰 배열 → BM25 검색 조건 변환
_BM25_SPECIAL = re.compile(r'[+\-:?\\*~^(){}\[\]"]')          # 접두사·퍼지·필드한정·이스케이프 등
_BM25_RESERVED = frozenset({"AND", "OR", "NOT", "TO", "IN"})   # Tantivy 논리 예약어

def build_bm25_query(tokens: list[str]) -> str:
    safe = []
    for t in tokens:
        t = _BM25_SPECIAL.sub("", t)            # 특수문자 제거
        if t and t.upper() not in _BM25_RESERVED:  # 예약어 필터 (대소문자 무관)
            safe.append(t)
    return " ".join(safe)  # ParadeDB @@@: 공백 구분 토큰 OR 매칭

# SQL 조건
# WHERE service_name @@@ $1 OR metadata @@@ $1
# $1 = "따릉이 대여소" → "따릉이", "대여소" 개별 매칭
```

모든 토큰이 특수문자·예약어로 제거되어 쿼리 문자열이 비게 되면 `bm25_search`는 DB 호출 없이 빈 결과를 반환한다. 빈 쿼리를 ParadeDB에 보내면 에러 또는 전체 스캔 폴백이 발생할 수 있기 때문이다.

이 방식의 장점: Rust 바이너리 빌드 없이 Python 코드 수정만으로 도메인 용어를 즉시 추가할 수 있다. 색인 측(`pg_search`)과 쿼리 측(Python) 토크나이저를 동일 KoDic 기반으로 맞춰 토큰 불일치를 최소화한다.

### 도메인 공통어 stopword 필터 (BM25 전용)

서비스 전반에 걸쳐 거의 모든 문서에 등장하는 어휘(`예약`, `서울`, `서울시`, `공공`, `서비스`, `공공서비스`, `접수`, `신청`, `이용`, `안내`, `시설`, `프로그램`)는 BM25의 IDF가 ≈ 0이 되어 점수 변별력에 기여하지 못한다. 오히려 RRF 결합 시 노이즈를 늘릴 수 있어, `VectorAgent.search`에서 BM25 호출 직전에 stopword 목록으로 필터링한다.

```python
# agents/vector_agent.py
_BM25_STOPWORDS = frozenset({
    "예약", "서울", "서울시", "공공", "서비스", "공공서비스",
    "접수", "신청", "이용", "안내", "시설", "프로그램",
})

bm25_tokens = [t for t in tokens if t not in _BM25_STOPWORDS]
if bm25_tokens:
    bm25_rows = await bm25_search(bm25_tokens, ai_session)
else:
    # 유효 BM25 토큰이 없으면 BM25를 건너뛰고 벡터 단독 결과로 진행
    bm25_rows = []
```

`bm25_search` 도구 자체는 순수 검색 함수로 유지하고, 도메인 공통어 필터링은 호출 측(애플리케이션 레이어)에서 책임진다. 색인 시점의 stopword 제거가 아니므로 BM25 인덱스는 그대로 두며, 쿼리 시점에만 변별력 없는 토큰을 차단한다.

---

## 임베딩 전략

### 현황

`gemini-embedding-2-preview`를 사용한다. `output_dimensionality=768`으로 설정한다.

- 모델: `models/gemini-embedding-2-preview`
- 차원: 768
- 거리 함수: 코사인 유사도 (`<=>`)

**768차원을 채택한 이유**: Gemini `text-embedding-004`는 MRL(Matryoshka Representation Learning) 구조로 768 / 768 / 3072차원을 모두 지원한다. 첫 768차원이 가장 밀도 높은 의미 정보를 담으며, 이 프로젝트의 임베딩 문서(시설명·카테고리·지역 조합, 수십 단어 수준)에서는 768이 의미 손실 없이 충분하다. 저장 공간과 HNSW 검색 속도 모두 768 대비 유리하다. 품질 평가(Phase 15) 결과에 따라 768으로 올릴 수 있다.

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
|의미/맥락형|"어린이랑 같이 갈 만한 문화행사"|**벡터 검색** — 임베딩 유사도 → Track C 강조|
|고유명사형|"따릉이 대여소"|**BM25** — 정확 매칭|
|복합형|"미세먼지 심할 때 실내 시설 추천"|**벡터 → SQL 필터** 조합|
|가이드 문의형|"예약 취소 어떻게 해?"|**벡터** — FAQ 문서 검색 → Track B/C 강조|

벡터 검색이 필요한 모든 케이스에서 BM25를 함께 실행하여 RRF로 결합하는 것이 기본이다. 단, 고유명사형(따릉이 등)은 BM25가 명확히 우세하므로 시맨틱을 후보 확장 용도로만 활용한다.

---

## VectorSubIntent 가중치 프로파일

Router Agent가 분류한 `vector_sub_intent`에 따라 4채널 가중치가 달라진다.
Phase 1에서는 `rrf_unweighted_baseline=True`로 모든 채널 1.0을 사용한다.
Phase 3에서 분류 정확도 ≥ 80% 검증 후 활성화 예정.

| sub_intent | Track A (identity) | Track B (summary) | Track C (question) | BM25 |
|---|---|---|---|---|
| `identification` | 0.5 | 0.25 | 0.25 | 0.5 |
| `detail` | 0.2 | 0.5 | 0.3 | 0.4 |
| `semantic` | 0.15 | 0.35 | 0.5 | 0.3 |

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
-- $1 = 임베딩 벡터, $2 = min_similarity, scan_k = top_k × SCAN_K_MULTIPLIER(기본 5)
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
WHERE metadata->>'max_class_name' = :max_class_name  -- post-filter (None이면 절 생략)
  AND metadata->>'area_name'      = :area_name
  AND metadata->>'service_status' = :service_status
LIMIT :top_k;
```

pre-filter 대비 장점: HNSW 인덱스를 전체 데이터에 적용하므로 의미적으로 가장 가까운 결과를 놓치지 않는다. `scan_k`(`top_k × SCAN_K_MULTIPLIER`, 기본 50건)를 충분히 크게 잡아 필터 탈락으로 인한 결과 부족을 완충한다.

필터 파라미터(`max_class_name`, `area_name`, `service_status`)는 `VectorAgent`의 질의 정제 단계에서 LLM이 추출한다. `_RefinedQuery` 스키마에 필터 필드를 포함시켜 질의 정제와 필터 추출을 한 번의 LLM 호출로 처리한다. 필터가 None이면 해당 조건 절은 SQL에 포함되지 않는다.

---

## 원본 데이터 Hydration

`service_embeddings`는 의미 검색의 인덱스로만 사용한다. 답변 생성에 필요한 컨텍스트는 RRF 결합 후 `service_id`를 키로 `public_service_reservations` 원본 테이블에서 다시 조회하여 채운다. 임베딩 시점에 스냅샷된 `service_status`·`receipt_start_dt`·`receipt_end_dt` 등이 stale 상태로 답변에 들어가는 것을 막기 위함이다.

### 컬럼 책임 분리

| 용도 | 출처 | 컬럼 예시 |
|---|---|---|
| 의미 검색 (임베딩 입력) | `service_embeddings.embedding` | `service_name`, `max_class_name`, `min_class_name`, `area_name`, `place_name`, `target_info`, `detail_content` (앞 300자) |
| 키워드 검색 (BM25) | `service_embeddings.service_name`, `service_embeddings.metadata` | `service_name`, `metadata` JSONB |
| 답변 표시 (Hydration) | `public_service_reservations` | 모든 표시 컬럼 — 특히 자주 바뀌는 `service_status`, `receipt_*_dt`, `service_url` |

**원칙**: 임베딩 metadata는 검색 후처리(post-filter 등)에만 쓰고, 사용자에게 노출되는 표시 값은 항상 원본 테이블에서 가져온다.

### Hydration 흐름

```text
VectorAgent.search()
  1. 질의 정제 + 임베딩
  2. vector_search(row_kind="identity")  → Track A
  3. vector_search(row_kind="summary")   → Track B
  4. question_search()                   → Track C (service_id별 dedup)
  5. bm25_search()                       → BM25
  6. reciprocal_rank_fusion → service_id 리스트
  7. hydrate_services(data_session, service_ids)
     - public_service_reservations에서 WHERE service_id = ANY(:service_ids) AND deleted_at IS NULL
     - 입력 순서(RRF 순위) 유지
     - 원본 누락분 자동 제외
  8. rrf_score 병합 후 vector_results에 할당
  9. search_channels 에 vector_a/b/c / bm25 / rrf / final 6채널 ChannelData 노출
     → 종단 search_persist_node 가 chat_search_queries + chat_search_results 일괄 적재
```

> **검색 결과 적재 (Phase 19)**: 각 검색 노드가 채운 `state.search_channels` 는 그래프 종단부에서 `search_persist_node` 가 `chat_search_queries` (입력 — 무엇으로 검색했는가) + `chat_search_results` (출력 — 무엇이 반환됐는가) 두 테이블에 단일 트랜잭션으로 적재한다. RRF 채널은 `query_text=NULL` + `parameters` 에 source_channels/weights 기록, `final` 채널은 hydration 직후의 실제 사용자 노출 목록을 담는다. 자세한 스키마·분석 쿼리·운영 정책은 [`docs/chat-search-persistence.md`](chat-search-persistence.md) 참조.

### 임베딩 ↔ 원본 동기화 정책

수집 스케줄러는 매일 1회 `service_change_log`에 변경분을 기록한다. 임베딩 재생성 트리거는 다음과 같다:

| 변경된 필드 | 임베딩 재생성 | 사유 |
|---|---|---|
| `service_name`, `max_class_name`, `min_class_name`, `area_name`, `place_name`, `target_info`, `detail_content` (앞 300자) | **필요** | 의미 공간 자체가 달라짐 |
| `service_status`, `receipt_*_dt`, `service_open_*_dt`, `service_url`, `payment_type` | 불필요 | Hydration이 매 답변마다 최신 값을 끌어오므로 임베딩 갱신 불필요 |

현재 `scripts/embed_metadata.py --incremental` 은 `service_embeddings` 에 아직 적재되지 않은 신규 `service_id` 만 임베딩한다. 의미 컬럼이 변경된 기존 행의 재임베딩 트리거는 현 시점에 자동화되어 있지 않으며, `service_change_log` 기반 변경분 감지는 향후 과제(Phase 19 예정)다. 임시 우회로 변경된 행은 수동으로 삭제 후 `--incremental` 재실행한다.

### 누락·실패 처리

| 상황 | 처리 |
|---|---|
| 임베딩엔 있지만 원본 테이블에 service_id 미존재 | 결과에서 제외 (검색 결과 누락으로 인지) |
| 원본 행이 soft-delete (`deleted_at IS NOT NULL`) | 결과에서 제외 |
| `hydrate_services` 자체가 예외 (DB 다운 등) | `vector_results = []`로 fallback. stale metadata로 답변하지 않는다. Self-correction이 빈 답변을 재시도로 전환할 수 있다. |

---

## 검색 쿼리 구조

### 시맨틱 검색 (post-filter)

`vector_search` 도구가 실행하는 쿼리다. 전체 임베딩에서 유사도 상위 `scan_k`를 먼저 추출하고, 서브쿼리 외부에서 metadata 필터를 적용한다. 필터가 없으면 외부 WHERE 절은 생략된다.

```sql
-- $1 = 임베딩 벡터, $2 = min_similarity
-- scan_k = top_k × SCAN_K_MULTIPLIER (기본 SCAN_K_MULTIPLIER=5, scan_k=50)
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
-- post-filter: 필터 파라미터가 있을 때만 조건 추가 (None이면 해당 절 생략)
WHERE metadata->>'max_class_name' = :max_class_name
  AND metadata->>'area_name'      = :area_name
  AND metadata->>'service_status' = :service_status
LIMIT :top_k;
```

필터 파라미터 추출 흐름:

1. `VectorAgent.search`에서 LLM이 사용자 질의를 정제할 때 `_RefinedQuery`로 `refined_query`와 함께 `max_class_name`, `area_name`, `service_status`를 함께 추출한다.
2. None이 아닌 필터만 `vector_search` 키워드 인자로 전달하며, None인 필터는 SQL WHERE 절에 포함되지 않는다.
3. 필터 값은 항상 bind 파라미터로 전달하여 SQL injection을 방지한다.

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

4채널 결과의 순위를 합산하여 최종 순위를 결정한다. RRF는 점수 스케일이 다른 검색 결과를 결합할 때 표준적으로 쓰는 방식이다.

```python
# core/rrf.py 활용 예시
from core.rrf import reciprocal_rank_fusion

merged = reciprocal_rank_fusion(
    {
        "track_a": [r["service_id"] for r in a_rows],  # identity
        "track_b": [r["service_id"] for r in b_rows],  # summary
        "track_c": [r["service_id"] for r in c_rows],  # question
        "bm25":    [r["service_id"] for r in d_rows],
    },
    weights=None,  # Phase 1: 비가중치 baseline (rrf_unweighted_baseline=True)
    k_constant=60,
)
```

`k_constant=60`은 RRF 표준 상수로, 상위 결과의 영향력을 조정한다. 이 값이 작으면 1위 가중치가 강해지고, 크면 결과가 평탄해진다. 60이 일반적인 기본값이다.

---

### 그 외 채택하지 않은 대안

**pgroonga + TokenMecab**

- 도입 시도했으나 PostgreSQL 프로세스 내에서 Groonga의 libmecab 호출 시 _사전 경로 인식 실패 문제가 해결되지 않아_ 폐기했다. apt mecab(일본어 기반)과 mecab-ko 라이브러리 충돌, Groonga 플러그인의 사전 경로 하드코딩 등 디버깅 비용이 과도했다.

**PostgreSQL 내장 FTS (`tsvector`)**

- _한국어 전용 딕셔너리가 없어_ 형태소 분석이 불가능하다. `simple` 딕셔너리는 공백 기준 토크나이징만 한다. BM25 미지원.

**Elasticsearch 외부 도입**

- PostgreSQL ↔ Elasticsearch 동기화(CDC) 구조 구축 필요. 추가 인프라 구축 및 관리는 ROI 효율이 낮음. ParadeDB가 동일 BM25 알고리즘을 PostgreSQL 내부에서 제공하므로 도입 불필요하다고 판단.