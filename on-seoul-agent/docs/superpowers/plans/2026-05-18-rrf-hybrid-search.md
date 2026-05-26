# RRF Hybrid Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** [Triple-Track Embedding Pipeline](./2026-05-18-triple-track-embedding-pipeline.md)이 적재한 `service_embeddings` 통합 테이블의 Track A(`row_kind='identity'`) / Track B(`row_kind='summary'`) / Track C(`row_kind='question'`) row와 BM25 검색 결과를 **Reciprocal Rank Fusion(RRF)**으로 결합하여 의도별 가중치 프로파일을 적용한다. Router Agent를 `VectorSubIntent`로 세분화 분류하도록 확장한다.

**Scope:** 본 계획은 **검색 단계 업그레이드**를 다룬다. 선행 계획의 종료 시점에는 단일 경쟁 vector + BM25 단순 union으로 검색이 동작하는데, 본 계획에서는 이를 **트랙별 partial 쿼리 + 가중 RRF** 로 재구성한다.

**Architecture:**
- **Router Agent 확장**: `_IntentOutput`에 `vector_sub_intent` 필드 추가 (`identification` / `detail` / `semantic`). 별도 LLM 호출 없이 기존 intent 분류 체인에서 함께 산출.
- **VectorAgent 재구성**: 통합 테이블에서 row_kind별로 3회 vector 쿼리(`WHERE row_kind='identity'/'summary'/'question'`) + `bm25_search` 1회 호출 (병렬 `asyncio.gather`). 결과 4개를 RRF로 결합.
- **RRF 결합**: `core/rrf.py` 모듈에 가중 RRF 함수 작성. service_id 기준 dedup. Track C(question row)는 동일 service_id의 여러 question row 중 최고 rank만 사용하도록 SQL `ROW_NUMBER() OVER (PARTITION BY service_id)` 로 1차 dedup 후 RRF로 전달.
- **가중치 프로파일**: `core/config.py`에 트랙별 가중치 4종(Track A/B/C/BM25) × sub_intent 3종 매트릭스로 분리. 코드에 박지 않는다.
- **단계적 활성화**: 1단계는 비가중치(1:1:1:1) baseline. 2단계는 평가 후 가중치 활성화. 분류 정확도 검증 전까지는 `semantic` default 프로파일 단일 운영.

**전제 조건:**
- `2026-05-18-triple-track-embedding-pipeline.md` 완료 — 통합 `service_embeddings` 테이블에 identity/summary/question row가 적재된 상태.
- 단일 HNSW 인덱스 + BM25 partial index(`WHERE row_kind='identity'`) 가동 중.
- 평가셋 80개 봉인본 사용 가능 (`scripts/eval/eval_set_holdout.tsv`).
- `tools/bm25_search.py` 기존 동작 유지 (도메인 stopword 필터링 포함).

**Tech Stack:** Python 3.13, LangGraph, pgvector + pg_search, pytest

---

## 관련 문서

- 설계 근거: [`RRF-Strategy.md`](./RRF-Strategy.md), [`Embedding-Strategy.md`](./Embedding-Strategy.md)
- 선행 계획: [`2026-05-18-triple-track-embedding-pipeline.md`](./2026-05-18-triple-track-embedding-pipeline.md)
- 영향 받는 기존 문서:
  - `docs/agent-design.md` (Router 분류 표, 흐름도)
  - `docs/hybrid-search-strategy.md` (RRF 쿼리 예시, 트랙 구성)
  - `on-seoul-agent/README.md` (워크플로우 mermaid)

---

## File Map

