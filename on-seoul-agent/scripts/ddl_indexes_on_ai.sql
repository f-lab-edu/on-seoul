-- =============================================================================
-- on_ai DB — service_embeddings 인덱스
-- 대상 DB  : on_ai
-- 실행 계정: on_ai_app (또는 superuser)
-- 목적     : question_search / vector_search 쿼리 가속
--
-- 운영 중 무중단 적용: CREATE INDEX CONCURRENTLY 사용.
-- CONCURRENTLY는 트랜잭션 블록 안에서 실행할 수 없으므로
-- 이 파일은 psql 단독 세션에서 실행하거나 Flyway의 outOfOrder=true + runInTransaction=false로 실행한다.
--
-- 롤백:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_se_question_service_id;
-- =============================================================================


-- =============================================================================
-- [question_search] partial index — row_kind='question' 행만 인덱싱
--
-- 쿼리 패턴 (tools/question_search.py):
--   SELECT DISTINCT ON (service_id) ...
--   FROM service_embeddings
--   WHERE row_kind = 'question'
--     AND 1 - (embedding <=> :query_vector) >= :min_similarity
--   ORDER BY service_id, embedding <=> :query_vector
--   LIMIT :top_k
--
-- 실행 계획:
--   Index Scan using idx_se_question_service_id
--     → Incremental Sort (service_id presorted)
--       → Unique (DISTINCT ON)
--         → Limit
--
-- 전체 인덱스 대비 효과:
--   - row_kind='question' 이외 행을 인덱스에서 제외해 인덱스 크기 및 Buffer hit 감소.
--   - EXPLAIN 실측: Rows Removed by Filter 1,439 → 1,071 (-26%), Buffers 4,124 → 3,692 (-10%).
--   - Execution Time 6.1 ms → 5.7 ms.
-- =============================================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_se_question_service_id
    ON service_embeddings (service_id)
    WHERE row_kind = 'question';


-- =============================================================================
-- 검증 쿼리 (실행 계획 확인)

-- question_search 대표 패턴:
--  EXPLAIN (ANALYZE, BUFFERS)
--  SELECT DISTINCT ON (service_id)
--      service_id, embedding_text, intent_label,
--      1 - (embedding <=> '[...vector...]'::vector) AS similarity
--  FROM service_embeddings
--  WHERE row_kind = 'question'
--    AND 1 - (embedding <=> '[...vector...]'::vector) >= 0.7
--  ORDER BY service_id, embedding <=> '[...vector...]'::vector
--  LIMIT 5;
-- 기대: Index Scan on idx_se_question_service_id → Incremental Sort → Unique → Limit
-- =============================================================================
