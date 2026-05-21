-- =============================================================================
-- service_embeddings — Triple-Track 임베딩 통합 테이블 (row-per-vector 구조)
-- =============================================================================
--
-- 한 시설에 대해 다음 세 종류의 row가 적재된다.
--   row_kind='identity'  : 시설 식별 임베딩 (Track A). 1행/시설.
--   row_kind='summary'   : 추출 요약 임베딩 (Track B). 0~1행/시설.
--   row_kind='question'  : HyQE 예상질문 임베딩 (Track C). 0~N행/시설.
--
-- 단일 HNSW 인덱스로 모든 row가 경쟁하는 벡터 검색을 지원하고,
-- BM25는 identity row만 partial index로 색인하여 IDF 오염을 막는다.
--
-- 설계 결정 근거: docs/superpowers/plans/2026-05-18-triple-track-embedding-pipeline.md
-- =============================================================================

DROP TABLE IF EXISTS service_embeddings CASCADE;

CREATE TABLE service_embeddings (
    id             BIGSERIAL    PRIMARY KEY,
    service_id     VARCHAR(255) NOT NULL,
    row_kind       VARCHAR(16)  NOT NULL,
    idx            SMALLINT     NOT NULL DEFAULT 0,

    -- 모든 row에 기록 (디버깅·재현·운영 가시성)
    service_name   TEXT         NOT NULL,
    embedding_text TEXT         NOT NULL,
    embedding      vector(768)  NOT NULL,

    -- identity row에만 의미 있음 (다른 row는 NULL)
    metadata       JSONB,

    -- question row에만 의미 있음 (다른 row는 NULL)
    intent_label   VARCHAR(32),

    created_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_service_embeddings_row UNIQUE (service_id, row_kind, idx),
    CONSTRAINT ck_service_embeddings_row_kind
        CHECK (row_kind IN ('identity', 'summary', 'question')),
    CONSTRAINT ck_service_embeddings_question_intent_label
        CHECK ((row_kind = 'question') = (intent_label IS NOT NULL))
);

-- -----------------------------------------------------------------------------
-- COMMENTS
-- -----------------------------------------------------------------------------

COMMENT ON TABLE service_embeddings IS
'Triple-Track(A/B/C) 임베딩 통합 테이블. 한 시설당 identity 1행 + summary 0~1행 + question 0~N행으로 구성된다. 단일 HNSW 인덱스로 모든 row가 경쟁하는 벡터 검색을 지원한다.';

COMMENT ON COLUMN service_embeddings.id IS
'BM25 인덱스의 key_field 용 정수 PK. (service_id, row_kind, idx)는 UNIQUE 제약으로 보장.';

COMMENT ON COLUMN service_embeddings.service_id IS
'public_service_reservations.service_id 와 동일한 논리 키. 하나의 service_id 가 identity/summary/question row를 가질 수 있다.';

COMMENT ON COLUMN service_embeddings.row_kind IS
'트랙 디스크리미네이터. identity=Track A(시설 식별), summary=Track B(추출 요약), question=Track C(HyQE 예상질문).';

COMMENT ON COLUMN service_embeddings.idx IS
'question row의 순번(0~N-1). identity/summary row는 항상 0.';

COMMENT ON COLUMN service_embeddings.service_name IS
'원본 시설명. 모든 row에 복제 저장하여 row 단독 조회 시 즉시 식별 가능하도록 한다. JOIN 비용 절감 목적.';

COMMENT ON COLUMN service_embeddings.embedding_text IS
'실제로 임베딩 모델에 입력된 텍스트. 트랙별로 다름: identity=식별 텍스트, summary=extracted.summary, question=question_text. 검색 품질 디버깅·재현의 핵심 근거.';

COMMENT ON COLUMN service_embeddings.embedding IS
'embedding_text의 vector(768) 표현. Gemini text-embedding-2-preview, output_dimensionality=768, 코사인 유사도 기준.';

COMMENT ON COLUMN service_embeddings.metadata IS
'identity row에만 채워지는 JSONB. extracted 키 아래에 fee/operating_hours/cancellation/facilities/capacity/restrictions/summary 7개 추출 필드를 저장. BM25 색인 대상이지만 partial index로 identity row만 색인된다.';

COMMENT ON COLUMN service_embeddings.intent_label IS
'question row의 의도 라벨. semantic / detail / keyword 중 하나. CHECK 제약으로 row_kind=question 일 때만 NOT NULL, 그 외에는 NULL.';

COMMENT ON COLUMN service_embeddings.created_at IS '최초 적재 시각.';
COMMENT ON COLUMN service_embeddings.updated_at IS '마지막 재적재 시각. service_id 단위로 DELETE-INSERT 시 갱신.';

COMMENT ON CONSTRAINT uq_service_embeddings_row ON service_embeddings IS
'(service_id, row_kind, idx) 조합 유일성. UPSERT 충돌 키.';

COMMENT ON CONSTRAINT ck_service_embeddings_row_kind ON service_embeddings IS
'row_kind 화이트리스트. 허용 값 외 INSERT 차단.';

COMMENT ON CONSTRAINT ck_service_embeddings_question_intent_label ON service_embeddings IS
'intent_label은 question row와 정확히 동치. identity/summary row의 NULL과 question row의 NOT NULL을 동시에 강제한다.';

-- -----------------------------------------------------------------------------
-- 인덱스
-- -----------------------------------------------------------------------------

-- 단일 HNSW: 모든 row_kind가 경쟁하는 단일 vector 쿼리에 사용 (Phase 1).
-- 후속 RRF 계획에서 row_kind별 partial 인덱스 추가 여부를 결정.
CREATE INDEX idx_service_embeddings_hnsw
    ON service_embeddings
    USING hnsw (embedding vector_cosine_ops);

COMMENT ON INDEX idx_service_embeddings_hnsw IS
'전체 row_kind 대상 단일 HNSW 인덱스. row-per-vector 구조에서 모든 트랙이 같은 벡터 공간에서 경쟁한다.';

DROP INDEX idx_service_embeddings_service_id;
-- service_id 역참조: hydration, 재적재 시 DELETE.
CREATE INDEX idx_service_embeddings_service_id
    ON service_embeddings (service_id);

COMMENT ON INDEX idx_service_embeddings_service_id IS
'service_id 역참조 인덱스. hydration 및 재적재 시 DELETE WHERE service_id=X 에 사용.';

-- BM25 partial: identity row만 색인. summary/question 텍스트의 IDF 오염을 방지.
DROP INDEX idx_service_embeddings_bm25;
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

COMMENT ON INDEX idx_service_embeddings_bm25 IS
'BM25 partial index — identity row만 색인. summary/question 텍스트를 색인하면 도메인 공통어의 IDF가 오염되므로 의도적으로 제외. 의미·세부 검색은 벡터 채널이 담당한다.';