| 파일 | 역할 | 변경 |
|------|------|------|
| `core/config.py` | 가중치 프로파일, scan_k, RRF 상수(k=60) 등 설정 | 수정 |
| `core/rrf.py` | 가중 RRF 결합 함수 + service_id dedup | 신규 |
| `tools/vector_search.py` | 선행 계획의 단일 경쟁 쿼리를 `row_kind` 파라미터 기반 partial 쿼리로 변경 (`identity` / `summary`). post-filter(`max_class_name`/`area_name`/`service_status`) 인자 복구 | 수정 |
| `tools/question_search.py` | `service_embeddings WHERE row_kind='question'` 에서 유사 질문 검색 → service_id별 최고 rank만 반환 (PARTITION BY dedup) | 신규 |
| `tools/bm25_search.py` | 변경 없음 (기존 partial index 그대로 사용) | — |
| `schemas/state.py` | `AgentState`에 `vector_sub_intent` 추가 (기본 None) | 수정 |
| `agents/router_agent.py` | `_IntentOutput`에 `vector_sub_intent` 필드, 프롬프트에 분류 가이드 추가 | 수정 |
| `agents/vector_agent.py` | 선행 계획의 "단순 union 결합" 을 "4 채널 병렬 호출 + 가중 RRF" 로 재구성 | 수정 |
| `scripts/eval/run_recall.py` | holdout 평가셋으로 recall@k / MRR 측정 | 신규 |
| `scripts/eval/tune_weights.py` | 가중치 그리드 서치 (config 출력만, 자동 반영 X) | 신규 |
| `tests/test_rrf.py` | RRF 단위 테스트 (dedup, 가중치, 빈 결과) | 신규 |
| `tests/test_question_search.py` | question_search dedup 동작 단위 테스트 | 신규 |
| `tests/test_router_subintent.py` | `vector_sub_intent` 분류 단위 테스트 | 신규 |
| `tests/test_vector_agent_hybrid.py` | 4 채널 호출 + RRF 통합 (도구 mock) | 신규 |
| `docs/agent-design.md` | Router 분류 / 폴백 / 흐름도 갱신 | 수정 |
| `docs/hybrid-search-strategy.md` | 결합 대상 4채널, sub_intent 가중치 표 추가 | 수정 |
| `on-seoul-agent/README.md` | 워크플로우 mermaid에 트랙 구조 반영 | 수정 |

---

## Task 1: `core/config.py` — 가중치 프로파일 등 설정

### 추가 설정

```python
# Triple-track + RRF 결합
rrf_k_constant: int = 60                      # RRF 표준 상수
rrf_scan_k_per_track: int = 50                # 각 트랙에서 상위 몇 건씩 가져올지
rrf_top_k_final: int = 10                     # 최종 반환 건수

# Track C 전용 — question은 service_id별 최고 rank 1개만 사용
question_scan_multiplier: int = 3             # service_id dedup 후 부족할 수 있으므로 더 많이 가져옴

# VectorSubIntent 활성화 단계
vector_sub_intent_enabled: bool = False       # Phase 1은 False. 분류 정확도 검증 후 True
vector_default_sub_intent: str = "semantic"   # 비활성 또는 분류 실패 시 사용

# 가중치 프로파일 — sub_intent → {track_a, track_b, track_c, bm25}
# 검증 전 초기값. 평가셋 측정 후 tune_weights.py로 조정.
rrf_weight_profiles: dict[str, dict[str, float]] = {
    "identification": {"track_a": 0.5,  "track_b": 0.25, "track_c": 0.25, "bm25": 0.5},
    "detail":         {"track_a": 0.2,  "track_b": 0.5,  "track_c": 0.3,  "bm25": 0.4},
    "semantic":       {"track_a": 0.15, "track_b": 0.35, "track_c": 0.5,  "bm25": 0.3},
}

# 비가중치 baseline 모드 (Phase 1 측정용)
rrf_unweighted_baseline: bool = True          # True면 모든 가중치 1.0 — 측정 후 False로
```

**Files:**
- Modify: `core/config.py`

- [x] **Step 1: 설정 추가 + 환경변수 로딩 확인**

```bash
uv run python -c "from core.config import settings; print(settings.rrf_weight_profiles['semantic'])"
```

