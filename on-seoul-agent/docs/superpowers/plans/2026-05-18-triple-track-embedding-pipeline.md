# Triple-Track Embedding Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 임베딩 품질을 끌어올리기 위해 `service_embeddings`를 단일 트랙에서 **삼중 트랙(Track A/B/C)**으로 확장하고, `detail_content`를 사전 정제 → LLM 구조화 추출 → 색인의 파이프라인으로 재구성한다.

**Scope:** 본 계획은 **색인 단계만** 다룬다. 검색 단계(RRF 결합, Router 확장, VectorSubIntent)는 [별도 계획](./2026-05-18-rrf-hybrid-search.md)에서 다룬다.

**Architecture:**
- **통합 테이블 + row_kind 디스크리미네이터**: `service_embeddings`를 row-per-vector 구조로 재정의. 각 행은 `row_kind ∈ {'identity', 'summary', 'question'}` 와 `embedding_text`(실제 임베딩한 텍스트)를 함께 보관. 별도 테이블 없이 모든 트랙이 같은 테이블에 들어간다.
- **사전 정제**: `detail_content`에서 "3. 상세내용" ~ "4. 주의사항" 구간만 추출하여 boilerplate(60~70%)를 제거. 후속 LLM 처리의 입력 품질을 결정한다.
- **Track A (`row_kind='identity'`)**: `embedding_text = "{area_name} {max_class_name} {min_class_name} {service_name} {place_name}"`. 추출 결과와 무관하게 항상 적재.
- **Track B (`row_kind='summary'`)**: LLM이 7개 필드(`fee`·`operating_hours`·`cancellation`·`facilities`·`capacity`·`restrictions`·`summary`)를 산출하여 identity row의 `metadata.extracted`에 저장하고, `embedding_text = extracted.summary` 로 summary row 1건 INSERT. `cleaned_detail`이 비면 메타 컬럼만으로 LLM 호출, LLM 실패 시 summary row를 만들지 않음(템플릿 합성 폴백 없음 — Track A 클론이 되어 변별력 훼손).
- **Track C (`row_kind='question'`, HyQE)**: LLM이 시설당 5~15개 예상 질문 생성 → 각 질문이 question row가 됨. `embedding_text = question_text`, `intent_label` 함께 저장.
- **검색 (Phase 1)**: **RRF 없이** 단일 vector 쿼리로 모든 row_kind를 경쟁시키고 `DISTINCT ON (service_id)`로 dedup. 측정 후 가중 RRF 도입은 후속 계획에서 다룬다.
- **평가셋 분리**: 100개 중 80개 봉인(recall@k/MRR 전용), 20개는 HyQE few-shot용.

**진행 순서**: ① 임베딩 방식 개편 (본 계획) → ② RRF 결합 도입 (후속) → ③ HyDE 도입 (Phase 2).

**전제 조건:**
- `service_embeddings`는 이미 존재(vector(768), HNSW 인덱스, BM25 인덱스 포함).
- `gemini-embedding-2-preview` (768차원) 사용 중.
- ParadeDB(`pg_search` 0.23.4 + pgvector 0.8.1) 인프라 가동 중.
- 본 계획에서 임베딩 모델 자체는 변경하지 않는다.

**Tech Stack:** Python 3.13, asyncpg/SQLAlchemy async, Gemini API (LLM + embedding), pytest

---

## 관련 문서

- 설계 근거: [`Embedding-Strategy.md`](./Embedding-Strategy.md)
- 후속 계획: [`2026-05-18-rrf-hybrid-search.md`](./2026-05-18-rrf-hybrid-search.md) — Track B/C 활용한 검색·결합 단계

## 본 계획의 범위와 위임

본 계획은 **색인 + 단일 경쟁 검색 (Phase 1)** 까지 다룬다. 검색 측에서는 RRF 없이 단일 vector 쿼리로 row_kind를 경쟁시키는 최소 변경만 한다.

| 항목 | 처리 |
|---|---|
| 통합 테이블 DDL, Track A/B/C 적재 | **본 계획** |
| `vector_search` 가 row_kind 인자 없이 단일 쿼리로 dedup하도록 변경 | **본 계획** |
| BM25 채널 — 기존 동작 유지(identity row 대상 partial index) | **본 계획** |
| `VectorAgent` 가 단일 vector 결과 + BM25 결과를 단순 union/intersection으로 결합 | **본 계획** (RRF 도입 전 임시) |
| Router Agent의 `VectorSubIntent` 분류 | RRF 계획 |
| `_RefinedQuery`의 `intent_type` 필드 추가 | RRF 계획 |
| 가중 RRF 결합 + 트랙별 partial query | RRF 계획 |
| HyDE 도입 | HyDE 계획 (Phase 3) |
| FAQ 검색 인프라 | 별도 계획 (현재 데이터 6건) |

---

## File Map

| 파일 | 역할 | 변경 |
|------|------|------|
| `scripts/ddl/service_embeddings.sql` | `service_embeddings`를 row-per-vector 통합 테이블로 재정의하는 전용 DDL 파일 — 컬럼/제약/인덱스 + 모든 항목에 `COMMENT ON ...` 주석 | 신규 |
| `scripts/ddl_chat_entities.sql` | 기존 정의에서 `service_embeddings` 부분 제거 + 새 DDL 파일을 `\i scripts/ddl/service_embeddings.sql` 으로 include | 수정 |
| `tools/vector_search.py` | 단일 경쟁 쿼리 + `DISTINCT ON (service_id)` dedup. row_kind 인자 없음 (트랙별 partial 쿼리는 RRF 계획에서 도입) | 수정 |
| `agents/vector_agent.py` | 단일 vector 결과 + BM25 결과를 단순 union으로 결합 (RRF 미사용, 임시 결합) | 수정 |
| `scripts/cleaning/detail_content.py` | "3. 상세내용" ~ "4. 주의사항" 마커 기반 정제 모듈 | 신규 |
| `llm/extractor.py` | `detail_content` 정제본 → 구조화 추출 (Pydantic 스키마 + LLM 호출) | 신규 |
| `llm/hyqe.py` | 시설 메타데이터 + 정제본 → 5~15개 예상 질문 생성 (의도 분포 강제) | 신규 |
| `llm/prompts/extraction.py` | 구조화 추출 프롬프트 + few-shot | 신규 |
| `llm/prompts/hyqe.py` | HyQE 프롬프트 + 평가셋 20개 few-shot | 신규 |
| `scripts/tracks/__init__.py` | 트랙 모듈 패키지 | 신규 |
| `scripts/tracks/_shared.py` | 공통 헬퍼 — `delete_rows_by_service_id`, `_insert_row` SQL 템플릿 | 신규 |
| `scripts/tracks/identity.py` | **Track A 전담** — `embed_and_insert_identity()`. 식별 텍스트 합성 + 임베딩 + identity row INSERT | 신규 |
| `scripts/tracks/summary.py` | **Track B 전담** — `embed_and_insert_summary()`. `extracted.summary` 임베딩 + summary row INSERT (호출 전제: extracted is not None) | 신규 |
| `scripts/tracks/questions.py` | **Track C 전담** — `embed_and_insert_questions()`. HyQE 생성 + N개 question row 임베딩·INSERT. 성공 여부 bool 반환 | 신규 |
| `scripts/embed_metadata.py` | 얇은 오케스트레이터로 축소 — 정제 / 추출 / 트랙 모듈 호출 / 실패 로깅 / `--track {A,B,C,all}` · `--retry-failed` 옵션 분기 | 수정 |
| `scripts/eval/eval_set.py` | 평가셋 100개를 80(봉인) / 20(few-shot)으로 분리하는 freeze 스크립트 | 신규 |
| `scripts/eval/eval_set_holdout.tsv` | 봉인 평가셋 80개 (학습/프롬프트 노출 금지) | 신규 |
| `scripts/eval/eval_set_fewshot.tsv` | HyQE few-shot용 20개 | 신규 |
| `llm/embedding_config.py` | 임베딩 파이프라인 도메인 상수 — `MIN_CHARS`, `HYQE_QUESTIONS_PER_SERVICE`, `HYQE_INTENT_DISTRIBUTION`, `EXTRACTION_MODEL`, `EXTRACTION_MAX_RETRIES`, 마커 문자열 등 | 신규 |
| `tests/test_detail_content_cleaning.py` | 마커 정제 단위 테스트 | 신규 |
| `tests/test_extractor.py` | 구조화 추출 + 폴백 동작 단위 테스트 | 신규 |
| `tests/test_hyqe.py` | HyQE 생성 + 분포 검증 단위 테스트 | 신규 |
| `tests/test_track_identity.py` | identity 트랙 단위 테스트 (텍스트 합성, INSERT 컬럼 매핑) | 신규 |
| `tests/test_track_summary.py` | summary 트랙 단위 테스트 (extracted 미존재 시 가드, 호출 계약) | 신규 |
| `tests/test_track_questions.py` | questions 트랙 단위 테스트 (HyQE 성공/실패, N개 row 적재, intent_label 일관성) | 신규 |
| `tests/test_embed_metadata_pipeline.py` | 오케스트레이터 end-to-end — 트랙 모듈은 mock하고 호출 순서·조건만 검증, 별도로 실제 DB 통합 시나리오 1개 | 신규 |
| `routers/embeddings.py` | `POST /embeddings/services/sync` 핸들러 + `_run_services_sync` 백그라운드 워커 | 신규 |
| `schemas/embeddings.py` | `ServiceEmbeddingsSyncRequest` / `ServiceEmbeddingsSyncResponse` Pydantic 모델 | 신규 |
| `main.py` | embeddings 라우터 등록 | 수정 |
| `tests/test_embeddings_services_sync_router.py` | `POST /embeddings/services/sync` 라우터 검증·enqueue 테스트 | 신규 |
| `tests/test_embeddings_services_sync_worker.py` | `_run_services_sync` 워커 동시성·실패격리·트랙 모듈 위임 테스트 | 신규 |
| `docs/embedding-pipeline.md` | 운영 가이드 (재적재 절차, 트랙별 옵션, 비용 예측, `POST /embeddings/services/sync` 사용법) | 신규 |

