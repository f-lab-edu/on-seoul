-- =============================================================================
-- chat_search_queries + chat_search_results — 검색 결과 관측(observability) 테이블
-- =============================================================================
--
-- 한 사용자 메시지(chat_messages.id) 단위로 모든 검색 채널의
--   - 입력 (무엇으로 검색했는지)              → chat_search_queries (채널당 1행)
--   - 출력 (어떤 시설이 어떤 순위로 반환됐는지) → chat_search_results (채널당 N행)
-- 을 별도 테이블에 분리 적재한다. (message_id, channel) 가 두 테이블의 join 키.
--
-- 설계 결정 근거:
--   docs/superpowers/plans/2026-05-19-chat-search-results-persistence.md (Task 1)
--
-- 두 테이블이 분리된 이유:
--   1) queries=채널당 1행, results=채널당 1~N행. 카디널리티가 다르므로 정규화.
--   2) queries.parameters 가 큰 JSONB(SQL 전체 필터/RRF 가중치 등) 인데,
--      이를 results 모든 row에 복제하면 낭비.
--   3) "무엇으로 검색했나" 와 "무엇이 반환됐나" 는 독립적 분석 질문.
--   4) search_persist_node 가 두 테이블을 한 트랜잭션으로 INSERT → 정합성 보장.
--
-- kind / channel 디자인:
--   * kind  — VARCHAR(8) + CHECK 화이트리스트 (6종: sql/vector/bm25/rrf/map/final).
--             안정적이라 확장 빈도 낮음. denormalize 되어 results 단독 분석 가능.
--   * channel — VARCHAR(32) freeform (CHECK 없음). Phase 2/3 채널 확장 자유 보장.
--               애플리케이션 측 SearchChannel 상수로 typo 방지.
-- =============================================================================

-- 멱등성: 재실행 시 데이터 손실 없도록 IF NOT EXISTS 패턴 사용.
-- 스키마 변경이 필요하면 별도 ALTER 마이그레이션으로 처리.

-- -----------------------------------------------------------------------------
-- chat_search_queries (input — 채널당 1행)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat_search_queries (
    id          BIGSERIAL    PRIMARY KEY,
    message_id  BIGINT       NOT NULL,
    kind        VARCHAR(8)   NOT NULL,
    channel     VARCHAR(32)  NOT NULL,
    query_text  TEXT,
    parameters  JSONB,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_chat_search_queries_message_channel
        UNIQUE (message_id, channel),
    CONSTRAINT ck_chat_search_queries_kind
        CHECK (kind IN ('sql', 'vector', 'bm25', 'rrf', 'map', 'final'))
);

-- -----------------------------------------------------------------------------
-- chat_search_results (output — 채널당 N행)
-- -----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS chat_search_results (
    id          BIGSERIAL     PRIMARY KEY,
    message_id  BIGINT        NOT NULL,
    kind        VARCHAR(8)    NOT NULL,
    channel     VARCHAR(32)   NOT NULL,
    rank        SMALLINT      NOT NULL,
    service_id  VARCHAR(255)  NOT NULL,
    score       DOUBLE PRECISION,
    meta        JSONB,
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_chat_search_results_message_channel_rank
        UNIQUE (message_id, channel, rank),
    CONSTRAINT ck_chat_search_results_rank_positive
        CHECK (rank >= 1),
    CONSTRAINT ck_chat_search_results_kind
        CHECK (kind IN ('sql', 'vector', 'bm25', 'rrf', 'map', 'final'))
);

-- =============================================================================
-- COMMENTS — chat_search_queries
-- =============================================================================

COMMENT ON TABLE chat_search_queries IS
'검색 입력(input) 관측 테이블. 한 사용자 메시지에서 각 검색 채널이 "무엇으로" 검색을 수행했는지 채널당 1행씩 기록한다. chat_search_results 와 (message_id, channel) 키로 join 된다.';

COMMENT ON COLUMN chat_search_queries.id IS
'서로게이트 PK. (message_id, channel) UNIQUE 가 비즈니스 키.';

COMMENT ON COLUMN chat_search_queries.message_id IS
'on_data.chat_messages.id 에 대한 논리 FK (cross-DB 이므로 물리 FK 없음). API 서비스가 발급한 메시지 ID.';

