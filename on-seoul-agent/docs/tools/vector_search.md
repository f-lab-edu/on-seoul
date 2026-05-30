# vector_search

`on_ai.service_embeddings` 테이블에서 쿼리 벡터와 코사인 유사도가 높은 결과를 반환합니다.
HNSW 인덱스를 최대한 활용하기 위해 post-filter 전략을 사용합니다.
내부 서브쿼리에서 `scan_k`(= `top_k × SCAN_K_MULTIPLIER`) 건을 먼저 추출한 뒤,
서브쿼리 외부에서 카테고리·지역·상태·유사도 하한 필터를 적용합니다.

## 시그니처

```python
async def vector_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    row_kind: Literal["identity", "summary"] = "identity",
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
| `query_vector` | `list[float]` | 쿼리 임베딩 벡터 (차원: 768) |
| `row_kind` | `Literal["identity", "summary"]` | 검색 대상 트랙. `identity`=Track A(post-filter 적용), `summary`=Track B(post-filter 미적용). 기본값: `"identity"` |
| `max_class_name` | `str \| None` | post-filter: 대분류 카테고리. None이면 미적용 |
| `area_name` | `str \| None` | post-filter: 자치구. None이면 미적용 |
| `service_status` | `str \| None` | post-filter: 예약 상태 (접수중·예약마감·접수종료·예약일시중지·안내중). None이면 미적용 |
| `top_k` | `int` | 반환할 최대 결과 수. 기본값: 10 |
| `min_similarity` | `float` | 코사인 유사도 하한 (0~1). 기본값: 0.6. post-filter 단계에서 적용 |

## 검색 전략 (post-filter)

pgvector HNSW 인덱스는 WHERE 조건과 동시에 사용하면 sequential scan으로 폴백합니다.
이를 방지하기 위해 아래 두 단계로 검색합니다.

1. **서브쿼리 (HNSW 인덱스 활용)**: 필터 없이 전체 임베딩 대상으로 코사인 거리 기준 상위 `scan_k`건 추출
2. **외부 필터 (post-filter)**: `similarity >= min_similarity` 및 `max_class_name`·`area_name`·`service_status` 조건 적용 후 `top_k`건 반환

`SCAN_K_MULTIPLIER = 5` (기본값)이므로 `top_k=10`일 때 `scan_k=50`이 됩니다.
scan_k를 충분히 크게 잡아 post-filter 탈락으로 인한 결과 부족을 완충합니다.

## 트랙 구분

| `row_kind` | 임베딩 내용 | post-filter | 호출 주체 |
|---|---|---|---|
| `"identity"` (Track A) | 시설 신원 텍스트 (`area_name service_name max_class_name`) | 적용 (`max_class_name`·`area_name`·`service_status`) | `VectorAgent` (필터 있을 때) |
| `"summary"` (Track B) | 자연어 요약 설명 | 미적용 (metadata NULL) | `VectorAgent` (항상) |

Track C(question row)는 `question_search` 도구가 별도로 담당합니다.
VectorAgent는 두 트랙을 순차적으로 2회 호출합니다 (asyncpg 단일 세션 제약).

## 반환값

`list[dict]` — `service_id`, `service_name`, `metadata`, `similarity` 키를 가진 딕셔너리 리스트.
결과가 없으면 빈 리스트.

## 사용 예

```python
from tools.vector_search import vector_search

results = await vector_search(
    session,
    query_vector=[0.1, 0.2, ...],  # 768차원
    area_name="강남구",
    min_similarity=0.7,
)
```