---

## Task 1: DDL — `service_embeddings`를 row-per-vector 통합 테이블로 재정의

### DDL 위치

전용 파일 **`scripts/ddl/service_embeddings.sql`** 에 작성한다. 본 계획 내에 SQL을 인라인하지 않는다 (가독성 + 실제 적용 파일과 분리). `scripts/ddl_chat_entities.sql` 은 새 파일을 `\i` 로 include한다.

### 스키마 요약

| 컬럼 | 타입 | 비고 |
|---|---|---|
| `id` | BIGSERIAL PK | BM25 `key_field` 용 정수 PK |
| `service_id` | VARCHAR(255) | `public_service_reservations.service_id` 와 동일 |
| `row_kind` | VARCHAR(16) | `identity` / `summary` / `question` (CHECK 화이트리스트) |
| `idx` | SMALLINT | question row의 순번. 그 외 0 |
| `service_name` | TEXT | **모든 row에 복제** (디버깅·JOIN 비용 절감) |
| `embedding_text` | TEXT | **모든 row에 기록** — 실제 임베딩에 사용된 텍스트 |
| `embedding` | vector(768) | 코사인 유사도 기준 |
| `metadata` | JSONB | identity row에만. `extracted` 키 포함 (BM25 색인 대상) |
| `intent_label` | VARCHAR(32) | question row에만. `semantic` / `detail` / `keyword` |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

### 제약

- `UNIQUE (service_id, row_kind, idx)` — UPSERT 충돌 키
- `CHECK row_kind IN ('identity','summary','question')` — 화이트리스트
- `CHECK (row_kind='question') = (intent_label IS NOT NULL)` — intent_label과 question row 동치 강제

### 인덱스

- **단일 HNSW** (`embedding vector_cosine_ops`) — 모든 row_kind가 경쟁하는 Phase 1 단일 쿼리에 사용
- **service_id B-tree** — hydration / 재적재 DELETE
- **BM25 partial** (`WHERE row_kind='identity'`) — summary/question 텍스트의 IDF 오염 방지

### COMMENT 정책

테이블·모든 컬럼·모든 인덱스·모든 CHECK/UNIQUE 제약에 `COMMENT ON ...` 을 단다. 이유:

1. **`\d+` 출력에 즉시 보이므로** 신규 합류자가 별도 문서 없이도 스키마 의도를 파악할 수 있다.
2. **마이그레이션 도구·ORM introspection** (예: SQLAlchemy reflection)이 코멘트를 자동 노출한다.
3. 의도가 코드와 함께 git에 남는다.

코멘트 작성 원칙:

- 테이블: 한 줄로 "무엇을 저장하며 어떻게 검색되는지"
- 컬럼: 값 범위·트랙별 의미·NULL 의미를 한 줄에
- 제약: 비즈니스 룰 의도 (예: "intent_label은 question row와 정확히 동치")
- 인덱스: 어떤 검색 패턴을 위한 것인지

### 설계 원칙 (재명시)

- `service_name` 모든 row 복제 — 디버깅 + JOIN 비용 절감
- `embedding_text` 모든 row 기록 — 검색 품질의 근본 변수 가시화
- `metadata.extracted` 는 identity row에만 — canonical 위치 1곳, BM25 IDF 오염 차단
- 단일 HNSW (Phase 1) — RRF 도입 시 row_kind별 partial 추가 여부는 후속 계획에서 결정
- BM25 identity partial — 의미·세부 검색은 벡터 채널이 담당

### 기존 데이터 마이그레이션

기존 `service_embeddings`는 1행/시설 구조라 새 구조로 in-place ALTER가 불가능하다. `DROP TABLE` 후 재적재한다.

```bash
# 백업 (롤백 대비)
pg_dump "$ON_AI_DSN" -t service_embeddings > backup_service_embeddings.sql

# 새 스키마 적용
psql "$ON_AI_DSN" -f scripts/ddl_chat_entities.sql

# 전체 재적재 (Task 6)
uv run python scripts/embed_metadata.py --all
```

**Files:**
- Create: `scripts/ddl/service_embeddings.sql` (전용 DDL — 스키마·제약·인덱스·모든 COMMENT 포함)
- Modify: `scripts/ddl_chat_entities.sql` (기존 `service_embeddings` 정의 제거 + `\i scripts/ddl/service_embeddings.sql` include)

- [ ] **Step 1: `scripts/ddl/service_embeddings.sql` 작성**

위 "스키마 요약" / "제약" / "인덱스" / "COMMENT 정책" 을 모두 반영한 단일 파일을 생성한다. 각 항목별 `COMMENT ON TABLE`·`COMMENT ON COLUMN`·`COMMENT ON CONSTRAINT`·`COMMENT ON INDEX` 진술을 모두 포함한다.

- [ ] **Step 2: `scripts/ddl_chat_entities.sql` 정리**

기존 `service_embeddings` CREATE/ALTER/INDEX 문을 제거하고 새 DDL 파일을 include한다.

```sql
-- scripts/ddl_chat_entities.sql 안
\i scripts/ddl/service_embeddings.sql
```

기존 정의는 git history에 맡기고 백업 주석으로 남기지 않는다.

- [ ] **Step 3: 백업 후 적용**

```bash
pg_dump "$ON_AI_DSN" -t service_embeddings > /tmp/backup_service_embeddings.sql
psql "$ON_AI_DSN" -f scripts/ddl_chat_entities.sql
psql "$ON_AI_DSN" -c "\d+ service_embeddings"   # COMMENT 포함 출력
```

Expected: `\d+` 출력에 모든 컬럼의 코멘트가 한국어로 표시. UNIQUE/CHECK 제약 표시. HNSW 1개 + BM25 partial 1개 + service_id B-tree 1개.

- [ ] **Step 4: COMMENT 적재 검증**

```sql
-- 모든 컬럼에 코멘트가 달렸는지 확인
SELECT column_name,
       col_description('service_embeddings'::regclass, ordinal_position) AS comment
FROM information_schema.columns
WHERE table_name = 'service_embeddings'
ORDER BY ordinal_position;
-- 모든 row의 comment 컬럼이 NULL이 아니어야 한다.

-- 테이블 코멘트
SELECT obj_description('service_embeddings'::regclass);

-- 인덱스 코멘트
SELECT i.relname, obj_description(i.oid, 'pg_class')
FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid
WHERE x.indrelid = 'service_embeddings'::regclass;
```

- [ ] **Step 5: CHECK 제약 동작 검증**

```sql
-- intent_label 없는 question row insert → 실패해야 함
INSERT INTO service_embeddings (service_id, row_kind, idx, service_name, embedding_text, embedding)
  VALUES ('X', 'question', 0, 'x', 'x', '[0,0,...]'::vector(768));

-- 잘못된 row_kind → 실패해야 함
INSERT INTO service_embeddings (service_id, row_kind, idx, service_name, embedding_text, embedding)
  VALUES ('X', 'invalid', 0, 'x', 'x', '[0,0,...]'::vector(768));
```

---

## Task 2: `detail_content` 사전 정제 모듈

### 동작

```python
# scripts/cleaning/detail_content.py

START_MARKER = "3. 상세내용"
END_MARKER = "4. 주의사항"

def clean_detail_content(raw: str | None) -> str:
    """detail_content에서 boilerplate를 제거하고 변별 정보 구간만 반환.

    - 시작 마커가 없으면 원문 전체를 그대로 반환한다 (fallback).
    - 종료 마커가 없으면 시작 마커 이후 끝까지 반환한다.
    - 길이 0이면 빈 문자열 반환. (빈문자의 경우, detail_content 없이 LLM 요청을 처리한다.)
    """
```

### 운영 모니터링

마커 누락률을 로그로 추적한다. 적재 배치 종료 시 다음 형태로 출력:

```
[clean] processed=412 marker_hit=387 (94.0%) fallback_used=25 (6.0%) empty=0
```

5% 이상이 fallback으로 빠지면 마커 패턴을 재검토하라는 신호.

**Files:**
- Create: `scripts/cleaning/__init__.py`
- Create: `scripts/cleaning/detail_content.py`
- Create: `tests/test_detail_content_cleaning.py`

- [ ] **Step 1: 테스트 작성**

```python
class TestCleanDetailContent:
    def test_extracts_section_between_markers(self):
        from scripts.cleaning.detail_content import clean_detail_content
        raw = (
            "1. 공공시설 준수사항\n... boilerplate ...\n"
            "2. 시설예약 안내\n... boilerplate ...\n"
            "3. 상세내용\n반딧불이 체험 프로그램\n운영시간 14:00-16:00\n"
            "4. 주의사항\n... 약관 ..."
        )
        result = clean_detail_content(raw)
        assert "반딧불이" in result
        assert "boilerplate" not in result
        assert "약관" not in result

    def test_missing_start_marker_returns_raw(self):
        from scripts.cleaning.detail_content import clean_detail_content
        raw = "마커가 없는 평문 콘텐츠"
        assert clean_detail_content(raw) == raw

    def test_missing_end_marker_takes_until_end(self):
        from scripts.cleaning.detail_content import clean_detail_content
        raw = "1. ...\n2. ...\n3. 상세내용\n실제 내용\n끝까지"
        result = clean_detail_content(raw)
        assert "실제 내용" in result and "끝까지" in result

    def test_none_returns_empty_string(self):
        from scripts.cleaning.detail_content import clean_detail_content
        assert clean_detail_content(None) == ""

    def test_empty_string_returns_empty(self):
        from scripts.cleaning.detail_content import clean_detail_content
        assert clean_detail_content("") == ""
```

- [ ] **Step 2: 테스트 실패 확인**

```bash
uv run pytest tests/test_detail_content_cleaning.py -v
```

- [ ] **Step 3: 구현**

`scripts/cleaning/detail_content.py`에 위 동작 명세대로 구현.

- [ ] **Step 4: 통과 + 린트**