- [x] **Step 2: 린트 + 타입 체크**

```bash
uv run ruff check core/config.py
```

---

## Task 2: `core/rrf.py` — 가중 RRF 결합

### 동작

```python
def reciprocal_rank_fusion(
    channels: dict[str, list[str]],   # {channel_name: [service_id 순위 리스트]}
    *,
    weights: dict[str, float] | None = None,
    k_constant: int = 60,
) -> list[tuple[str, float]]:
    """가중 RRF로 service_id 리스트를 결합한다.

    - weights가 None이면 모든 채널 가중치 1.0.
    - 한 채널에서 같은 service_id가 여러 rank에 등장하면 최고 rank만 사용.
    - 결과는 (service_id, rrf_score) 내림차순 정렬.
    - 빈 채널은 무시한다 (가중치 0과 동등).
    """
```

### RRF 공식

```
rrf_score(service_id) = Σ over channels: weight[c] / (k_constant + rank[c, service_id])
```

**Files:**
- Create: `core/rrf.py`
- Create: `tests/test_rrf.py`

- [x] **Step 1: 테스트 작성 + 구현 + 통과**

```python
class TestReciprocalRankFusion:
    def test_unweighted_basic_merge(self):
        result = reciprocal_rank_fusion({
            "a": ["S1", "S2", "S3"],
            "b": ["S2", "S3", "S4"],
        })
        # S2는 두 채널 모두 상위, S1과 S4는 한쪽만
        ids = [sid for sid, _ in result]
        assert ids[0] == "S2"

    def test_weighted_emphasis(self):
        result = reciprocal_rank_fusion(
            {"a": ["S1", "S2"], "b": ["S2", "S1"]},
            weights={"a": 1.0, "b": 0.1},
        )
        # a의 1위인 S1이 우위
        assert result[0][0] == "S1"

    def test_dedup_within_channel(self):
        # 동일 service_id가 한 채널 안에 중복 등장하면 첫 등장(=최고 rank)만 사용
        result = reciprocal_rank_fusion({"a": ["S1", "S2", "S1"]})
        assert len([sid for sid, _ in result if sid == "S1"]) == 1

    def test_empty_channel_ignored(self):
        result = reciprocal_rank_fusion({"a": ["S1"], "b": []})
        assert result[0][0] == "S1"

    def test_all_empty_returns_empty(self):
        assert reciprocal_rank_fusion({"a": [], "b": []}) == []
```

---

## Task 3: `tools/vector_search.py` — `row_kind` partial 쿼리로 재구성

### 변경 동작

선행 계획에서 단일 경쟁 쿼리(전체 row 대상 + `DISTINCT ON (service_id)`)로 동작하던 `vector_search` 를 `row_kind` 파라미터 기반 partial 쿼리로 바꾼다. identity/summary row는 1:1 관계라 dedup 불필요. **post-filter 인자(max_class_name/area_name/service_status)도 복구**한다 (identity row의 metadata.extracted를 기준으로 필터링).

```python
async def vector_search(
    session,
    query_vector,
    *,
    row_kind: Literal["identity", "summary"] = "identity",
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
) -> list[dict]:
    """service_embeddings WHERE row_kind=:row_kind ORDER BY similarity LIMIT top_k.

    row_kind:
        - "identity": Track A. metadata.extracted post-filter 적용 가능.
        - "summary":  Track B. summary row는 metadata가 NULL이므로 post-filter는 미적용.
    """
```

내부적으로 `_ALLOWED_ROW_KIND = {"identity", "summary"}` 화이트리스트로 SQL 조립. Track C(question)는 dedup 로직이 별개이므로 `question_search` 로 분리.

### SQL 구조 (identity 예시 — post-filter 포함)