COMMENT ON COLUMN chat_search_queries.kind IS
'검색 종류 화이트리스트. 허용 값: sql=정형 SQL 검색 / vector=벡터 유사도 검색 / bm25=BM25 키워드 검색 / rrf=Reciprocal Rank Fusion 병합 / map=지도 반경 검색 / final=hydration·dedup·top_k 후 사용자에게 노출된 최종 목록. denormalize 되어 results 단독 분석 시에도 그룹화 가능.';

COMMENT ON COLUMN chat_search_queries.channel IS
'kind 내부 세부 디스크리미네이터 (freeform, CHECK 없음). 현재 알려진 채널: sql / vector (Phase 1) / vector_a / vector_b / vector_c (Phase 2) / hyde_vector (Phase 3) / bm25 / rrf / map / final. 채널 추가는 DB 마이그레이션 없이 가능. 코드에서는 SearchChannel 상수로만 사용.';

COMMENT ON COLUMN chat_search_queries.query_text IS
'사람-읽기 가능한 primary 검색 표현. 채널별 형태: sql=주요 keyword (없으면 NULL) / vector*=임베딩된 refined_query / bm25=BM25 토큰 join / map="lat=...,lng=...,r=...m" / rrf=NULL(원본 검색 미수행) / final=NULL(상위 채널 병합).';

COMMENT ON COLUMN chat_search_queries.parameters IS
'채널별 구조화 파라미터 JSONB. sql={filters dict, top_k} / vector={top_k, min_similarity} / bm25={tokens, top_k} / map={lat, lng, radius_m, top_k} / rrf={source_channels, weights, k_constant} / final={source_channels, hydration_applied}.';

COMMENT ON COLUMN chat_search_queries.created_at IS '적재 시각.';

COMMENT ON CONSTRAINT uq_chat_search_queries_message_channel ON chat_search_queries IS
'한 메시지의 한 채널은 query 1행만. self-correction 재시도 시 retry_prep_node 가 search_channels 를 리셋하므로 마지막 시도의 channel set 만 남는다.';

COMMENT ON CONSTRAINT ck_chat_search_queries_kind ON chat_search_queries IS
'kind 화이트리스트 — sql/vector/bm25/rrf/map/final 외 INSERT 차단. 새 kind 추가는 ALTER 필요(빈도 낮음).';

-- =============================================================================
-- COMMENTS — chat_search_results
-- =============================================================================

COMMENT ON TABLE chat_search_results IS
'검색 출력(output) 관측 테이블. 각 채널이 반환한 시설 순위를 채널당 1~N행 기록한다. chat_search_queries 와 (message_id, channel) 키로 join 되며, kind 는 denormalize 되어 단독 분석에도 사용 가능.';

COMMENT ON COLUMN chat_search_results.id IS
'서로게이트 PK. (message_id, channel, rank) UNIQUE 가 비즈니스 키.';

COMMENT ON COLUMN chat_search_results.message_id IS
'on_data.chat_messages.id 에 대한 논리 FK (cross-DB, 물리 FK 없음). chat_search_queries.message_id 와 동일.';

COMMENT ON COLUMN chat_search_results.kind IS
'검색 종류 화이트리스트. 허용 값: sql/vector/bm25/rrf/map/final. 동일 (message_id, channel) 의 chat_search_queries.kind 와 일치하도록 search_persist_node 가 단일 소스(kind_of(channel))로 보장한다.';

COMMENT ON COLUMN chat_search_results.channel IS
'kind 내부 세부 채널 (freeform). chat_search_queries.channel 와 동일 값이 들어간다. 현재 알려진 채널: sql / vector / vector_a / vector_b / vector_c / hyde_vector / bm25 / rrf / map / final.';

COMMENT ON COLUMN chat_search_results.rank IS
'채널 안에서의 1-based 순위. rank=1 이 최상위. CHECK (rank >= 1) 로 0 또는 음수 차단.';

COMMENT ON COLUMN chat_search_results.service_id IS
'반환된 시설의 service_id (public_service_reservations.service_id 와 동일 논리 키). 특정 시설이 어떤 질의에 surface 되었는지 역추적 가능.';