```bash
uv run pytest tests/test_detail_content_cleaning.py -v
uv run ruff check scripts/cleaning/
```

---

## Task 3: Track B 전처리 — 구조화 추출 + 요약 (메타데이터 폴백 포함)

### 설계 결정: 구조화 추출 = Track B 전처리

`metadata.extracted.summary`가 Track B 임베딩의 단일 입력이므로 **구조화 추출 단계와 Track B는 1:1 결합 관계**다. 두 단계를 별도 Task로 두면 호출 경계만 늘어나고 단독으로 의미가 없다. 따라서 본 Task는 "Track B 전처리"로 통합한다.

`metadata.extracted`의 나머지 필드(`fee`·`operating_hours`·`cancellation`·`facilities`·`capacity`·`restrictions`)는 BM25 색인 대상이지만, BM25는 Track B 임베딩과 동일한 row에 함께 적재되므로 의존성 충돌이 없다. `--track A` 단독 실행 시에는 본 단계가 호출되지 않으며, Track A는 `metadata.extracted`를 입력으로 사용하지 않으므로 깨지지 않는다.

### 추출 스키마

```python
# llm/extractor.py
from pydantic import BaseModel, Field

class ExtractedMetadata(BaseModel):
    fee: str | None = Field(default=None, description="이용료 (평일/주말 구분 포함)")
    operating_hours: str | None = Field(default=None, description="운영시간")
    cancellation: str | None = Field(default=None, description="취소 규정 (며칠 전까지 가능, 환불 비율 등)")
    facilities: list[str] = Field(default_factory=list, description="부대시설")
    capacity: str | None = Field(default=None, description="수용 인원")
    restrictions: list[str] = Field(default_factory=list, description="이용 제한")
    summary: str = Field(description="시설 한 줄 요약 (Track B 임베딩 입력)")
```

### 동작 — 두 갈래 경로 + LLM 실패 시 skip

`cleaned_detail` 의 유무에 따라 두 갈래로 분기한다. 두 경로 모두 LLM을 호출하며, 사용 가능한 입력만으로도 의미 있는 `ExtractedMetadata` 를 산출한다. **LLM 호출이 실패하면 템플릿 합성으로 덮지 않고 `None`을 반환**하여 Track B/C 적재 자체를 건너뛴다.

| 입력 상태 | 경로 | 입력 컬럼 | 채울 수 있는 필드 |
|---|---|---|---|
| `cleaned_detail` 충분 (≥ `MIN_CHARS`) | **full extraction** | `cleaned_detail` + 메타 필드 | 7개 모두 시도 |
| `cleaned_detail` 비어있음 또는 짧음 | **metadata-only fallback** | `service_name`·`area_name`·`max_class_name`·`min_class_name`·`place_name`·`target_info`·`payment_type` | `fee`(payment_type 매핑), `restrictions`(target_info), `summary` 만 |
| 위 두 경로 모두 LLM 호출 실패 | **skip** | — | `None` 반환 |

`operating_hours`·`cancellation`·`facilities`·`capacity` 는 메타데이터만으로는 derive 불가이므로 metadata-only 경로에서는 `None` / `[]` 로 둔다.

### 설계 결정: 템플릿 합성 폴백을 두지 않는 이유

LLM 실패 시 `f"{area_name} {max_class_name} {min_class_name} {service_name} {place_name}"` 같은 템플릿으로 `summary`를 합성하는 안을 고려했으나 채택하지 않는다.

1. **Track A의 클론이 된다** — Track A의 임베딩 입력 텍스트와 거의 동일하다. 같은 의미 공간에 두 벡터가 들어가 검색 다양성이 사라진다.
2. **RRF 가중 왜곡** — Track A와 Track B가 비슷한 결과를 반환하면 해당 service_id의 rrf_score가 인위적으로 올라간다.
3. **변별력 0** — 같은 카테고리·자치구의 시설들이 거의 동일한 합성 summary를 갖게 되어 코사인 유사도 분포가 평탄해진다.

대신 NULL로 두고 다음 스케줄러 실행에서 재시도한다. 그 사이에도 Track A + Track C + BM25가 해당 시설을 검색 가능하게 유지한다.

### 시그니처

```python
async def extract_metadata(
    *,
    service_name: str,
    area_name: str | None = None,
    max_class_name: str | None = None,
    min_class_name: str | None = None,
    place_name: str | None = None,
    target_info: str | None = None,
    payment_type: str | None = None,
    cleaned_detail: str,
    llm_client: LlmClient,
) -> ExtractedMetadata | None:
    """사전 정제된 detail_content + 메타데이터 컬럼에서 구조화 정보를 추출한다.

    경로:
        cleaned_detail 충분 → full extraction (detail_content 기반, 7개 필드 시도)
        cleaned_detail 비어있음/짧음 → metadata-only fallback (LLM 호출 유지, summary는 항상 채움)
        LLM 호출 실패 → None 반환 (호출자가 Track B/C skip + extraction_failed.tsv 로깅)
    """
```

### 프롬프트 (두 변형)

`llm/prompts/extraction.py` 에 두 프롬프트를 함께 둔다.

**`EXTRACTION_PROMPT_FULL`** — `cleaned_detail` 이 있을 때

```
당신은 서울시 공공서비스 예약 시설의 안내문을 구조화하는 보조자입니다.

[입력]
- 시설명: ...
- 자치구: ...
- 분류: {max_class_name} > {min_class_name}
- 안내문 (사전 정제됨): ...

[지시]
다음 7개 항목을 추출하여 JSON으로 반환하세요.
- 추출할 정보가 안내문에 없는 항목은 null 또는 빈 리스트로 두십시오.
- summary는 반드시 채우십시오 (시설명·카테고리·핵심 특징을 한 줄로).
- 추측하지 마십시오. 안내문에 없는 정보를 생성하지 마십시오.
```

**`EXTRACTION_PROMPT_METADATA_ONLY`** — `cleaned_detail` 이 비어있을 때

```
당신은 서울시 공공서비스 예약 시설을 짧게 요약하는 보조자입니다. 상세 안내문이
없으므로 주어진 메타데이터만으로 작업합니다.

[입력]
- 시설명: ...
- 자치구: ...
- 분류: {max_class_name} > {min_class_name}
- 장소: {place_name}
- 대상 정보: {target_info}
- 결제 유형: {payment_type}   (유료 / 무료 등)

[지시]
- summary: 위 메타데이터로 시설을 한 줄로 자연스럽게 요약하세요. (자치구·분류·시설명·대상을 포함)
- fee: payment_type이 "무료"면 "무료", "유료"면 "유료(상세 미공개)", 그 외 null.
- restrictions: target_info에 명시된 대상 제한이 있으면 항목별로 분해해 리스트로. 없으면 빈 리스트.
- operating_hours / cancellation / facilities / capacity: 모두 null 또는 빈 리스트로 두십시오.
- 안내문이 없으므로 절대 시간·요금·환불 비율 등을 임의로 만들지 마십시오.
```

두 프롬프트 모두 `with_structured_output(ExtractedMetadata)` 형태로 호출하여 출력 스키마는 동일하게 강제한다.

### LLM 실패 처리

1회 재시도 후에도 LLM 호출이 실패하면:

1. `extract_metadata` 가 `None` 을 반환한다.
2. 호출자(`embed_metadata.py`)는 해당 service_id를 `extraction_failed.tsv` 에 한 줄 추가하고 **summary/question row 생성을 건너뛴다**.
3. identity row 1행만 INSERT된다 (`metadata.extracted` 없음).
4. 다음 스케줄러 실행 시 `--retry-failed` 옵션 또는 `extraction_failed.tsv` 기반 재처리로 채워진다 (Task 6 옵션 참조).

이로써 일시적인 LLM 장애가 변별력 없는 데이터를 영구히 색인에 남기지 않는다.

**Files:**
- Create: `llm/extractor.py`
- Create: `llm/prompts/__init__.py`
- Create: `llm/prompts/extraction.py`
- Create: `tests/test_extractor.py`

- [ ] **Step 1: 테스트 작성** — `tests/test_extractor.py`