```sql
SELECT service_id, embedding_text, metadata, similarity
FROM (
    SELECT
        service_id, embedding_text, metadata,
        1 - (embedding <=> CAST(:q AS vector)) AS similarity
    FROM service_embeddings
    WHERE row_kind = 'identity'
      AND 1 - (embedding <=> CAST(:q AS vector)) >= :min_similarity
    ORDER BY embedding <=> CAST(:q AS vector)
    LIMIT :scan_k
) candidates
WHERE metadata->>'max_class_name' = :max_class_name   -- post-filter
  AND metadata->>'area_name'      = :area_name
LIMIT :top_k;
```

> **summary 쿼리는 WHERE row_kind='summary' 만 다르고, post-filter 절은 metadata가 NULL이라 의미 없으므로 생략한다.** identity row와 summary row의 정합성은 service_id로 보장되며, post-filter는 identity 채널이 책임진다.

**Files:**
- Modify: `tools/vector_search.py`
- Modify: `tests/test_vector_search.py`

- [x] **Step 1: row_kind별 단위 테스트 + 통과**

```python
class TestVectorSearchRowKind:
    async def test_identity_filters_row_kind(self): ...
    async def test_summary_filters_row_kind(self): ...
    async def test_invalid_row_kind_raises(self): ...
    async def test_identity_applies_post_filter(self): ...
    async def test_summary_ignores_post_filter(self): ...    # metadata NULL이므로 효과 없음
```

- [x] **Step 2: 기존 `@pytest.mark.skip(reason="phase-rrf")` 처리되어 있던 post-filter 테스트 활성화**

선행 계획에서 보류했던 post-filter 테스트를 본 단계에서 다시 살린다.

---

## Task 4: `tools/question_search.py` — Track C 검색 + PARTITION BY dedup

### 동작

`service_embeddings` 의 question row 만 대상으로 검색하고, 같은 service_id의 여러 question row가 매칭되면 최고 similarity 1건만 반환한다.

```python
async def question_search(
    session,
    query_vector,
    *,
    scan_k: int = TOP_K * 3,             # service_id dedup 후 부족분 보완
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
) -> list[dict]:
    """service_embeddings WHERE row_kind='question' → service_id별 최고 rank만 반환.

    반환:
        [{service_id, embedding_text, intent_label, similarity}] (service_id unique)
    """
```

### SQL 구조

```sql
WITH ranked AS (
    SELECT
        service_id,
        embedding_text,           -- question_text
        intent_label,
        1 - (embedding <=> CAST(:q AS vector)) AS similarity,
        ROW_NUMBER() OVER (
            PARTITION BY service_id
            ORDER BY embedding <=> CAST(:q AS vector)
        ) AS service_rank
    FROM service_embeddings
    WHERE row_kind = 'question'
      AND 1 - (embedding <=> CAST(:q AS vector)) >= :min_similarity
    ORDER BY embedding <=> CAST(:q AS vector)
    LIMIT :scan_k
)
SELECT service_id, embedding_text, intent_label, similarity
FROM ranked
WHERE service_rank = 1
ORDER BY similarity DESC
LIMIT :top_k;
```

> **post-filter 미적용**: question row는 metadata가 NULL이므로 카테고리/자치구 필터는 identity 채널이 담당한다. Track C는 의미·맥락형 매칭에 집중.

**Files:**
- Create: `tools/question_search.py`
- Create: `tests/test_question_search.py`

- [x] **Step 1: 테스트 + 구현 + 통과**

```python
class TestQuestionSearch:
    async def test_dedup_per_service_id(self, ai_session):
        """한 service_id의 question row 여러 개가 매칭되면 최고 similarity 1건만 반환."""
        ...

    async def test_only_query_question_rows(self, ai_session):
        """row_kind='identity' / 'summary' row는 무시된다."""
        ...

    async def test_min_similarity_filter(self, ai_session): ...
    async def test_returns_intent_label(self, ai_session): ...
```

---

## Task 5: `AgentState` 확장 + Router Agent 분류

