# question_search

`on_ai.service_embeddings`에서 `row_kind='question'`인 예상 질문 임베딩을 검색합니다.
같은 시설(`service_id`)에 여러 question row가 매칭될 경우 `ROW_NUMBER() OVER (PARTITION BY service_id)`로
최고 유사도 1건만 반환합니다.

## 시그니처

```python
async def question_search(
    session: AsyncSession,
    query_vector: list[float],
    *,
    scan_k: int = TOP_K * 3,
    top_k: int = TOP_K,               # 기본값: 10
    min_similarity: float = MIN_SIMILARITY,  # 기본값: 0.6
) -> list[dict]:
```

## 파라미터

| 파라미터 | 타입 | 설명 |
|---|---|---|
| `session` | `AsyncSession` | `on_ai_app` 계정 세션 |
| `query_vector` | `list[float]` | 쿼리 임베딩 벡터 (차원: 768) |
| `scan_k` | `int` | CTE 내부에서 가져올 후보 수. `TOP_K * 3` 기본값으로 service_id dedup 후 부족분 보완 |
| `top_k` | `int` | 반환할 최대 결과 수. 기본값: 10 |
| `min_similarity` | `float` | 코사인 유사도 하한 (0~1). 기본값: 0.6 |

## dedup 동작 (PARTITION BY)

한 시설이 여러 예상 질문을 가질 수 있습니다. 쿼리 벡터와 가장 유사한 question row 1건만
반환하기 위해 CTE 내부에서 `ROW_NUMBER()`를 사용합니다.

```sql
WITH ranked AS (
    SELECT
        service_id,
        embedding_text,
        intent_label,
        1 - (embedding <=> CAST(:q AS vector)) AS similarity,
        ROW_NUMBER() OVER (
            PARTITION BY service_id
            ORDER BY embedding <=> CAST(:q AS vector)
        ) AS service_rank
    FROM service_embeddings
    WHERE row_kind = 'question'
      AND 1 - (embedding <=> CAST(:q AS vector)) >= :min_similarity
    ORDER BY embedding <=> CAST(:q AS vector)
    LIMIT :scan_k
)
SELECT service_id, embedding_text, intent_label, similarity
FROM ranked
WHERE service_rank = 1
ORDER BY similarity DESC
LIMIT :top_k;
```

## 반환값

`list[dict]` — `service_id`, `embedding_text`, `intent_label`, `similarity` 키를 가진 딕셔너리 리스트.
`service_id`는 unique. 결과가 없으면 빈 리스트.

## 호출 주체

`VectorAgent`가 Track C로 호출합니다. post-filter는 미적용합니다 (question row의 metadata는 NULL).
카테고리·지역 필터는 Track A(`vector_search(row_kind="identity")`)가 책임집니다.

## 사용 예

```python
from tools.question_search import question_search

results = await question_search(
    session,
    query_vector=[0.1, 0.2, ...],  # 768차원
    min_similarity=0.6,
)
```