```python
from unittest.mock import AsyncMock, patch

import pytest


class TestExtractMetadataFullPath:
    """cleaned_detail이 충분할 때 — full extraction 프롬프트 호출."""

    async def test_normal_path_returns_extracted(self):
        from llm.extractor import ExtractedMetadata, extract_metadata
        llm = AsyncMock()
        llm.structured = AsyncMock(return_value=ExtractedMetadata(
            fee="평일 5천원, 주말 1만원",
            operating_hours="09:00-21:00",
            facilities=["샤워실", "라커"],
            summary="강남구 테니스장. 평일 5천원.",
        ))
        result = await extract_metadata(
            service_name="강남구 테니스장",
            area_name="강남구",
            max_class_name="체육시설",
            min_class_name="테니스장",
            cleaned_detail="평일 5천원, 주말 1만원. 운영시간 09:00-21:00. 부대시설: 샤워실, 라커.",
            llm_client=llm,
        )
        assert result.fee == "평일 5천원, 주말 1만원"
        assert "샤워실" in result.facilities


class TestExtractMetadataFallbackPath:
    """cleaned_detail이 비어있을 때 — metadata-only 프롬프트 호출."""

    async def test_empty_detail_uses_metadata_only_prompt(self):
        """LLM 호출은 유지되며, 메타데이터 컬럼이 프롬프트에 들어간다."""
        from llm.extractor import ExtractedMetadata, extract_metadata
        captured: dict = {}

        async def _spy(messages, *args, **kwargs):
            captured["messages"] = messages
            return ExtractedMetadata(
                fee="무료",
                restrictions=["어린이 단체"],
                summary="마포구 교육강좌 자연/과학 유아숲체험원 (어린이 단체)",
            )

        llm = AsyncMock()
        llm.structured = _spy

        result = await extract_metadata(
            service_name="유아숲체험원",
            area_name="마포구",
            max_class_name="교육강좌",
            min_class_name="자연/과학",
            target_info="어린이 단체",
            payment_type="무료",
            cleaned_detail="",
            llm_client=llm,
        )
        prompt_text = "\n".join(getattr(m, "content", str(m)) for m in captured["messages"])
        assert "유아숲체험원" in prompt_text
        assert "마포구" in prompt_text
        assert "어린이 단체" in prompt_text
        assert "무료" in prompt_text
        # operating_hours 등은 derive 불가 → None 유지
        assert result.operating_hours is None
        assert result.cancellation is None
        assert result.facilities == []
        assert result.capacity is None
        # 메타데이터 기반 필드는 채워짐
        assert result.fee == "무료"
        assert result.restrictions == ["어린이 단체"]
        assert "유아숲체험원" in result.summary

    async def test_short_detail_below_threshold_uses_fallback_prompt(self):
        """MIN_CHARS 미만이면 metadata-only 경로로 분기한다."""
        from llm.extractor import extract_metadata
        llm = AsyncMock()
        llm.structured = AsyncMock(return_value=None)  # 호출되었는지만 확인
        await extract_metadata(
            service_name="x",
            area_name="강남구",
            max_class_name="체육시설",
            min_class_name="테니스장",
            cleaned_detail="짧음",   # 임계치 미만
            llm_client=llm,
        )
        # 어떤 프롬프트로 호출되었는지는 별도 fixture spy로 검증 — 여기선 호출만 확인
        llm.structured.assert_called_once()


class TestExtractMetadataLlmFailure:
    """LLM 자체가 실패할 때 — None 반환, 호출자가 skip."""

    async def test_llm_failure_returns_none(self):
        """변별력 없는 템플릿으로 채우지 않고 None을 반환한다.

        Track B/C 적재 자체를 건너뛰기 위함. Track A 임베딩은 호출자가 별도 진행한다.
        """
        from llm.extractor import extract_metadata
        llm = AsyncMock()
        llm.structured = AsyncMock(side_effect=RuntimeError("api down"))
        result = await extract_metadata(
            service_name="테니스장",
            area_name="강남구",
            max_class_name="체육시설",
            min_class_name="테니스",
            place_name="마루공원",
            cleaned_detail="평일 5천원 ...",
            llm_client=llm,
        )
        assert result is None

    async def test_retry_once_before_returning_none(self):
        """LLM 실패는 1회 재시도 후에 None 반환."""
        from llm.extractor import extract_metadata
        llm = AsyncMock()
        llm.structured = AsyncMock(side_effect=RuntimeError("transient"))
        await extract_metadata(
            service_name="x", cleaned_detail="...", llm_client=llm,
        )
        assert llm.structured.call_count == 2  # 1회 본 호출 + 1회 재시도
```

- [ ] **Step 2: 테스트 실패 확인 → 구현 → 통과**

```bash
uv run pytest tests/test_extractor.py -v
# 구현 후
uv run pytest tests/test_extractor.py -v
```

- [ ] **Step 3: 두 프롬프트 작성** — `llm/prompts/extraction.py`

`EXTRACTION_PROMPT_FULL` / `EXTRACTION_PROMPT_METADATA_ONLY` 두 상수로 분리. few-shot 예시는 각 경로에 맞게 별도 구성하되 **봉인 평가셋(80개)과 겹치지 않도록 확인**.

---

## Task 4: HyQE (예상질문 생성)

### 동작

```python
# llm/hyqe.py
class HyQEQuestion(BaseModel):
    question_text: str
    intent_label: Literal["semantic", "detail", "keyword"]


async def generate_questions(
    *,
    service_name: str,
    area_name: str,
    max_class_name: str,
    min_class_name: str,
    cleaned_detail: str,
    extracted_summary: str,
    n: int = 10,                  # llm.embedding_config.HYQE_QUESTIONS_PER_SERVICE
    llm_client: LlmClient,
) -> list[HyQEQuestion]:
    """시설 한 건에 대해 예상 질문 N개를 생성한다.

    분포 강제:
      semantic 50% / detail 30% / keyword 20%
    """
```

### 분포 강제 검증

LLM 출력의 intent_label 분포가 ±10% 이상 어긋나면 1회 재시도. 그래도 어긋나면 부족한 카테고리의 폴백 질문(템플릿 기반)을 채워 N개를 맞춘다.

```python
def _enforce_distribution(questions: list[HyQEQuestion], n: int) -> list[HyQEQuestion]:
    """semantic 50% / detail 30% / keyword 20% 분포를 만족하도록 자른다 + 부족분은 폴백."""
```

### 프롬프트

평가셋 100개 중 20개를 few-shot으로 인용한다 (의도유형 컬럼 포함). 80개 봉인본은 절대 노출하지 않는다.

**Files:**
- Create: `llm/hyqe.py`
- Create: `llm/prompts/hyqe.py`
- Create: `tests/test_hyqe.py`

- [ ] **Step 1: 테스트 작성 + 구현 + 통과**

```python
class TestGenerateQuestions:
    async def test_returns_n_questions(self): ...
    async def test_enforces_intent_distribution(self): ...
    async def test_fallback_fills_missing_categories(self): ...
    async def test_llm_failure_returns_empty_list(self): ...  # 적재 진행은 막지 않음
```

- [ ] **Step 2: 분포 검증 유틸 단위 테스트**

```python
class TestEnforceDistribution:
    def test_trims_excess_semantic(self): ...
    def test_pads_missing_keyword_with_template(self): ...
```

---

## Task 5: 평가셋 분리 (80 봉인 / 20 few-shot)

### 분리 절차

1. 평가셋 원본 `tests/data/eval_set_100.tsv` (또는 기존 `schemas/retrieval_queries.tsv`) 를 확정한다.
2. `scripts/eval/eval_set.py freeze --seed 42` 실행:
   - 의도 유형별 stratified sampling으로 80/20 분할.
   - `scripts/eval/eval_set_holdout.tsv` (80) / `scripts/eval/eval_set_fewshot.tsv` (20) 생성.
   - holdout 파일은 `.gitignore`로 커밋에서 제외할지 검토 (또는 access-controlled 디렉토리로 이동).
3. HyQE 프롬프트(`llm/prompts/hyqe.py`)는 **`eval_set_fewshot.tsv` 만 import**. holdout은 어떤 코드에서도 import하지 않는다.
4. 평가 스크립트는 holdout만 사용한다.

### 봉인 검증

```python
# tests/test_eval_set_isolation.py
def test_holdout_not_referenced_in_prompts():
    """grep으로 llm/prompts/ 어떤 파일도 holdout 파일을 참조하지 않는지 검증."""
```

**Files:**
- Create: `scripts/eval/__init__.py`
- Create: `scripts/eval/eval_set.py`
- Create: `tests/test_eval_set_isolation.py`
- Modify: `.gitignore` (필요 시)

- [ ] **Step 1: 분리 스크립트 구현 + 분리 실행 + 결과 파일 검증**

---

## Task 6: 트랙별 모듈 분리 + `scripts/embed_metadata.py` 오케스트레이터화

### 설계 원칙

`embed_metadata.py` 가 트랙 3종의 텍스트 합성·임베딩·INSERT를 모두 가지면 한 파일이 500줄을 넘기고 단위 테스트가 어려워진다. **트랙별로 모듈을 분리**하고 `embed_metadata.py` 는 정제·추출·트랙 호출 순서만 책임진다.

```
scripts/
├── embed_metadata.py          # 얇은 오케스트레이터
└── tracks/
    ├── __init__.py
    ├── _shared.py              # DELETE_BY_SERVICE_ID, INSERT_ROW SQL, 공통 헬퍼
    ├── identity.py             # Track A
    ├── summary.py              # Track B
    └── questions.py            # Track C
```

### 트랙 모듈 계약

각 트랙 모듈은 다음 시그니처를 갖는다. 모두 비동기, **세션을 받지만 트랜잭션 경계는 호출자(orchestrator)가 관리**한다.

```python
# scripts/tracks/identity.py
async def embed_and_insert_identity(
    session: AsyncSession,
    service: ServiceRecord,
    *,
    embedder: Embedder,
    extracted: ExtractedMetadata | None,    # None이면 metadata.extracted 없이 INSERT
) -> None:
    """Track A — 항상 실행된다. extraction 실패와 무관."""

# scripts/tracks/summary.py
async def embed_and_insert_summary(
    session: AsyncSession,
    service: ServiceRecord,
    *,
    embedder: Embedder,
    extracted: ExtractedMetadata,           # 호출 전제: not None (orchestrator가 가드)
) -> None:
    """Track B — summary row 1행 INSERT."""

# scripts/tracks/questions.py
async def embed_and_insert_questions(
    session: AsyncSession,
    service: ServiceRecord,
    *,
    embedder: Embedder,
    llm_client: LlmClient,
    cleaned_detail: str,
    extracted_summary: str,
) -> bool:
    """Track C — HyQE 생성 + N개 question row INSERT.

    Returns:
        True  : 생성·적재 성공
        False : HyQE 생성 실패 (orchestrator가 로깅 후 다음 service로)
    """
```

각 모듈은 자기 트랙의 **텍스트 합성 로직**(`_compose_identity_text` 등)과 **INSERT 컬럼 매핑**을 내부에 캡슐화한다.

### 오케스트레이터 흐름 (`embed_metadata.py`)

