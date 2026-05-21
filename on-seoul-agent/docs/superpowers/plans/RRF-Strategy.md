# RRF 결합 전략

## 개요

> on-seoul 하이브리드 검색에서 **다중 검색 채널의 결과를 결합하는 방식**을 정의한다. Track A/B/C 벡터 검색과 BM25 검색의 결과를 Reciprocal Rank Fusion으로 통합하고, 의도별 가중치 프로파일을 적용하기 위해 Router Agent를 확장한다.

본 문서는 [임베딩 개선 전략](./Embedding-Strategy.md)의 색인·검색 파이프라인을 전제로 한다. 임베딩 트랙 구성(Track A/B/C)과 HyDE 적용 방식은 해당 문서를 따른다.

---

## 결합 대상

RRF로 결합하는 검색 결과는 다음 네 가지다.

| 채널 | 데이터 소스 | 설명 |
|---|---|---|
| Track A 벡터 검색 | `service_embeddings WHERE row_kind='identity'` | `{area_name} {max_class_name} {min_class_name} {service_name} {place_name}` 임베딩 |
| Track B 벡터 검색 | `service_embeddings WHERE row_kind='summary'` | `extracted.summary` 임베딩 |
| Track C 벡터 검색 | `service_embeddings WHERE row_kind='question'` | LLM 생성 예상 질문 임베딩 (HyQE). 시설당 N행. |
| BM25 | `service_embeddings` BM25 partial index (`WHERE row_kind='identity'`) | `service_name` + `metadata.extracted` 색인, 원문 질의 사용 |

세 트랙 모두 동일한 `service_embeddings` 통합 테이블의 partial query다. Track C는 한 시설에 다수 row가 존재하므로 결합 전 SQL `ROW_NUMBER() OVER (PARTITION BY service_id)` 로 중복 제거를 수행하고 최고 rank만 유지한다.

---

## 가중치 결정 원칙

세 트랙의 벡터 검색 결과와 BM25 결과를 RRF로 결합한다. 가중치는 사전에 직관으로 정하지 않고 **평가셋 측정 후 결정**한다. RRF는 가중치 없이도 견고한 baseline을 제공하므로, 가중치 도입은 지표를 측정하여 조절해야 한다.

### 진행 순서

1. **비가중치 RRF**(1, 1, 1, 1)로 80개 평가셋을 측정한다.
2. 카테고리별 recall을 분석한다. Track C 비중을 올렸을 때 의미/맥락형 recall이 얼마나 오르고 식별형 recall이 얼마나 떨어지는지 수치로 확인한다.
3. 측정 결과를 보고 가중치를 조정한다. 검증 전 값은 코드에 박지 않고 config로 분리한다.

---

## Router Agent 확장

세 트랙으로 구성된 벡터 검색은 의도별로 트랙 가중치를 다르게 적용해야 효과가 극대화된다. 이를 위해 기존 Router Agent를 확장한다.

### 현재 구성

기존 Router Agent는 사용자 메시지를 IntentType 4종(`SQL_SEARCH`, `VECTOR_SEARCH`, `MAP`, `FALLBACK`) 중 하나로 분류한다. `recent_queries` 컨텍스트를 활용해 follow-up 질의의 카테고리/지역을 이어받는다.

### 확장 방향

`VECTOR_SEARCH`로 분류된 질의를 다시 **VectorSubIntent**로 세분화해 트랙 가중치 프로파일을 선택할 수 있게 한다.

```python
class VectorSubIntent(Enum):
    IDENTIFICATION = "identification"   # 시설명/지역+분류 식별형
    DETAIL         = "detail"           # 요금/취소/시간 등 세부정보형
    SEMANTIC       = "semantic"         # 활동/체험/맥락 의미형 (default)
```

분류 결과는 `VectorAgent`로 전달되어 트랙 가중치 프로파일을 결정한다. `_RefinedQuery` 추출 단계에서 이미 `intent_type`을 함께 생성하고 있으므로([임베딩 개선 전략 - HyDE 통합 절](./Embedding-Strategy.md) 참조), Router Agent와 VectorAgent 사이에서 아래와 같은 데이터 흐름이 생긴다.

```
Router Agent
    ├─ IntentType: VECTOR_SEARCH
    └─ VectorSubIntent: identification | detail | semantic
            │
            └─→ VectorAgent
                    ├─ _RefinedQuery 생성 (refined_query, hyde_document, filters)
                    └─ 트랙 가중치 프로파일 선택 (sub_intent 기반)
```

VectorSubIntent 분류는 별도 LLM 호출을 만들지 않고 Router Agent의 IntentType 분류 체인에 필드를 추가해 단일 호출 안에서 처리한다.

---

## 의도별 가중치 프로파일

초기 후보값은 다음과 같다. 측정 결과에 따라 조정한다.

| VectorSubIntent | Track A | Track B | Track C | 비고 |
|---|---|---|---|---|
| `identification` | 0.5 | 0.25 | 0.25 | BM25 단독으로 부족한 식별 질의 보조 |
| `detail` | 0.2 | 0.5 | 0.3 | 요약/추출 정보가 핵심 |
| `semantic` (default) | 0.15 | 0.35 | 0.5 | HyQE가 가장 강한 영역 |

### 적용 원칙

- VectorSubIntent 분류 정확도가 검증되기 전까지는 default 프로파일(`semantic`) 한 세트만 운영한다.
- 분류 정확도가 검증된 뒤 프로파일을 활성화하고, 잘못 분류된 케이스에서의 recall 회귀를 측정한다.
- 가중치 수치는 모두 config로 분리하고 코드에 박지 않는다.

---

## 리스크와 대응

| 리스크 | 영향 | 대응 |
|---|---|---|
| VectorSubIntent 오분류 | 잘못된 가중치 프로파일 적용으로 recall 회귀 | 분류 정확도 검증 전까지 default 프로파일 단일 운영 |
| 가중치 overfit | 평가셋에 맞춘 가중치가 실제 사용 분포와 어긋남 | 평가셋 80개로 측정, 나머지 20개를 holdout으로 활용 |
| BM25 채널 가중치 미정의 | 본 문서의 프로파일은 벡터 트랙만 다룸 | 측정 단계에서 BM25 가중치도 동시에 튜닝, 별도 컬럼으로 관리 |

---

## 결정 사항 요약

1. **비가중치 RRF로 baseline**을 잡고 평가셋 측정 후 가중치를 결정한다.
2. **Router Agent에 VectorSubIntent 분류**를 추가해 의도별 가중치 프로파일을 적용한다. 별도 LLM 호출은 만들지 않는다.
3. **VectorSubIntent 분류 정확도 검증 전까지는 default(`semantic`) 프로파일 단일 운영**한다.
4. **가중치 수치는 config로 분리**하고 코드에 박지 않는다.