COMMENT ON COLUMN chat_search_results.score IS
'채널 native 점수. 채널별 의미: vector*=similarity (코사인) / bm25=bm25_score / rrf=rrf_score / map=distance_m / sql=NULL (점수 개념 없음) / final=NULL.';

COMMENT ON COLUMN chat_search_results.meta IS
'채널별 부가 정보 JSONB. 예시: vector_c={intent_label} / 임베딩 디버깅={embedding_text} / map={distance_m}. 채널 단위로 자유 형식.';

COMMENT ON COLUMN chat_search_results.created_at IS '적재 시각.';

COMMENT ON CONSTRAINT uq_chat_search_results_message_channel_rank ON chat_search_results IS
'(message_id, channel, rank) 조합 유일. 한 채널 안에서 동일 rank 가 두 번 들어갈 수 없다.';

COMMENT ON CONSTRAINT ck_chat_search_results_rank_positive ON chat_search_results IS
'rank 는 1-based. 0 또는 음수 차단으로 클라이언트의 off-by-one 실수 방지.';

COMMENT ON CONSTRAINT ck_chat_search_results_kind ON chat_search_results IS
'kind 화이트리스트 — sql/vector/bm25/rrf/map/final 외 INSERT 차단. chat_search_queries 와 동일 정책.';

-- =============================================================================
-- INDEXES — chat_search_queries
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_chat_search_queries_message_id
    ON chat_search_queries (message_id);

COMMENT ON INDEX idx_chat_search_queries_message_id IS
'메시지별 모든 채널 query 일괄 조회. 운영/디버깅 시 "이 메시지에서 어떤 검색들이 수행됐나" 추적용.';

CREATE INDEX IF NOT EXISTS idx_chat_search_queries_message_channel
    ON chat_search_queries (message_id, channel);

COMMENT ON INDEX idx_chat_search_queries_message_channel IS
'(message_id, channel) 복합 인덱스. chat_search_results 와의 join 키 검색 및 UNIQUE 제약 백킹 인덱스.';

-- =============================================================================
-- INDEXES — chat_search_results
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_chat_search_results_message_id
    ON chat_search_results (message_id);

COMMENT ON INDEX idx_chat_search_results_message_id IS
'메시지별 모든 결과 일괄 조회. 한 사용자 응답의 전체 검색 결과 reconstruction 용.';

CREATE INDEX IF NOT EXISTS idx_chat_search_results_message_channel
    ON chat_search_results (message_id, channel);

COMMENT ON INDEX idx_chat_search_results_message_channel IS
'(message_id, channel) 복합 인덱스. 특정 채널의 rank 순회 조회 및 chat_search_queries 와의 join 백킹.';

CREATE INDEX IF NOT EXISTS idx_chat_search_results_service_id
    ON chat_search_results (service_id);

COMMENT ON INDEX idx_chat_search_results_service_id IS
'시설 역추적 인덱스. "이 시설이 어떤 질의에 surface 됐는가" 와 같은 분석 쿼리를 지원한다.';

CREATE INDEX IF NOT EXISTS idx_chat_search_results_message_kind
    ON chat_search_results (message_id, kind);

COMMENT ON INDEX idx_chat_search_results_message_kind IS
'(message_id, kind) 인덱스. kind 단위 일괄 조회 (예: 한 메시지의 모든 vector 채널 결과)에 사용.';

-- =============================================================================
-- GRANTS (on_ai_app — AI 서비스 런타임 계정)
-- =============================================================================
--
-- on_ai_app 가 search_persist_node 에서 INSERT 한다. 분석 쿼리를 위해 SELECT 도 부여.
-- BIGSERIAL INSERT 시 시퀀스 USAGE 권한이 누락되면 권한 오류 발생하므로 명시적 GRANT.
-- 기존 on_ai DB grant 정책이 자동 적용된다면(예: GRANT ... ON ALL TABLES IN SCHEMA public)
-- 본 GRANT 는 idempotent 하게 중복 호출되어도 안전하다.

GRANT INSERT, SELECT ON chat_search_queries TO on_ai_app;
GRANT INSERT, SELECT ON chat_search_results TO on_ai_app;
GRANT USAGE,  SELECT ON SEQUENCE chat_search_queries_id_seq TO on_ai_app;
GRANT USAGE,  SELECT ON SEQUENCE chat_search_results_id_seq TO on_ai_app;
