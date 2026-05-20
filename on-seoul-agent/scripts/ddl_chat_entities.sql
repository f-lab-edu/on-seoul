-- on_ai DB Schema
-- AI 서비스 전용 DB. on_ai_app 계정(CRUD)으로 실행한다.

-- ============================================================
-- Vector Extension
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

\i scripts/ddl/service_embeddings.sql

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

-- ============================================================
-- 신규 DDL include (psql \i 메타 커맨드)
-- ============================================================

\i scripts/ddl/chat_search.sql