### AgentState

```python
# schemas/state.py
class AgentState(TypedDict):
    ...
    vector_sub_intent: str | None  # "identification" | "detail" | "semantic" | None
```

### Router 변경

`_IntentOutput`에 `vector_sub_intent` 필드 추가. intent가 `VECTOR_SEARCH`일 때만 의미 있다 (그 외는 None).

```python
class _IntentOutput(BaseModel):
    intent: IntentType
    refined_query: str | None = None
    max_class_name: str | None = None
    area_name: str | None = None
    service_status: str | None = None
    vector_sub_intent: Literal["identification", "detail", "semantic"] | None = None
```

프롬프트에 다음 가이드 추가:

```
intent가 VECTOR_SEARCH인 경우 vector_sub_intent를 다음 3종 중 하나로 분류하세요.

- identification: 시설명/지역/분류 식별 (예: "마포구 풋살장", "응봉공원 테니스장")
- detail: 요금/취소/시간 등 세부정보 (예: "테니스장 평일 이용료", "취소 며칠 전까지")
- semantic: 활동/체험/맥락 의미 (예: "아이랑 갈 만한 무료 체험", "드론 날릴 수 있는 곳")

intent가 VECTOR_SEARCH가 아니면 vector_sub_intent는 null로 두세요.
```

**Files:**
- Modify: `schemas/state.py`
- Modify: `agents/router_agent.py`
- Create: `tests/test_router_subintent.py`

- [x] **Step 1: AgentState 갱신 + 초기화 회귀 (test_chat_router, fixtures 등)**

- [x] **Step 2: Router 분류 테스트** — LLM mock으로 3 카테고리 예시별 라벨 검증

```python
class TestRouterSubIntent:
    async def test_identification_query(self): ...
    async def test_detail_query(self): ...
    async def test_semantic_query(self): ...
    async def test_non_vector_intent_returns_none(self): ...   # SQL_SEARCH 등
```

- [x] **Step 3: 잘못된 라벨에 대한 fallback** — `vector_sub_intent_enabled=False` 이거나 분류 실패면 `settings.vector_default_sub_intent`("semantic") 사용

---

## Task 6: `VectorAgent` 재구성

> **연계 — chat-search-persistence**: VectorAgent 는 4 채널(`vector_a/b/c`, `bm25`) + RRF 병합 결과 + hydrated `final` 을 `state.search_channels` 에 `ChannelData(kind, query, hits)` 형태로 채널 키별로 노출한다. `rrf` 채널의 query 는 `source_channels` + `weights` + `k_constant` 를 `parameters` 에 기록하고 `query_text` 는 `None`. `final` 채널은 hydration·dedup 이후 사용자 노출 목록을 담는다. `SearchChannel` / `SearchKind` 상수와 `_search_channel_utils._to_hits` 헬퍼를 사용한다. 적재는 종단 `search_persist_node` 가 일괄 처리하므로 VectorAgent 는 채널 dict 만 채우면 된다 (`docs/chat-search-persistence.md` 참조).

### 흐름

