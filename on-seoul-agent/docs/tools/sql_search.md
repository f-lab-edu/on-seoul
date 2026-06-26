# sql_search

`public_service_reservations` 테이블을 파라미터화 SQL로 조회합니다.
LLM이 생성한 값을 포함한 모든 필터 값은 bind 파라미터로만 전달하여 SQL Injection을 방지합니다.

## 시그니처

```python
from datetime import date

async def sql_search(
    session: AsyncSession,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    payment_type: str | None = None,
    keyword: str | None = None,
    receipt_date_from: date | None = None,
    receipt_date_to: date | None = None,
    top_k: int = TOP_K,          # 기본값: 10
) -> list[dict]:
```

## 파라미터

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `session` | `AsyncSession` | `on_data_reader` 계정 세션 (SELECT 전용) |
| `max_class_name` | `str \| None` | 대분류 카테고리 (체육시설, 문화행사, 시설대관, 교육, 진료) |
| `area_name` | `str \| None` | 서울 자치구 (예: 강남구) |
| `service_status` | `str \| None` | 예약 상태 (접수중, 예약마감, 접수종료, 예약일시중지, 안내중) |
| `payment_type` | `str \| None` | 결제 유형. `"무료"`이면 `payment_type = '무료'` 정확 매칭, `"유료"`이면 `payment_type LIKE '유료%'` 접두 매칭(원천 데이터의 `"유료"` / `"유료(요금안내문의)"`를 모두 포괄). 그 외 값/None이면 미적용 |
| `keyword` | `str \| None` | 시설명/장소명 키워드. `(service_name || ' ' || place_name)` 연결 표현식에 `%keyword%` ILIKE 검색 |
| `receipt_date_from` | `date \| None` | 접수 기간 시작 필터. `receipt_end_dt >= receipt_date_from`인 서비스만 포함(구간 겹침). None이면 미적용 |
| `receipt_date_to` | `date \| None` | 접수 기간 종료 필터. `receipt_start_dt <= receipt_date_to`인 서비스만 포함(구간 겹침). None이면 미적용 |
| `top_k` | `int` | 최대 반환 건수. 기본값: 10 |

### 접수 기간 구간 겹침

`receipt_date_from` / `receipt_date_to`는 함께 사용하면 요청 구간과 서비스 접수 구간의 겹침을 판정합니다.

```
[receipt_date_from, receipt_date_to] ∩ [receipt_start_dt, receipt_end_dt] ≠ ∅
  ↔ receipt_end_dt >= receipt_date_from AND receipt_start_dt <= receipt_date_to
```

두 인자는 독립적으로 적용되므로 한쪽만 지정하면 해당 경계 조건만 걸립니다.

## 반환값

`list[dict]` — 아래 컬럼을 가진 딕셔너리 리스트. 결과가 없으면 빈 리스트.

`service_id`, `service_name`, `max_class_name`, `min_class_name`,
`area_name`, `place_name`, `service_status`, `payment_type`,
`service_url`, `receipt_start_dt`, `receipt_end_dt`,
`service_open_start_dt`, `service_open_end_dt`,
`use_time_start`, `use_time_end`,
`cancel_std_type`, `cancel_std_days`, `tel_no`,
`coord_x`, `coord_y`, `target_info`

### 정렬 계약

결과는 `ORDER BY receipt_start_dt DESC NULLS LAST, service_id ASC`로 정렬됩니다.
접수 시작일 최신순이며, 시작일이 같으면 `service_id` 오름차순으로 정렬하여 완전히 결정적인 순서를 보장합니다. `receipt_start_dt`가 NULL인 행은 항상 마지막에 배치됩니다.

## 사용 예

```python
from datetime import date

from tools.sql_search import sql_search

rows = await sql_search(
    session,
    max_class_name="체육시설",
    area_name="마포구",
    service_status="접수중",
)

# 무료 + 접수 기간 겹침 필터
rows = await sql_search(
    session,
    payment_type="무료",
    receipt_date_from=date(2026, 6, 1),
    receipt_date_to=date(2026, 6, 30),
)
```
