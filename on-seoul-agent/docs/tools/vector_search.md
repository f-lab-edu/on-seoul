# vector_search

`on_ai.service_embeddings` 테이블에서 쿼리 벡터와 코사인 유사도가 높은 결과를 반환합니다.
카테고리·지역·상태를 pre-filter로 적용하여 관련 범위 내 유사도 비교 정확도를 높입니다.

## 시그니처

```python
async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    max_class_name: str | None = None,
    area_name: str | None = None,
    service_status: str | None = None,
    top_k: int = TOP_K,               # 기본값: 10
    min_similarity: float = MIN_SIMILARITY,  # 기본값: 0.6
) -> list[dict]:
```

## 파라미터

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `session` | `AsyncSession` | `on_ai_app` 계정 세션 (service_embeddings CRUD 권한) |
| `query_vector` | `list[float]` | 쿼리 임베딩 벡터 (차원: 1536) |
| `max_class_name` | `str \| None` | pre-filter: 대분류 카테고리. None이면 미적용 |
| `area_name` | `str \| None` | pre-filter: 자치구. None이면 미적용 |
| `service_status` | `str \| None` | pre-filter: 예약 상태. None이면 미적용 |
| `top_k` | `int` | 반환할 최대 결과 수. 기본값: 10 |
| `min_similarity` | `float` | 코사인 유사도 하한 (0~1). 기본값: 0.6 |

## 반환값

`list[dict]` — `service_id`, `service_name`, `metadata`, `similarity` 키를 가진 딕셔너리 리스트.
결과가 없으면 빈 리스트.

## 사용 예

```python
from tools.vector_search import vector_search

results = await vector_search(
    session,
    query_vector=[0.1, 0.2, ...],  # 1536차원
    area_name="강남구",
    min_similarity=0.7,
)
```
