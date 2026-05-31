# analytics_search

`on_data.public_service_reservations` 테이블에서 집계/분포 질의를 실행합니다.
LLM이 SQL을 생성하지 않으며, AnalyticsAgent가 LLM 구조화 출력으로 파라미터를 추출한 뒤 이 도구를 호출합니다.
임베딩·RRF·hydration 단계를 거치지 않는 독립적인 정형 집계 경로입니다.

## 시그니처

```python
async def analytics_search(
    session: AsyncSession,
    *,
    group_by: str,
    metric: Literal["count", "distinct"] = "count",
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    keyword: str | None = None,
    top_k: int = 20,
) -> list[dict]:
```

## 파라미터

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `session` | `AsyncSession` | `on_data_reader` 계정 세션 (SELECT 전용) |
| `group_by` | `str` | 집계 차원. 허용값: `area_name` / `max_class_name` / `min_class_name` / `service_status` |
| `metric` | `Literal["count", "distinct"]` | 집계 방식. `count`=건수 집계(GROUP BY COUNT), `distinct`=고유값 목록(SELECT DISTINCT). 기본값: `"count"` |
| `max_class_name` | `str \| None` | 필터: 대분류 카테고리 (체육시설·문화행사·시설대관·교육·진료). None이면 미적용 |
| `area_name` | `str \| None` | 필터: 서울 자치구 (예: 강남구). None이면 미적용 |
| `service_status` | `str \| None` | 필터: 예약 상태 (접수중·예약마감·접수종료·예약일시중지·안내중). None이면 미적용 |
| `keyword` | `str \| None` | 필터: 시설명·장소명 키워드 (`%keyword%` ILIKE). None이면 미적용 |
| `top_k` | `int` | 최대 반환 건수. 기본값: 20 |

## 반환 스키마

결과가 없으면 빈 리스트를 반환한다.

### `metric="count"`

```python
[
    {"group_value": "강남구", "count": 42},
    {"group_value": "마포구", "count": 35},
    ...
]
```

### `metric="distinct"`

```python
[
    {"group_value": "체육시설"},
    {"group_value": "문화행사"},
    ...
]
```

## 차원 화이트리스트 (`_DIMENSION_COLUMNS`)

컬럼명은 아래 dict의 값만 f-string에 삽입할 수 있다. dict 외의 값은 도구 진입 시 즉시 `ValueError`로 거부된다.

| `group_by` 입력값 | 실제 컬럼명 |
|---|---|
| `area_name` | `area_name` |
| `max_class_name` | `max_class_name` |
| `min_class_name` | `min_class_name` |
| `service_status` | `service_status` |

```python
_DIMENSION_COLUMNS: dict[str, str] = {
    "area_name": "area_name",
    "max_class_name": "max_class_name",
    "min_class_name": "min_class_name",
    "service_status": "service_status",
}
```

## 보안 노트

- **컬럼명 인젝션 방지**: `group_by` 값은 화이트리스트 dict 조회로만 실제 컬럼명을 얻는다. dict 외 값은 `ValueError` 반환. f-string에 삽입되는 것은 dict의 값(하드코딩된 컬럼명)뿐이므로 SQL Injection이 불가능하다.
- **필터값·top_k**: `max_class_name`, `area_name`, `service_status`, `keyword`, `top_k` 전부 SQLAlchemy bind 파라미터로 처리한다.
- **세션 격리**: `on_data_reader` (SELECT 전용) 계정 세션만 사용한다. 쓰기 권한 없음.

## 인덱스 활용

| 필터 조건 | 활성 인덱스 |
|---|---|
| `max_class_name` 필터 | `idx_psr_max_class_name` (B-tree) — 대분류 조건 선적용으로 대상 행 급감 |
| `area_name` 필터 | `idx_psr_area_name` (B-tree, 있는 경우) |
| `min_class_name` (group_by 전용) | `max_class_name` 필터 선적용 후 소규모 sequential scan. 전용 인덱스 불필요 |

`min_class_name` 전용 인덱스를 두지 않은 이유: 대부분의 ANALYTICS 질의는 `max_class_name` 필터와 함께 호출되며, 해당 인덱스가 행을 충분히 줄여준다. 이후 `min_class_name` GROUP BY는 소규모 seq scan으로 처리 가능하다.

## 호출처

`AnalyticsAgent` (`agents/analytics_agent.py`) — LLM 구조화 출력으로 `group_by`, `metric`, 필터 파라미터를 추출한 뒤 이 도구를 호출한다.

## 사용 예

```python
from tools.analytics_search import analytics_search

# 자치구별 접수중 체육시설 건수
rows = await analytics_search(
    session,
    group_by="area_name",
    metric="count",
    max_class_name="체육시설",
    service_status="접수중",
    top_k=25,
)
# [{"group_value": "강남구", "count": 12}, ...]

# 전체 대분류 종류 목록
kinds = await analytics_search(
    session,
    group_by="max_class_name",
    metric="distinct",
)
# [{"group_value": "체육시설"}, {"group_value": "문화행사"}, ...]
```
