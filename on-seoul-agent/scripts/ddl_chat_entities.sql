-- on_ai DB Schema
-- AI 서비스 전용 DB. on_ai_app 계정(CRUD)으로 실행한다.

-- ============================================================
-- Vector Extension
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================
-- service_embeddings
-- 시설 메타데이터 임베딩 벡터 (pgvector)
-- on_data.public_service_reservations 기준으로 embed_metadata.py 배치 적재
-- ============================================================

CREATE TABLE IF NOT EXISTS service_embeddings (
    id          BIGSERIAL PRIMARY KEY,
    service_id  VARCHAR(255)  NOT NULL UNIQUE,
    service_name TEXT          NOT NULL,
    metadata    JSONB,
    embedding   vector(1536),            -- Gemini gemini-embedding-2-preview (output_dimensionality=1536)
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_service_embeddings_service_id
    ON service_embeddings (service_id);

-- HNSW 인덱스 (코사인 유사도). 데이터 적재 완료 후 실행한다.
-- Phase 9 파라미터: m=16(연결수), ef_construction=64(빌드품질), ef_search=40(쿼리정확도)
-- 데이터 10000건 이상 시 m=32, ef_construction=128 재검토 권고
CREATE INDEX IF NOT EXISTS idx_service_embeddings_hnsw
    ON service_embeddings USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
-- ef_search 조정 방법:
--   세션 레벨(재연결 시 초기화): SET hnsw.ef_search = 40;
--   영속 적용: ALTER SYSTEM SET hnsw.ef_search = 40; → SELECT pg_reload_conf();

-- ============================================================
-- chat_agent_traces
-- LangGraph 에이전트 실행 메타데이터
-- on_data.chat_messages.id를 message_id로 논리 참조 (물리 FK 없음)
-- ============================================================

CREATE TABLE IF NOT EXISTS chat_agent_traces (
    id          BIGSERIAL PRIMARY KEY,
    message_id  BIGINT        NOT NULL,  -- chat_messages.id (논리 참조)
    trace       JSONB         NOT NULL,  -- intent, node 경로, tool 결과, 소요시간 등
    created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_agent_traces_message_id
    ON chat_agent_traces (message_id);