```
VectorAgent.search(state, ai_session, data_session):
    # 1. 질의 정제 (기존)
    refined = await self._refine_chain.ainvoke({"message": state["message"]})
    query_vec = await embedder.embed(refined.refined_query)

    # 2. 4 채널 병렬 호출 — 모두 service_embeddings 통합 테이블 대상
    filters = {
        "max_class_name": refined.max_class_name,
        "area_name":      refined.area_name,
        "service_status": refined.service_status,
    }
    a_task = vector_search(ai_session, query_vec, row_kind="identity", **filters)
    b_task = vector_search(ai_session, query_vec, row_kind="summary")       # summary는 post-filter 미적용
    c_task = question_search(ai_session, query_vec)                          # WHERE row_kind='question' + PARTITION BY
    bm25_tokens = tokenize_and_filter(refined.refined_query)
    d_task = bm25_search(ai_session, bm25_tokens) if bm25_tokens else _empty()
    # bm25_search는 BM25 partial index(row_kind='identity')를 자동으로 사용

    a_rows, b_rows, c_rows, d_rows = await asyncio.gather(a_task, b_task, c_task, d_task)

    # 3. RRF 결합
    weights = _resolve_weights(state.get("vector_sub_intent"))
    merged = reciprocal_rank_fusion(
        {
            "track_a": [r["service_id"] for r in a_rows],
            "track_b": [r["service_id"] for r in b_rows],
            "track_c": [r["service_id"] for r in c_rows],
            "bm25":    [r["service_id"] for r in d_rows],
        },
        weights=weights if not settings.rrf_unweighted_baseline else None,
    )

    # 4. Hydration (data_session에서 원본 조회)
    service_ids = [sid for sid, _ in merged[:settings.rrf_top_k_final]]
    hydrated = await hydrate_services(data_session, service_ids)

    return {**state, "vector_results": hydrated, "refined_query": refined.refined_query}
```

### 가중치 결정 함수

```python
def _resolve_weights(sub_intent: str | None) -> dict[str, float]:
    if not settings.vector_sub_intent_enabled:
        return settings.rrf_weight_profiles[settings.vector_default_sub_intent]
    label = sub_intent or settings.vector_default_sub_intent
    return settings.rrf_weight_profiles.get(label, settings.rrf_weight_profiles[settings.vector_default_sub_intent])
```

**Files:**
- Modify: `agents/vector_agent.py`
- Create: `tests/test_vector_agent_hybrid.py`

- [x] **Step 1: 통합 테스트 작성**

```python
class TestVectorAgentHybrid:
    async def test_calls_all_four_channels_in_parallel(self, mocks): ...
    async def test_rrf_merges_and_hydrates(self, mocks): ...
    async def test_empty_bm25_tokens_skips_bm25_channel(self, mocks): ...
    async def test_sub_intent_selects_weight_profile(self, mocks): ...
    async def test_unweighted_baseline_when_flag_set(self, mocks): ...
    async def test_hydration_failure_returns_empty_results(self, mocks): ...
```

- [x] **Step 2: 구현 + 회귀**

기존 단일 트랙 호출 + RRF가 BM25 + 단일 vector였던 코드를 위 흐름으로 교체. `_RefinedQuery`는 그대로 활용.

```bash
uv run pytest tests/test_vector_agent_hybrid.py tests/test_vector_agent.py -v
```

---

## Task 7: 평가 파이프라인 — recall@k / MRR

### 동작

`scripts/eval/run_recall.py` 가 다음을 수행한다.

1. `scripts/eval/eval_set_holdout.tsv` 로드 (질의 + 정답 service_id 목록).
2. 각 질의에 대해 `VectorAgent.search` 실행 (실제 DB 필요).
3. 정답이 결과의 몇 번째에 등장하는지로 recall@1, recall@5, recall@10, MRR 측정.
4. 의도 유형별 분리 측정 (식별/세부정보/의미·맥락).
5. 결과를 `tests/data/eval_results/{timestamp}.json` 으로 저장 (가중치 튜닝 비교용).

### 가중치 그리드 서치

`scripts/eval/tune_weights.py` 는 후보 가중치 조합을 받아 recall@k를 비교하고 추천 조합을 출력한다 (config에 직접 반영하지는 않는다 — 사람이 검수 후 반영).

```bash
uv run python scripts/eval/tune_weights.py \
  --grid "track_a:0.1,0.3,0.5;track_b:0.2,0.4;track_c:0.3,0.5;bm25:0.3,0.5" \
  --metric recall@10 \
  --sub-intent semantic
```

**Files:**
- Create: `scripts/eval/run_recall.py`
- Create: `scripts/eval/tune_weights.py`