```python
from scripts.tracks.identity  import embed_and_insert_identity
from scripts.tracks.summary   import embed_and_insert_summary
from scripts.tracks.questions import embed_and_insert_questions
from scripts.tracks._shared   import delete_rows_by_service_id

async def process_service(service: ServiceRecord, *, session, embedder, llm_client, tracks: set[str]):
    cleaned = clean_detail_content(service.detail_content)

    async with session.begin():
        # 0) 기존 row 제거 (재적재 시 stale 방지)
        await delete_rows_by_service_id(session, service.service_id, tracks=tracks)

        # 1) Track B 전처리 — orchestrator가 책임 (identity의 metadata에 extracted를 채워야 하므로)
        extracted = None
        if "A" in tracks or "B" in tracks or "C" in tracks:
            extracted = await extract_metadata(
                service_name=service.service_name,
                area_name=service.area_name, ...,
                cleaned_detail=cleaned,
                llm_client=llm_client,
            )

        # 2) Track A — 항상 (요청된 경우)
        if "A" in tracks:
            await embed_and_insert_identity(session, service, embedder=embedder, extracted=extracted)

        # 3) extracted 가드 — Track B/C는 extracted 필수
        if extracted is None:
            log_failed(service.service_id, reason="extraction_failed")
            return

        # 4) Track B
        if "B" in tracks:
            await embed_and_insert_summary(session, service, embedder=embedder, extracted=extracted)

        # 5) Track C
        if "C" in tracks:
            ok = await embed_and_insert_questions(
                session, service,
                embedder=embedder, llm_client=llm_client,
                cleaned_detail=cleaned, extracted_summary=extracted.summary,
            )
            if not ok:
                log_failed(service.service_id, reason="hyqe_failed")
```

> **트랜잭션 경계**: orchestrator가 `session.begin()` 으로 시설 1건 = 1 트랜잭션 보장. 트랙 모듈은 commit/rollback하지 않는다.
> **트랙 부분 선택**: `--track A` 면 `tracks={"A"}` 만 전달되어 다른 트랙은 호출되지 않는다. `_shared.delete_rows_by_service_id(..., tracks={"A"})` 는 `row_kind='identity'` row만 삭제한다.

### 공통 헬퍼 (`scripts/tracks/_shared.py`)

```python
_TRACK_TO_ROW_KIND = {"A": "identity", "B": "summary", "C": "question"}

INSERT_ROW = text("""
    INSERT INTO service_embeddings (
        service_id, row_kind, idx,
        service_name, embedding_text, embedding,
        metadata, intent_label
    ) VALUES (
        :service_id, :row_kind, :idx,
        :service_name, :embedding_text, :embedding,
        CAST(:metadata AS jsonb), :intent_label
    )
""")

async def delete_rows_by_service_id(session, service_id: str, *, tracks: set[str]) -> None:
    row_kinds = [_TRACK_TO_ROW_KIND[t] for t in tracks]
    await session.execute(
        text("DELETE FROM service_embeddings WHERE service_id = :sid AND row_kind = ANY(:kinds)"),
        {"sid": service_id, "kinds": row_kinds},
    )
```

### 운영 메모

> **메모 1**: extraction이 `None` 을 반환하면 orchestrator가 Track B/C 호출을 건너뛴다. Track A가 요청에 포함된 경우 identity row 1행만 적재된다. `WHERE service_id=X AND row_kind='summary'` 가 빈 결과면 미적재 시설.

> **메모 2**: HyQE 실패 시 questions 모듈이 `False` 를 반환하고 orchestrator가 로깅한다. identity/summary row는 이미 같은 트랜잭션에서 적재되었으므로 함께 커밋된다.

> **메모 3**: 단일 vector 경쟁 검색에서 미적재 row_kind는 자동으로 결과에서 빠진다 (row 자체가 없음). 별도 NULL 필터 불필요.

### 새 옵션

| 옵션 | 동작 |
|---|---|
| `--all` | 전체 재적재 (기존 데이터 DELETE 후 INSERT) |
| `--incremental` | 신규 service_id만 적재 (기존 동작) |
| `--track {A,B,C,all}` | 특정 트랙만 재생성. Track A/B는 service_embeddings UPSERT, Track C는 question 테이블 갱신 |
| `--retry-failed` | `extraction_failed.tsv` 또는 summary row가 없는 service_id 만 재처리. 일시적 LLM 장애 복구용 |
| `--limit N` | 디버깅용 N건만 처리 |
| `--dry-run` | LLM/Embedding 호출까지 하되 DB 쓰기는 생략 |

### 배치 / 비용 제어

- Embedding 호출은 batch 단위 (Gemini API의 batch endpoint 활용, 기본 32건).
- LLM 추출 + HyQE는 시설당 2회 LLM 호출이 들어가므로 비용 큼. `--limit` 와 `--dry-run` 으로 점진 적용.
- 재시도: 각 LLM 호출은 1회 재시도 후 fallback. 실패 row는 `extraction_failed.tsv` 로 로깅.

**Files:**
- Create: `scripts/tracks/__init__.py`
- Create: `scripts/tracks/_shared.py`
- Create: `scripts/tracks/identity.py`
- Create: `scripts/tracks/summary.py`
- Create: `scripts/tracks/questions.py`
- Modify: `scripts/embed_metadata.py` (orchestrator로 축소)
- Create: `tests/test_track_identity.py`
- Create: `tests/test_track_summary.py`
- Create: `tests/test_track_questions.py`
- Create: `tests/test_embed_metadata_pipeline.py`

### Step 1: 트랙 모듈 단위 테스트 + 구현

각 트랙은 호출 계약이 단순하므로 단위 테스트가 자연스럽다. session·embedder·llm_client는 AsyncMock으로 대체.

- [ ] **1-1. `tests/test_track_identity.py`**

```python
class TestEmbedAndInsertIdentity:
    async def test_text_composition_includes_all_fields(self, mock_session, mock_embedder):
        """{area} {max} {min} {service} {place} 순서로 합성."""
        ...

    async def test_extracted_none_omits_extracted_key(self, mock_session, mock_embedder):
        """extracted=None이면 metadata JSONB에 extracted 키 없음."""
        ...

    async def test_extracted_present_includes_extracted_key(self, mock_session, mock_embedder):
        ...

    async def test_inserts_with_correct_row_kind_and_idx(self, mock_session, mock_embedder):
        """row_kind='identity', idx=0 으로 INSERT."""
        ...
```

- [ ] **1-2. `tests/test_track_summary.py`**

```python
class TestEmbedAndInsertSummary:
    async def test_embeds_summary_field(self, mock_session, mock_embedder):
        """extracted.summary 를 그대로 embedder.embed에 전달."""
        ...

    async def test_inserts_with_summary_row_kind(self, mock_session, mock_embedder):
        """row_kind='summary', idx=0, intent_label=None, metadata=NULL."""
        ...
```

- [ ] **1-3. `tests/test_track_questions.py`**

```python
class TestEmbedAndInsertQuestions:
    async def test_inserts_n_question_rows(self, mock_session, mock_embedder, mock_llm):
        """HyQE가 N개 반환 시 N개 row INSERT (idx=0..N-1)."""
        ...

    async def test_intent_label_propagated(self, mock_session, mock_embedder, mock_llm):
        """각 question row의 intent_label이 HyQE 출력과 일치."""
        ...

    async def test_hyqe_failure_returns_false(self, mock_session, mock_embedder, mock_llm):
        """generate_questions가 None 반환 시 False 반환, INSERT 0회."""
        ...

    async def test_row_kind_is_question(self, mock_session, mock_embedder, mock_llm):
        ...
```

### Step 2: 오케스트레이터 통합 테스트 + 구현 (LLM/Embedding은 mock, DB는 테스트 DSN 또는 testcontainer)

```python
class TestPipelineEndToEnd:
    async def test_single_service_creates_all_rows(self, test_db):
        """1건 처리 시 identity 1행 + summary 1행 + question N행이 같은 service_id로 들어간다."""
        # SELECT row_kind, count(*) FROM service_embeddings WHERE service_id=X GROUP BY row_kind
        # Expected: identity=1, summary=1, question=N
        ...

    async def test_track_a_only_keeps_other_rows(self, test_db):
        """--track A 재실행 시 identity row만 갱신, summary/question row는 유지.

        - delete_rows_by_service_id가 tracks={"A"} 만 받아 identity row만 DELETE
        - embed_and_insert_summary / embed_and_insert_questions 미호출 검증
        """
        ...

    async def test_orchestrator_delegates_to_track_modules(self, test_db, mocker):
        """orchestrator는 호출 순서·조건만 책임지고 실제 텍스트 합성/INSERT는 트랙 모듈에 위임.

        - mocker로 embed_and_insert_identity / summary / questions를 patch
        - 1건 처리 시 호출 순서: identity → summary → questions
        - extracted is None 인 경우: identity만 호출됨
        """
        ...

    async def test_empty_detail_content_still_creates_summary(self, test_db):
        """detail_content가 비어 있어도 metadata-only LLM 호출로 summary/question row가 생긴다."""
        ...

    async def test_extraction_failure_only_creates_identity(self, test_db):
        """LLM 실패 시 identity row 1행만 적재, summary/question row 없음, extraction_failed.tsv 1줄.

        - SELECT count(*) WHERE service_id=X AND row_kind='identity' → 1
        - SELECT count(*) WHERE service_id=X AND row_kind='summary'  → 0
        - SELECT count(*) WHERE service_id=X AND row_kind='question' → 0
        """
        ...

    async def test_retry_failed_reprocesses_only_missing_summary(self, test_db):
        """--retry-failed 는 summary row가 없는 service_id 만 재시도한다."""
        ...

    async def test_hyqe_failure_keeps_summary(self, test_db):
        """HyQE만 실패하면 identity + summary row는 적재되고 question row 0건."""
        ...

    async def test_check_constraint_rejects_invalid_row_kind(self, test_db):
        """CHECK 제약이 invalid row_kind insert를 거부한다."""
        ...

    async def test_check_constraint_rejects_question_without_intent_label(self, test_db):
        """question row에 intent_label NULL이면 CHECK 위반."""
        ...

    async def test_transaction_atomicity(self, test_db):
        """시설 1건 적재 중 임베딩 호출 실패 시 부분 row가 남지 않는다 (트랜잭션 롤백)."""
        ...

    async def test_dry_run_no_db_writes(self, test_db):
        ...
```

- [ ] **Step 2: 구현**

`scripts/embed_metadata.py` 의 기존 적재 함수에 위 흐름을 단계별로 추가한다. 트랜잭션 경계는 **시설 1건 = 1 트랜잭션** (한 시설의 Track A/B/C는 함께 커밋되어야 일관성 유지).

- [ ] **Step 3: 통과 + 린트**

