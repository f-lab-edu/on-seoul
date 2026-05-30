-- =============================================================================
-- chat_search.sql 적용 검증 스크립트
-- =============================================================================
--
-- 사용법:
--   psql "$ON_AI_DSN" -f scripts/ddl/chat_search.verify.sql
--
-- 또는 개별 쿼리만 골라서 실행할 수도 있다. 본 파일은 read-only 검증 쿼리만 포함하며
-- INSERT/UPDATE/DELETE 는 마지막 "CHECK 제약 동작" 섹션에서 임시 데이터로만 수행하고
-- 모두 ROLLBACK 한다 (BEGIN/ROLLBACK 블록).
-- =============================================================================

\echo ''
\echo '=== [1/7] 테이블 존재 확인 ==='
\echo ''

SELECT table_name
FROM information_schema.tables
WHERE table_name IN ('chat_search_queries', 'chat_search_results')
ORDER BY table_name;
-- 기대: 2행 (chat_search_queries, chat_search_results)

\echo ''
\echo '=== [2/7] 컬럼 + COMMENT 적재 검증 (NULL 코멘트가 없어야 함) ==='
\echo ''

\echo '--- chat_search_queries ---'
SELECT ordinal_position AS pos,
       column_name,
       data_type,
       is_nullable,
       col_description('chat_search_queries'::regclass, ordinal_position) AS comment
FROM information_schema.columns
WHERE table_name = 'chat_search_queries'
ORDER BY ordinal_position;

\echo ''
\echo '--- chat_search_results ---'
SELECT ordinal_position AS pos,
       column_name,
       data_type,
       is_nullable,
       col_description('chat_search_results'::regclass, ordinal_position) AS comment
FROM information_schema.columns
WHERE table_name = 'chat_search_results'
ORDER BY ordinal_position;

\echo ''
\echo '=== [3/7] 테이블 COMMENT 확인 ==='
\echo ''

SELECT 'chat_search_queries' AS table_name,
       obj_description('chat_search_queries'::regclass) AS comment
UNION ALL
SELECT 'chat_search_results',
       obj_description('chat_search_results'::regclass);

\echo ''
\echo '=== [4/7] 인덱스 + COMMENT 확인 ==='
\echo ''

SELECT i.relname           AS index_name,
       t.relname           AS table_name,
       obj_description(i.oid, 'pg_class') AS comment
FROM pg_index x
JOIN pg_class i ON i.oid = x.indexrelid
JOIN pg_class t ON t.oid = x.indrelid
WHERE t.relname IN ('chat_search_queries', 'chat_search_results')
ORDER BY t.relname, i.relname;

\echo ''
\echo '=== [5/7] 제약 + COMMENT 확인 ==='
\echo ''

SELECT con.conname     AS constraint_name,
       cls.relname     AS table_name,
       con.contype     AS type,        -- u=UNIQUE, c=CHECK, p=PRIMARY KEY
       obj_description(con.oid, 'pg_constraint') AS comment
FROM pg_constraint con
JOIN pg_class cls ON cls.oid = con.conrelid
WHERE cls.relname IN ('chat_search_queries', 'chat_search_results')
ORDER BY cls.relname, con.contype, con.conname;

\echo ''
\echo '=== [6/7] 권한(GRANT) 확인 — on_ai_app ==='
\echo ''

SELECT grantee, table_name, privilege_type
FROM information_schema.role_table_grants
WHERE table_name IN ('chat_search_queries', 'chat_search_results')
  AND grantee = 'on_ai_app'
ORDER BY table_name, privilege_type;
-- 기대: 양 테이블에 INSERT, SELECT (각 2행)

SELECT grantee, object_name AS sequence_name, privilege_type
FROM information_schema.role_usage_grants
WHERE object_name IN ('chat_search_queries_id_seq', 'chat_search_results_id_seq')
  AND grantee = 'on_ai_app'
ORDER BY object_name, privilege_type;
-- 기대: 양 시퀀스에 USAGE, SELECT

\echo ''
\echo '=== [7/7] CHECK 제약 동작 검증 (롤백되므로 실데이터 영향 없음) ==='
\echo ''

BEGIN;

\echo '--- 7-1. invalid kind ("xyz") — chat_search_queries — 실패해야 함 ---'
\set ON_ERROR_STOP off
INSERT INTO chat_search_queries (message_id, kind, channel)
  VALUES (999999, 'xyz', 'vector');
\set ON_ERROR_STOP on
\echo '   (위에서 ERROR: check_constraint 위반 메시지가 나왔어야 정상)'

\echo ''
\echo '--- 7-2. rank=0 — chat_search_results — 실패해야 함 ---'
\set ON_ERROR_STOP off
INSERT INTO chat_search_results (message_id, kind, channel, rank, service_id)
  VALUES (999999, 'sql', 'sql', 0, 'X');
\set ON_ERROR_STOP on
\echo '   (위에서 ERROR: rank_positive 위반 메시지가 나왔어야 정상)'

\echo ''
\echo '--- 7-3. rrf 채널 query_text NULL — chat_search_queries — 성공해야 함 ---'
INSERT INTO chat_search_queries (message_id, kind, channel, query_text, parameters)
  VALUES (999999, 'rrf', 'rrf', NULL, '{"source_channels": ["vector_a", "vector_b"]}'::jsonb);
SELECT count(*) AS inserted FROM chat_search_queries WHERE message_id=999999 AND channel='rrf';
-- 기대: 1

\echo ''
\echo '--- 7-4. freeform channel ("future_channel") — 성공해야 함 (channel CHECK 없음) ---'
INSERT INTO chat_search_queries (message_id, kind, channel, query_text)
  VALUES (999999, 'vector', 'future_channel', 'test');
SELECT count(*) AS inserted FROM chat_search_queries WHERE message_id=999999 AND channel='future_channel';
-- 기대: 1

\echo ''
\echo '--- 7-5. (message_id, channel) UNIQUE 위반 — 실패해야 함 ---'
\set ON_ERROR_STOP off
INSERT INTO chat_search_queries (message_id, kind, channel, query_text)
  VALUES (999999, 'rrf', 'rrf', '중복');
\set ON_ERROR_STOP on
\echo '   (위에서 ERROR: unique constraint 위반 메시지가 나왔어야 정상)'

ROLLBACK;

\echo ''
\echo '=== 모든 검증 단계 완료. 위 출력에서 다음을 확인 ==='
\echo '  [1] 테이블 2개 표시'
\echo '  [2] 모든 컬럼의 comment 가 NULL 이 아님'
\echo '  [3] 두 테이블 모두 테이블 코멘트 존재'
\echo '  [4] 인덱스 6개(queries 2 + results 4) 모두 코멘트 존재'
\echo '  [5] 제약 9개(PK 2 + UNIQUE 2 + CHECK 5) 모두 코멘트 존재'
\echo '  [6] on_ai_app 의 INSERT/SELECT 권한이 두 테이블에 부여됨'
\echo '  [7] CHECK 제약 동작 — 7-1, 7-2, 7-5 ERROR / 7-3, 7-4 SUCCESS'
\echo ''
