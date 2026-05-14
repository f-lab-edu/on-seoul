-- =============================================================================
-- on_data DB — public_service_reservations 인덱스
-- 대상 DB  : on_data
-- 실행 계정: on_data_app (또는 superuser)
-- 목적     : sql_search / map_search 쿼리 가속
--
-- 운영 중 무중단 적용: CREATE INDEX CONCURRENTLY 사용.
-- CONCURRENTLY는 트랜잭션 블록 안에서 실행할 수 없으므로
-- 이 파일은 psql 단독 세션에서 실행하거나 Flyway의 outOfOrder=true + runInTransaction=false로 실행한다.
--
-- 롤백:
--   DROP INDEX CONCURRENTLY IF EXISTS idx_psr_active_class;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_psr_active_area;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_psr_active_status;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_psr_active_receipt_start;
--   DROP INDEX CONCURRENTLY IF EXISTS idx_psr_active_ll_to_earth;

--   DROP EXTENSION IF EXISTS pg_trgm;
--   DROP EXTENSION IF EXISTS earthdistance;
--   DROP EXTENSION IF EXISTS cube;
-- =============================================================================

-- =============================================================================
-- 확장 설치
-- cube는 earthdistance의 의존 확장이므로 반드시 먼저 설치한다.
-- =============================================================================

CREATE EXTENSION IF NOT EXISTS cube;
CREATE EXTENSION IF NOT EXISTS earthdistance;

-- pg_trgm: ILIKE '%keyword%' 패턴에 GIN 인덱스를 적용할 수 있게 한다.
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- =============================================================================
-- [sql_search] 부분 인덱스 — deleted_at IS NULL 행만 인덱싱
--
-- 쿼리 패턴:
--   WHERE deleted_at IS NULL [AND max_class_name = :v] [AND area_name = :v]
--         [AND service_status = :v]
--   ORDER BY receipt_start_dt DESC NULLS LAST
--
-- 세 필터는 선택적으로 조합되므로 단일 복합 인덱스보다 컬럼별 부분 인덱스가
-- 옵티마이저 선택 자유도가 높고 인덱스 크기도 작다.
-- receipt_start_dt DESC 정렬 인덱스는 필터 없는 최신 순 조회에도 활용된다.
-- =============================================================================

-- max_class_name 필터 (체육시설·문화행사·시설대관·교육·진료)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_psr_active_class
    ON public_service_reservations (max_class_name)
    WHERE deleted_at IS NULL;

-- area_name 필터 (서울 자치구, 예: 강남구)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_psr_active_area
    ON public_service_reservations (area_name)
    WHERE deleted_at IS NULL;

-- service_status 필터 (접수중·예약마감·접수종료·예약일시중지·안내중)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_psr_active_status
    ON public_service_reservations (service_status)
    WHERE deleted_at IS NULL;

-- receipt_start_dt DESC 정렬 — ORDER BY receipt_start_dt DESC NULLS LAST 가속
-- NULLS LAST는 DESC 기본값과 동일하므로 인덱스 스캔 방향이 일치한다.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_psr_active_receipt_start
    ON public_service_reservations (receipt_start_dt DESC NULLS LAST)
    WHERE deleted_at IS NULL;


-- =============================================================================
-- [map_search] ll_to_earth 함수 기반 GiST 부분 인덱스
--
-- 쿼리 패턴:
--   earth_distance(ll_to_earth(:lat, :lng),
--                  ll_to_earth(CAST(coord_y AS float), CAST(coord_x AS float)))
--   WHERE deleted_at IS NULL AND coord_x IS NOT NULL AND coord_y IS NOT NULL
--
-- coord_x(경도)/coord_y(위도) 컬럼은 VARCHAR 또는 NUMERIC으로 저장될 수 있으므로
-- 인덱스 식에도 CAST(... AS float)를 포함한다.
-- ll_to_earth 반환 타입은 earth(= cube 별칭)이므로 GiST 연산자 클래스를 사용한다.
--
-- 반경 필터링은 <@ 연산자(earth_box)로 옵티마이저가 인덱스를 활용하도록
-- 쿼리를 다음과 같이 작성하면 더 효과적이다:
--   WHERE ll_to_earth(...) <@ earth_box(ll_to_earth(:lat,:lng), :radius_m)
-- 현재 map_search.py는 CTE + distance_m <= :radius_m 패턴을 사용하여
-- 옵티마이저가 Index Scan보다 Seq Scan을 선택할 수 있다.
-- 인덱스를 최대한 활용하려면 추후 earth_box 조건을 CTE 외부 WHERE 또는
-- 서브쿼리 WHERE에 추가하는 방식으로 쿼리를 개선할 것을 권고한다.
-- =============================================================================

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_psr_active_ll_to_earth
    ON public_service_reservations
        USING gist (
                    ll_to_earth(
                            CAST(coord_y AS float),
                            CAST(coord_x AS float)
                    )
            )
    WHERE deleted_at IS NULL
        AND coord_x IS NOT NULL
        AND coord_y IS NOT NULL;

-- =============================================================================
-- 검증 쿼리 (실행 계획 확인)

-- sql_search 대표 패턴:
--  EXPLAIN (ANALYZE, BUFFERS)
--  SELECT service_id, service_name, receipt_start_dt
--  FROM public_service_reservations
--  WHERE deleted_at IS NULL
--    AND max_class_name = '체육시설'
--    AND area_name = '강남구'
--  ORDER BY receipt_start_dt DESC NULLS LAST
--  LIMIT 10;
-- 기대: Index Scan on idx_psr_active_class 또는 idx_psr_active_area

-- map_search 대표 패턴 (earth_box 조건 추가 버전):
--  EXPLAIN (ANALYZE, BUFFERS)
--  SELECT service_id, coord_x, coord_y
--  FROM public_service_reservations
--  WHERE deleted_at IS NULL
--    AND coord_x IS NOT NULL
--    AND coord_y IS NOT NULL
--    AND ll_to_earth(CAST(coord_y AS float), CAST(coord_x AS float))
--        <@ earth_box(ll_to_earth(37.5665, 126.9780), 1000);
-- 기대: Index Scan on idx_psr_active_ll_to_earth
-- =============================================================================