```bash
uv run pytest tests/test_embed_metadata_pipeline.py -v
uv run ruff check scripts/
```

- [ ] **Step 4: 실데이터 dry-run**

```bash
uv run python scripts/embed_metadata.py --all --limit 5 --dry-run
```

로그로 정제 hit률, 추출 결과, 질문 생성 결과 확인.

---

## Task 6-2: `vector_search` — 단일 경쟁 쿼리 + dedup

> **연계 — chat-search-persistence**: Phase 1 `vector_node` 는 `state.search_channels` 에 `vector` (refined_query / 단일 경쟁 결과) + `bm25` (토큰 / 결과) + `final` (hydrated) 3개 채널을 `ChannelData(kind, query, hits)` 형태로 채운다. 적재는 종단 `search_persist_node` 가 일괄 처리한다. Phase 2 RRF 도입 시 `vector` 단일 채널이 `vector_a/b/c` + `rrf` 로 자연 대체되며 (kind 화이트리스트는 이미 모두 포함) DB 스키마 변경 없이 채널 키만 늘어난다. 운영 가이드는 `docs/chat-search-persistence.md` 참조.

### 동작

새 통합 테이블 구조에서 `vector_search` 는 row_kind 구분 없이 단일 쿼리로 검색하고 `service_id` 기준으로 dedup한다. Phase 1에는 가중치도 없다.

```sql
-- $1 = 쿼리 임베딩, $2 = min_similarity
SELECT DISTINCT ON (service_id)
    service_id, row_kind, embedding_text, similarity, intent_label
FROM (
    SELECT
        service_id, row_kind, embedding_text, intent_label,
        1 - (embedding <=> CAST(:q AS vector)) AS similarity
    FROM service_embeddings
    WHERE 1 - (embedding <=> CAST(:q AS vector)) >= :min_similarity
    ORDER BY embedding <=> CAST(:q AS vector)
    LIMIT :scan_k                   -- 단일 HNSW 인덱스로 전체 row 대상 검색
) candidates
ORDER BY service_id, similarity DESC          -- service_id 별 최고 점수 선택
LIMIT :top_k;
```

> **scan_k 산정**: 시설당 평균 ~12 row (identity 1 + summary 1 + question 10). top_k=10 기준 service_id dedup 후 부족분을 고려하여 `scan_k = top_k * 12 ≈ 100` 권장. 실측 후 조정.

### post-filter (metadata 조건)

identity row 외의 row는 `metadata=NULL` 이므로 post-filter는 identity row의 metadata에만 적용해야 의미가 있다. Phase 1에서는 post-filter를 적용하지 않고 단순 경쟁만 한다. 트랙별 partial 쿼리 + post-filter는 RRF 계획에서 도입.

**Files:**
- Modify: `tools/vector_search.py`
- Modify: `agents/vector_agent.py`
- Modify: `tests/test_vector_search.py`
- Modify: `tests/test_vector_agent.py`

- [ ] **Step 1: vector_search 단순화 + 테스트 갱신**

기존 시그니처에서 `max_class_name`/`area_name`/`service_status` post-filter 인자는 deprecated로 두되 무시 처리 (RRF 계획에서 복구). row_kind도 받지 않음.

```python
async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    top_k: int = TOP_K,
    min_similarity: float = MIN_SIMILARITY,
) -> list[dict]:
    """단일 경쟁 쿼리. service_id 기준 dedup된 결과 반환.

    Returns: [{service_id, row_kind, embedding_text, similarity, intent_label}]
    """
```

- [ ] **Step 2: VectorAgent — BM25와 단순 union 결합**

```python
# 임시 결합 (RRF 도입 전):
#  1. vector_search: 단일 경쟁 결과 N건
#  2. bm25_search: 동일하게 N건
#  3. service_id 기준 OR (union) 후 중복 제거, vector similarity 우선 정렬
#  4. hydrate_services
```

가중치도 없고 RRF 점수도 없다. 단순히 두 채널의 service_id를 합쳐 hydration. RRF 도입 시 본 단계가 교체된다.

- [ ] **Step 3: 회귀 테스트**

```bash
uv run pytest tests/test_vector_search.py tests/test_vector_agent.py -v
```

기존 post-filter 테스트는 RRF 계획에서 다시 활성화하므로 본 단계에서는 `@pytest.mark.skip(reason="phase-rrf")` 처리.

---

## Task 6-3: 동기화 REST API — `POST /embeddings/services/sync`

`on-seoul-api`(Spring Boot)가 일 1회 서울 Open API 수집을 완료한 직후, 변경된 service_id 목록을 본 서비스에 통보하여 임베딩을 갱신·삭제하도록 요청한다. 배치 스크립트(`scripts/embed_metadata.py`)와 동일한 트랙 모듈을 재사용하므로 비즈니스 로직 중복은 없다.

### API 설계

#### Endpoint

```
POST /embeddings/services/sync
```

**URL 설계 원칙**: `/embeddings/{resource}/sync` 패턴. 임베딩이 본 서비스의 owned resource이고, 하위 리소스 타입(`services`)으로 어떤 데이터의 임베딩인지 표현한다. 향후 FAQ 임베딩 등이 추가되면 `/embeddings/faqs/sync` 처럼 자연 확장된다.

#### Headers

| 헤더 | 필수 | 설명 |
|---|---|---|
| `Content-Type` | ✓ | `application/json` |

> **인증**: 현재 서비스 간 통신 스펙에 별도 인증 헤더가 없으므로 본 엔드포인트도 인증 없이 제공한다. 외부 노출은 인프라(Nginx allowlist, Docker 네트워크 격리)로 차단한다. 인증 도입은 전사 통신 스펙이 정해진 후 별도 계획에서 다룬다.

#### Request Body

```json
{
  "upsert": ["S240101A001", "S240101A002"],
  "delete": ["S230501Z099"]
}
```

| 필드 | 타입 | 설명 |
|---|---|---|
| `upsert` | `string[]` | 신규/변경 service_id. 트랙 A/B/C 전체 재적재. 빈 배열 허용 |
| `delete` | `string[]` | 삭제된 service_id. `service_embeddings` 에서 모든 row 제거. 빈 배열 허용 |

**제약**:
- `len(upsert) + len(delete) ≤ 500` per request (`settings.embedding_sync_max_items`). 초과 시 클라이언트가 청크로 분할.
- `service_id` 정규식 검증 (`^[A-Za-z0-9_-]+$`) — SQL injection 방지의 추가 방어선.
- `upsert ∩ delete == ∅` (같은 service_id가 양쪽에 있으면 422).
- 둘 다 빈 배열이면 422.

#### Response 202 Accepted

```json
{
  "accepted": { "upsert": 2, "delete": 1 }
}
```

처리는 백그라운드로 진행되며, 응답은 즉시 반환한다. 상세 진행 상태 조회 엔드포인트는 Phase 2에서 추가한다.

#### Response 4xx

| 상태 | 조건 |
|---|---|
| 422 | 두 배열 모두 비어있음 / 합계 500 초과 / 정규식 불일치 / 양쪽 중복 |
| 400 | malformed JSON |

### 처리 전략

#### 백그라운드 실행

FastAPI `BackgroundTasks` 로 in-process 백그라운드 실행. 별도 워커 인프라(Celery, RQ)는 도입하지 않는다. 동시성은 `asyncio.Semaphore` 로 제어하여 Gemini API rate limit을 보호한다.

```python
# routers/embeddings.py 핵심 발췌
# router = APIRouter(prefix="/embeddings", tags=["embeddings"])
@router.post("/services/sync", status_code=202)
async def services_sync(
    req: ServiceEmbeddingsSyncRequest,
    background: BackgroundTasks,
) -> ServiceEmbeddingsSyncResponse:
    background.add_task(_run_services_sync, req.upsert, req.delete)
    return ServiceEmbeddingsSyncResponse(accepted={"upsert": len(req.upsert), "delete": len(req.delete)})


async def _run_services_sync(upsert: list[str], delete: list[str]) -> None:
    sem = asyncio.Semaphore(settings.embedding_sync_concurrency)   # 기본 4

    async def _upsert_one(sid: str) -> None:
        async with sem:
            async with data_session_ctx() as data_session, ai_session_ctx() as ai_session:
                service = await fetch_service_record(data_session, sid)
                if service is None:
                    logger.warning("sync.upsert.miss service_id=%s", sid)
                    return
                # 트랙 모듈을 재사용 — 배치 스크립트와 동일 경로
                await process_service(
                    service, session=ai_session,
                    embedder=embedder, llm_client=llm_client,
                    tracks={"A", "B", "C"},
                )

    async def _delete_one(sid: str) -> None:
        async with sem:
            async with ai_session_ctx() as ai_session:
                await ai_session.execute(
                    text("DELETE FROM service_embeddings WHERE service_id = :sid"),
                    {"sid": sid},
                )
                await ai_session.commit()

    tasks = [_upsert_one(s) for s in upsert] + [_delete_one(s) for s in delete]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failures = [r for r in results if isinstance(r, Exception)]
    if failures:
        logger.warning("sync.completed total=%d failures=%d", len(results), len(failures))
    else:
        logger.info("sync.completed total=%d", len(results))
```

#### 책임 분리

- **REST 핸들러** (`routers/embeddings.py`) — 검증 + 백그라운드 작업 enqueue. 비즈니스 로직 없음. `/embeddings` prefix로 묶고 리소스 타입별 서브 핸들러(`services/sync`)로 분기.
- **동기화 워커** (`routers/embeddings.py::_run_services_sync`) — Semaphore + 세션 컨텍스트 관리. 트랙 모듈 호출.
- **트랙 모듈** (`scripts/tracks/{identity,summary,questions}.py`) — Task 6에서 정의한 동일 모듈을 재사용. **배치와 API 양쪽에서 같은 경로**.

이로써 "수집 배치 vs API 트리거" 의 처리 결과 일관성이 자동 보장된다 (분기 코드 없음).

#### 멱등성