- [x] **Step 1: 평가 스크립트 + smoke test**

```bash
uv run python scripts/eval/run_recall.py --limit 10   # holdout 일부만
```

- [x] **Step 2: 봉인 평가셋 노출 금지 검증**

```bash
grep -rn "eval_set_fewshot" scripts/eval/run_recall.py   # 빈 결과여야 함
```

---

## Task 8: 문서 일괄 갱신

다음 문서가 트랙 / sub_intent / RRF 결합 변경에 영향을 받는다. **하나라도 빠지면 정합성이 깨지므로 일괄 처리한다.**

### 영향 받는 문서

- `/Users/vito/study/on-seoul-agent/docs/agent-design.md`
- `/Users/vito/study/on-seoul-agent/docs/architecture.md`
- `/Users/vito/study/on-seoul-agent/on-seoul-agent/README.md`
- `/Users/vito/study/on-seoul-agent/on-seoul-agent/docs/hybrid-search-strategy.md`
- `/Users/vito/study/on-seoul-agent/on-seoul-agent/docs/tools/vector_search.md`
- `/Users/vito/study/on-seoul-agent/on-seoul-agent/tools/README.md`

### Step 1: `docs/agent-design.md`

- [x] **1-1.** Vector Agent 섹션을 "4 채널 병렬 호출 + RRF 결합"으로 갱신
- [x] **1-2.** AgentState 표에 `vector_sub_intent` 행 추가
- [x] **1-3.** Router 분류 표에 `vector_sub_intent` 컬럼 추가 (`identification` / `detail` / `semantic`)

### Step 2: `docs/architecture.md`

- [x] **2-1.** AI Service 섹션 "주요 설계 사항"에 트리플 트랙 + RRF 항목 추가
- [x] **2-2.** 디렉토리 구조에 `tools/question_search.py`, `core/rrf.py` 추가

### Step 3: `on-seoul-agent/README.md`

- [x] **3-1.** mermaid 워크플로우 VECTOR 노드를 "Track A + B + C + BM25 RRF" 로 갱신
- [x] **3-2.** 도구 표에 `question_search` 추가
- [x] **3-3.** 디렉토리 구조에 `core/rrf.py`, `tools/question_search.py` 추가

### Step 4: `on-seoul-agent/docs/hybrid-search-strategy.md`

- [x] **4-1.** "결론" 표를 4채널 구성으로 갱신
- [x] **4-2.** "RRF 결합" SQL 예시를 채널 4개(Track A/B/C/BM25) 기준으로 재작성
- [x] **4-3.** sub_intent 가중치 프로파일 표 추가 (RRF-Strategy.md 인용)
- [x] **4-4.** "조회 전략" 표에 의미/맥락형 → Track C 강조, 세부정보형 → Track B 강조 메모

### Step 5: `on-seoul-agent/docs/tools/vector_search.md`

- [x] **5-1.** `track` 파라미터 설명 추가 (A / B)
- [x] **5-2.** "호출 경로" 섹션 추가 — VectorAgent가 트랙별로 2회 호출함을 명시

### Step 6: `on-seoul-agent/docs/tools/question_search.md` (신규)

- [x] **6-1.** 새 도구 문서 작성: 시그니처, 파라미터 표, dedup 동작 SQL, 사용 예
- [x] **6-2.** `on-seoul-agent/tools/README.md` 도구 표에 `question_search` 행 추가

### Step 7: 정합성 검증

- [x] **7-1.** 모든 문서에서 다음 용어가 일관되게 사용되는지 grep:

```bash
grep -rn "VectorSubIntent\|vector_sub_intent" docs/ on-seoul-agent/docs/ on-seoul-agent/README.md
grep -rn "Track A\|Track B\|Track C" docs/ on-seoul-agent/docs/
```

- [x] **7-2.** `git diff` 일괄 확인

---

## Task 9: 단계적 활성화 + 측정

