# sql_search

`public_service_reservations` 테이블을 파라미터화 SQL로 조회합니다.
LLM이 생성한 값을 포함한 모든 필터 값은 bind 파라미터로만 전달하여 SQL Injection을 방지합니다.

## 시그니처

```python
async def sql_search(
    session: AsyncSession,
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    keyword: str | None = None,
    top_k: int = TOP_K,          # 기본값: 10
) -> list[dict]:
```

## 파라미터

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `session` | `AsyncSession` | `on_data_reader` 계정 세션 (SELECT 전용) |
| `max_class_name` | `str \| None` | 대분류 카테고리 (체육시설·문화행사·시설대관·교육·진료) |
| `area_name` | `str \| None` | 서울 자치구 (예: 강남구) |
| `service_status` | `str \| None` | 예약 상태 (접수중·접수예정·마감·대기) |
| `keyword` | `str \| None` | 시설명·장소명 키워드 (`%keyword%` ILIKE 검색) |
| `top_k` | `int` | 최대 반환 건수. 기본값: 10 |

## 반환값

`list[dict]` — 아래 컬럼을 가진 딕셔너리 리스트. 결과가 없으면 빈 리스트.

`service_id`, `service_name`, `max_class_name`, `min_class_name`,
`area_name`, `place_name`, `service_status`, `payment_type`,
`service_url`, `receipt_start_dt`, `receipt_end_dt`,
`service_open_start_dt`, `service_open_end_dt`, `coord_x`, `coord_y`,
`target_info`

## 사용 예

```python
from tools.sql_search import sql_search

rows = await sql_search(
    session,
    max_class_name="체육시설",
    area_name="마포구",
    service_status="접수중",
)
```