- **upsert**: orchestrator가 `DELETE service_embeddings WHERE service_id=X` 후 INSERT 하므로 동일 요청 반복은 안전. 중간 실패 시 트랜잭션 롤백.
- **delete**: `DELETE WHERE service_id=X` 자체가 멱등.
- **중복 요청**: Phase 1은 별도 lock 없음 — 동일 service_id가 동시 두 요청에 포함되면 마지막 commit이 이김. Phase 2에서 Redis SETNX 기반 per-service lock 도입 검토.

#### 동시성 제어

| 항목 | 기본값 | 근거 |
|---|---|---|
| `embedding_sync_concurrency` | 4 | Gemini API 동시성 한도(분당 RPM 기준)와 시설당 ~12회 임베딩 호출을 고려한 보수적 기본값 |
| `embedding_sync_max_items` | 500 | 한 요청이 너무 길어지지 않도록 |
| `embedding_sync_request_timeout` | 30s | 응답은 즉시(202)이므로 핸들러 자체 타임아웃은 단순 검증 + enqueue 시간만 필요 |

### Files

| 파일 | 역할 | 변경 |
|---|---|---|
| `routers/embeddings.py` | `/embeddings` prefix 라우터. `POST /embeddings/services/sync` 핸들러 + `_run_services_sync` 백그라운드 워커. 향후 다른 리소스용 서브 핸들러 추가 시 같은 파일에 그룹화 | 신규 |
| `schemas/embeddings.py` | `ServiceEmbeddingsSyncRequest` / `ServiceEmbeddingsSyncResponse` Pydantic 모델 (validator 포함) | 신규 |
| `scripts/embed_metadata.py` | `process_service` 함수를 모듈 import 가능하도록 노출 (orchestrator 로직을 함수로 분리) | 수정 |
| `scripts/tracks/_shared.py` | `fetch_service_record(data_session, service_id) -> ServiceRecord \| None` 헬퍼 추가 | 수정 |
| `core/config.py` | `embedding_sync_concurrency`, `embedding_sync_max_items` 추가 | 수정 |
| `main.py` | `routers/embeddings.py` 라우터 등록 | 수정 |
| `tests/test_embeddings_sync_router.py` | 라우터 유닛 테스트 (검증·인증·enqueue 동작) | 신규 |
| `tests/test_embeddings_sync_worker.py` | `_run_services_sync` 워커 테스트 (Semaphore, 트랙 모듈 호출 위임, 실패 격리) | 신규 |

### Steps

- [ ] **Step 1: Pydantic 스키마 작성** — `schemas/embeddings.py`

```python
import re
from pydantic import BaseModel, Field, model_validator

_SERVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]+$")


class ServiceEmbeddingsSyncRequest(BaseModel):
    upsert: list[str] = Field(default_factory=list)
    delete: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self) -> "ServiceEmbeddingsSyncRequest":
        if not self.upsert and not self.delete:
            raise ValueError("upsert and delete are both empty")
        total = len(self.upsert) + len(self.delete)
        if total > settings.embedding_sync_max_items:
            raise ValueError(f"too many items: {total} > {settings.embedding_sync_max_items}")
        for sid in [*self.upsert, *self.delete]:
            if not _SERVICE_ID_PATTERN.match(sid):
                raise ValueError(f"invalid service_id: {sid!r}")
        overlap = set(self.upsert) & set(self.delete)
        if overlap:
            raise ValueError(f"upsert/delete overlap: {sorted(overlap)}")
        return self


class ServiceEmbeddingsSyncResponse(BaseModel):
    accepted: dict[str, int]
```

- [ ] **Step 2: 라우터 유닛 테스트 작성** — `tests/test_embeddings_sync_router.py`

```python
class TestServiceEmbeddingsSyncRouter:
    async def test_empty_body_returns_422(self, async_client): ...
    async def test_invalid_service_id_returns_422(self, async_client): ...
    async def test_overlap_returns_422(self, async_client): ...
    async def test_too_many_items_returns_422(self, async_client): ...
    async def test_malformed_json_returns_400(self, async_client): ...
    async def test_accepted_returns_202_with_counts(self, async_client):
        """정상 요청은 202 + accepted.upsert/delete 카운트 반환. background task enqueue 확인."""
        ...
    async def test_handler_returns_before_processing(self, async_client):
        """핸들러는 process_service 완료를 기다리지 않는다 (BackgroundTasks 비동기 보장)."""
        ...
```

- [ ] **Step 3: 워커 유닛 테스트 작성** — `tests/test_embeddings_sync_worker.py`

```python
class TestRunServicesSync:
    async def test_upsert_delegates_to_process_service(self, mocker):
        """_upsert_one은 fetch_service_record → process_service 호출 순서를 따른다."""
        ...
    async def test_upsert_missing_service_logs_warning(self, mocker, caplog):
        """fetch_service_record가 None이면 sync.upsert.miss 로그 + process_service 미호출."""
        ...
    async def test_delete_executes_delete_sql(self, mocker): ...
    async def test_semaphore_limits_concurrency(self, mocker):
        """Semaphore(4)로 동시 실행 제한. 5개 요청 시 최대 4개만 동시 진행."""
        ...
    async def test_individual_failure_isolated(self, mocker):
        """한 service_id 처리가 예외를 던져도 다른 service_id 처리에 영향 없음. logger.warning만."""
        ...
    async def test_calls_track_modules_via_process_service(self, mocker):
        """배치 스크립트와 동일하게 트랙 모듈을 거친다 (분기 코드 없음 검증)."""
        ...
```

- [ ] **Step 4: 핸들러 + 워커 구현** — `routers/embeddings.py`

위 핵심 발췌의 동작을 그대로 구현. 인증 미들웨어/디펜던시는 두지 않는다.

- [ ] **Step 5: `process_service` 함수 노출** — `scripts/embed_metadata.py`

배치 스크립트 내부 로컬 함수였던 `process_service` 를 모듈 수준 `async def` 로 분리하여 라우터에서 import 가능하게 한다. 배치 측은 `for service in services: await process_service(...)` 로 그대로 사용.

- [ ] **Step 6: `fetch_service_record` 헬퍼 추가** — `scripts/tracks/_shared.py`

```python
async def fetch_service_record(data_session, service_id: str) -> ServiceRecord | None:
    """on_data.public_service_reservations 에서 service_id로 1건 조회. soft-delete 제외."""
    row = (await data_session.execute(
        text("SELECT ... FROM public_service_reservations WHERE service_id = :sid AND deleted_at IS NULL"),
        {"sid": service_id},
    )).first()
    return ServiceRecord.from_row(row) if row else None
```

- [ ] **Step 7: 라우터 등록 + 통과**

```python
# main.py
from routers import embeddings as embeddings_router
app.include_router(embeddings_router.router)
```

```bash
uv run pytest tests/test_embeddings_services_sync_router.py tests/test_embeddings_services_sync_worker.py -v
uv run ruff check routers/ schemas/embeddings.py
```

- [ ] **Step 8: 통합 smoke test (선택)**

실제 DB + 1건 upsert + 1건 delete 시나리오로 end-to-end 확인.

```bash
curl -X POST http://localhost:8000/embeddings/services/sync \
  -H "Content-Type: application/json" \
  -d '{"upsert": ["S240101A001"], "delete": []}'
# 응답: 202 + {"accepted": {"upsert": 1, "delete": 0}}

# 약 10초 후 DB 확인
psql "$ON_AI_DSN" -c "SELECT row_kind, count(*) FROM service_embeddings WHERE service_id='S240101A001' GROUP BY row_kind;"
# 기대: identity 1 / summary 1 / question N
```

### API 서비스(on-seoul-api) 측 위임 작업

| 항목 | 내용 |
|---|---|
| 수집 완료 훅 | `CollectDatasetUseCase` 종료 시 변경분(`service_change_log`)에서 service_id를 추출하여 `POST {AI_BASE_URL}/embeddings/services/sync` 호출 |
| 의미 컬럼 변경 필터 | API 서비스가 어떤 컬럼이 변경되었는지 알고 있으므로, **의미 컬럼**(service_name, max_class_name, min_class_name, area_name, place_name, target_info, detail_content) 변경분만 `upsert` 에 포함. status/dt 컬럼만 변경된 건은 호출하지 않음 (hydration이 처리) |
| 청크 분할 | 500건 초과 시 클라이언트에서 분할 호출 |
| 재시도 정책 | 5xx 응답 시 지수 백오프(예: 30s, 60s, 120s). 4xx는 즉시 실패로 처리 |

### Phase 2 후속 (별도 계획)

- **task_id + 상태 조회**: `GET /embeddings/services/sync/{task_id}` 추가. Redis에 task 메타데이터 저장.
- **Per-service Redis lock**: 동시 중복 요청 차단 (`SETNX`).
- **Dead letter queue**: 반복 실패 service_id를 별도 큐로 분리. 운영자 알림.
- **Outbox 패턴 도입 검토**: API 서비스가 변경 이력 테이블에 commit 후 별도 publisher가 호출하도록 분리하여 수집-동기화 결합도를 낮춤.

---

## Task 7: 운영 가이드 문서

**Files:**
- Create: `docs/embedding-pipeline.md`

- [ ] **Step 1: 문서 작성** — 다음을 포함한다.

1. **재적재 절차**
   - 전체 재적재: `DROP TABLE` → DDL 재적용 → `--all` (스키마 변경 동반 시)
   - 동일 스키마 전체 재적재: `--all` (service_id별 DELETE → INSERT)
   - 트랙별 재적재: `--track A` / `--track B` / `--track C` (특정 row_kind 만 갱신)
   - 증분 적재: `--incremental` (identity row가 없는 신규 service_id만)
   - 실패 복구: `--retry-failed` (summary row가 없는 service_id 만)
2. **트랙별 비용 예측** (시설 1건당 LLM 추출 1회 + HyQE 1회 + 임베딩 ~12회)
3. **마커 누락 모니터링**: 누락률 5% 이상이면 정제 패턴 검토
4. **장애 시 부분 적재 복구**:
   - LLM 실패 시 summary/question row가 만들어지지 않고 identity row만 남는다 (의도된 동작).
   - 변별력 없는 합성 데이터로 벡터 공간을 오염시키지 않기 위함.
   - 다음 스케줄러 실행 또는 수동 `--retry-failed` 로 복구한다.