### Phase 1 (본 계획 종료 시점)

- `rrf_unweighted_baseline=True` — 비가중치 RRF로 운영
- `vector_sub_intent_enabled=False` — Router의 sub_intent 분류는 산출되지만 가중치 적용은 default(`semantic`) 단일
- recall@k / MRR 측정 → `eval_results/baseline.json` 저장

### Phase 2 (별도 실행, 본 계획 후속)

- `tune_weights.py` 로 가중치 그리드 서치
- 추천 가중치를 `core/config.py`에 수동 반영
- `rrf_unweighted_baseline=False` 로 전환
- 회귀 측정

### Phase 3 (별도 실행, sub_intent 분류 정확도 검증 후)

- Router의 sub_intent 분류 정확도를 평가 (별도 라벨링 작업 필요)
- 정확도 ≥ 임계치(예: 80%)면 `vector_sub_intent_enabled=True`
- 의도별 가중치 프로파일 활성화 후 회귀 측정

- [ ] **Step 1: Phase 1 baseline 측정**

```bash
uv run python scripts/eval/run_recall.py --output eval_results/baseline.json
```

- [ ] **Step 2: 측정 결과를 docs/eval/baseline-2026-05-XX.md 에 요약**

---

## 완료 기준 체크리스트

- [x] `core/rrf.py`가 dedup·가중·빈 채널 처리를 모두 통과
- [x] `tools/vector_search.py` 가 `track="A"` / `"B"` 로 분기 (화이트리스트 외 값 거부)
- [x] `tools/question_search.py` 가 service_id별 최고 rank dedup 동작
- [x] Router의 `_IntentOutput` 에 `vector_sub_intent` 포함 (VECTOR_SEARCH 외에는 None)
- [x] `VectorAgent.search` 가 4 채널 병렬 호출 + RRF 결합 + hydration 수행
- [x] `vector_sub_intent_enabled=False` 일 때는 항상 `semantic` 프로파일 사용
- [x] `rrf_unweighted_baseline=True` 일 때는 모든 채널 가중치 1.0
- [ ] 봉인 평가셋 80개로 baseline recall@10 측정 결과 저장됨 (Task 7 — 실 DB 필요)
- [x] 6개 문서 일괄 갱신 (agent-design / architecture / README × 2 / hybrid-search-strategy / vector_search.md + 신규 question_search.md)
- [x] `tools/README.md` 표에 `question_search` 행 추가

---

## 사전 확정 사항

1. **선행 의존**: 본 계획은 [임베딩 파이프라인 계획](./2026-05-18-triple-track-embedding-pipeline.md) 완료를 전제로 한다. Track B/C 컬럼·테이블이 적재되지 않은 상태에서는 4 채널 검색이 동작하지 않는다.
2. **단계적 활성화**: Phase 1은 비가중치 + default 프로파일. 가중치 활성화·sub_intent 활성화는 평가셋 측정과 분류 정확도 검증 후 단계적으로 진행한다.
3. **가중치는 config 분리**. 코드에 박지 않으며, 평가셋(80개 봉인본)으로 측정한 후 사람이 수동 반영한다.
4. **봉인 평가셋은 평가 코드에서만 사용**. HyQE few-shot 등 어떤 프롬프트도 봉인본을 참조하지 않는다 (선행 계획에서 격리 검증).
5. **HyDE는 본 계획에 포함되지 않는다**. Phase 2 후속 계획에서 다룬다.
6. **FAQ 검색 분기는 본 계획에 포함되지 않는다**. 현재 FAQ 6건 규모로 별도 인프라가 미구축이며, 데이터 규모가 늘어날 때 별도 계획에서 다룬다.
7. **service_id 중복 처리 위치**: Track C 내부 dedup은 `question_search` SQL이 처리, 채널 간 dedup은 `core/rrf.py`가 처리한다. 두 단계 모두 service_id 기준이다.
