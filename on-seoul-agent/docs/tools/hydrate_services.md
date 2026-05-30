# hydrate_services

`public_service_reservations` 테이블에서 `service_id` 리스트에 해당하는 원본 행을 조회합니다. 검색 결과의 순위를 유지하고, 원본 누락분(soft-delete 또는 미존재)은 자동으로 제외합니다.

## 시그니처

```python
async def hydrate_services(
    session: AsyncSession,
    service_ids: list[str],
) -> list[dict]:
```

## 파라미터

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `session` | `AsyncSession` | `on_data_reader` 계정 세션 (SELECT 전용) |
| `service_ids` | `list[str]` | 검색 순위 순서로 정렬된 service_id 리스트. 빈 리스트면 DB 호출 없이 빈 리스트 반환 |

## 반환값

`list[dict]` — `sql_search`와 동일한 컬럼 셋:

`service_id`, `service_name`, `max_class_name`, `min_class_name`,
`area_name`, `place_name`, `service_status`, `payment_type`,
`service_url`, `receipt_start_dt`, `receipt_end_dt`,
`service_open_start_dt`, `service_open_end_dt`, `coord_x`, `coord_y`,
`target_info`

입력 `service_ids` 순서를 보존한다. 원본에 없는 service_id는 결과에서 제외된다.

## 안전성

- service_id 값은 단일 `ARRAY` bind 파라미터(`:service_ids`)로 전달된다 (SQL Injection 방지).
- `deleted_at IS NULL` 필터로 soft-delete된 행은 제외된다.

## 사용 예

```python
from tools.hydrate_services import hydrate_services

ranked_ids = ["S004", "S001", "S009"]  # RRF 순위
hydrated = await hydrate_services(data_session, ranked_ids)
# hydrated[0]["service_id"] == "S004", hydrated[0]["service_status"] = 최신 원본 값
```