5. **미적재 row_kind 의 검색 영향**: row 자체가 없으므로 단일 경쟁 쿼리에서 자동으로 빠진다. 별도 NULL 필터 불필요. 미적재 시설도 identity/BM25 채널로 검색 가능.
6. **임베딩 ↔ 원본 동기화 정책**: 의미 컬럼 변경(`service_name` 등) 시 재임베딩 필요. status/dt 컬럼 변경은 hydration이 처리하므로 불필요.
7. **검색 단계 변경 이력**: Phase 1 = 단일 경쟁 vector + BM25 union → Phase 2 = 가중 RRF + 트랙별 partial query → Phase 3 = HyDE 통합.
8. **`POST /embeddings/services/sync` 사용법**: 일 1회 수집 종료 후 `on-seoul-api` 가 변경된 service_id를 통보. URL은 `/embeddings/{resource}/sync` 패턴으로 향후 FAQ 등 다른 임베딩 리소스 추가에 대비. 본문 `{upsert: [...], delete: [...]}`. 의미 컬럼이 변경된 건만 `upsert`에 포함 (status/dt만 바뀐 건은 hydration이 처리하므로 호출 불필요). 500건 초과 시 청크 분할. 응답은 202로 즉시 반환되고 백그라운드에서 처리. 인증은 현재 서비스 간 통신 스펙에 없으므로 미적용 — 외부 노출은 인프라(Nginx allowlist / Docker 네트워크)로 차단.

---

## Task 8: 회귀 + 정합성 검증

- [ ] **Step 1: 전체 테스트 + 린트**

```bash
uv run pytest -v
uv run ruff check .
```

- [ ] **Step 2: 평가셋 봉인 검증**

```bash
uv run pytest tests/test_eval_set_isolation.py -v
grep -rn "eval_set_holdout" llm/prompts/   # 빈 결과여야 함
```

- [ ] **Step 3: 소규모 실적재 (10건)**

```bash
uv run python scripts/embed_metadata.py --all --limit 10
psql "$ON_AI_DSN" -c "SELECT row_kind, count(*) FROM service_embeddings GROUP BY row_kind;"
```

기대: `identity` 10 / `summary` 10 / `question` 80~150 (10 × 8~15 questions).

- [ ] **Step 4: BM25 색인 자동 갱신 확인**

`metadata.extracted` JSON에 들어간 키워드가 BM25로 매칭되는지 확인.

```sql
SELECT service_id, service_name
FROM service_embeddings
WHERE metadata @@@ 'paradedb.match(''metadata.extracted.facilities'', ''샤워실'')'
LIMIT 5;
```

---

## 완료 기준 체크리스트

- [ ] `service_embeddings`가 row-per-vector 구조로 재정의됨 (`row_kind`, `idx`, `embedding_text`, `intent_label` 컬럼 존재)
- [ ] 단일 HNSW 인덱스 + BM25 partial 인덱스(`WHERE row_kind='identity'`) 존재
- [ ] CHECK 제약이 invalid row_kind / question row의 NULL intent_label 모두 거부
- [ ] 마커 정제 함수가 fallback 포함 5개 케이스 모두 통과
- [ ] LLM 추출 실패 시 `extract_metadata`가 `None` 반환 (템플릿 합성 금지)
- [ ] LLM 추출 실패 시 identity row 1행만 적재되고 summary/question row 0건 + `extraction_failed.tsv` 1줄
- [ ] `--retry-failed` 옵션이 summary row가 없는 service_id 만 재처리
- [ ] HyQE intent_label 분포가 50/30/20에서 ±10% 이내
- [ ] 평가셋 80개(holdout)는 어떤 프롬프트/few-shot에서도 import되지 않음
- [ ] `scripts/embed_metadata.py --track A|B|C|all` 옵션 동작
- [ ] 시설 1건 = 1 트랜잭션 (부분 row 잔존 없음, 부분 커밋 발생 시 롤백)
- [ ] `vector_search` 가 `DISTINCT ON (service_id)` 로 dedup된 결과 반환
- [ ] VectorAgent 가 vector 결과 + BM25 결과를 단순 union으로 결합 (RRF 미사용)
- [ ] `--dry-run` 시 DB 쓰기 0회
- [ ] 마커 누락률·LLM 실패율을 배치 종료 시 로그로 출력
- [ ] `POST /embeddings/services/sync` 가 400 / 422 / 202 분기 모두 통과
- [ ] 동기화 워커가 배치와 동일한 `process_service` + 트랙 모듈을 거친다 (분기 코드 없음 검증)
- [ ] Semaphore가 `embedding_sync_concurrency` 한도를 강제 (동시 실행 수 검증)
- [ ] 동기화 워커의 개별 service_id 실패가 다른 service_id 처리에 영향을 주지 않는다 (`return_exceptions=True`)

---

## 향후 단계 (별도 계획)

진행 순서: **임베딩 개편 (본 계획) → RRF 결합 → HyDE 도입**.

- **[2026-05-18-rrf-hybrid-search.md](./2026-05-18-rrf-hybrid-search.md)** (Phase 2): 평가셋 측정 후 가중 RRF 도입. Router의 `VectorSubIntent` 분류 추가, 트랙별 partial query (row_kind 필터), 의도별 가중치 프로파일. 본 계획의 단일 경쟁 쿼리를 baseline으로 두고 회귀 측정.
- **HyDE 통합** (Phase 3): `_RefinedQuery`에 `hyde_document` 추가. semantic/complex intent에서만 적용. RRF 도입 후 어휘 격차로 인한 recall 부족이 확인되면 도입.
- **FAQ 별도 인프라**: 6건 → 수십 건으로 늘어날 때 검토.
- **환불 비율표 structured 필드**: 평가셋에서 환불 관련 recall 부족이 확인되면 도입.
- **의미 컬럼 변경 감지 자동화**: `service_change_log` 기반 재임베딩 트리거 (현재는 수동 삭제 후 `--incremental` 우회).

---

## 사전 확정 사항

1. **통합 테이블 + row_kind 디스크리미네이터.** `service_embeddings`를 row-per-vector 구조로 재정의한다. 별도 `service_question_embeddings` 테이블은 만들지 않는다.
2. **`service_name` 과 `embedding_text` 는 모든 row에 기록**한다. 디버깅·재현·운영 가시성을 위함. JOIN 비용도 절감.
3. **Track A 임베딩 텍스트는 `place_name`까지 포함**한다. `{area} {max} {min} {service} {place}`.
4. **Track B는 `extracted.summary` 단일 입력**. 다른 추출 항목은 BM25 색인용일 뿐 임베딩 입력이 아니다. metadata.extracted는 identity row에만 둔다.
5. **구조화 추출은 Track B 전처리로 통합**한다. `cleaned_detail` 유무에 따라 full extraction / metadata-only fallback 두 경로로 분기. 두 경로 모두 LLM을 호출하여 `summary` 를 채운다.
6. **LLM 실패 시 템플릿 합성 폴백을 두지 않는다.** `_compose_minimal_summary` 같은 메타 컬럼 concat 결과는 Track A 입력의 클론이 되어 벡터 공간 변별력을 훼손한다. LLM 실패 시 identity row만 적재하고 summary/question row는 만들지 않는다.
7. **미적재 row_kind는 NULL 컬럼이 아니라 row의 부재로 표현**한다. 검색 쿼리는 자동으로 빠지므로 별도 NULL 필터 불필요. `--retry-failed` 는 `WHERE NOT EXISTS (SELECT 1 FROM service_embeddings WHERE service_id=X AND row_kind='summary')` 식으로 판별.
8. **Track C는 시설당 5~15개, 기본 10개**. `llm.embedding_config.HYQE_QUESTIONS_PER_SERVICE` 로 제어.
9. **분포 강제는 LLM 출력 검증으로 처리**한다. ±10% 어긋나면 1회 재시도, 그 후에는 폴백 템플릿으로 보충.
10. **시설 1건 = 1 트랜잭션**. 트랙별 row 부분 적재로 인한 일관성 깨짐을 막는다. 재적재 시 동일 service_id의 기존 row를 모두 DELETE 후 INSERT.
10a. **트랙 모듈 분리.** `scripts/tracks/{identity,summary,questions}.py` 가 각각 자기 트랙의 텍스트 합성·임베딩·INSERT를 캡슐화한다. `embed_metadata.py` 는 정제·추출·트랙 호출 순서만 책임지는 얇은 오케스트레이터.
10b. **트랜잭션은 오케스트레이터가 관리.** 트랙 모듈은 commit/rollback하지 않는다. 호출자가 `session.begin()` 으로 감싼다.
10c. **배치와 API는 같은 `process_service` 를 거친다.** `POST /embeddings/services/sync` 의 백그라운드 워커는 `scripts/embed_metadata.py` 의 `process_service` 함수를 직접 import하여 호출한다. 분기 코드를 두지 않으므로 처리 결과 일관성이 자동 보장된다.
11. **BM25는 identity row partial index**. summary/question 텍스트는 색인하지 않아 IDF 오염을 막는다. 의미·세부 검색은 벡터 채널이 담당.
12. **검색 단계 (Phase 1): RRF 없이 단일 경쟁 쿼리**. `DISTINCT ON (service_id)` 로 dedup. 가중치도 없음. 측정 후 RRF 도입은 후속 계획.
13. **봉인 평가셋(80개)은 코드에서 import 금지**. 검증은 `tests/test_eval_set_isolation.py` 가 grep 기반으로 강제한다.
14. **본 계획 종료 시점**: 새 스키마로 적재 완료, `vector_search` 가 단일 경쟁 쿼리로 동작, BM25는 단순 union으로 결합. VectorSubIntent / 가중 RRF / 트랙별 partial query는 후속 계획에서 도입.
