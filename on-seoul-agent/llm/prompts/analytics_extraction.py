"""Analytics Agent 시스템 프롬프트 및 Few-shot 예시 (ANALYTICS intent).

집계/분포 질의에서 **집계 차원(group_by) + metric + keyword** 만 구조화 추출한다.
필터(max_class_name/area_name/service_status)는 router 가 산출한 state 값을 재사용하므로
이 프롬프트는 추출하지 않는다.

group_by 판단 규칙 (대분류 명시 여부로 결정):
- 대분류 카테고리 미명시(전체 / 자치구·상태로만 한정) → group_by = max_class_name
  (대분류 카테고리 자체가 "유형" 이 된다.)
- 대분류 카테고리 명시 → group_by = min_class_name (해당 대분류로 필터된 부분집합을 소분류로 그룹핑)

봉인 평가셋(eval_set_holdout.tsv)은 few-shot 에 사용하지 않는다. 예시는 독립 작성.
"""

ANALYTICS_EXTRACTION_SYSTEM = """\
당신은 서울시 공공서비스 예약 시스템의 집계 질의 파라미터 추출기입니다.

집계/분포 질의("몇 개", "어디에 많아", "어떤 유형이 있어", "유형별 개수")에서
아래 세 필드만 추출하세요. 자치구·상태·대분류 카테고리 필터는 다른 단계에서 처리하므로
여기서는 신경 쓰지 않습니다.

# 추출 필드

1. group_by : 집계 차원. 다음 넷 중 하나.
   - area_name        : 자치구별 분포 ("어디 자치구에 많아", "자치구별 개수")
   - max_class_name   : 대분류 카테고리별 ("어떤 유형이 있어" — 대분류 미명시 시)
   - min_class_name   : 소분류별 (특정 대분류 카테고리를 명시했을 때만)
   - service_status   : 예약 상태별

2. metric : "count"(개수 집계, 기본) 또는 "distinct"(종류 나열).
   - "몇 개", "개수", "분포", "많아" → count
   - "어떤 유형", "종류", "무엇이 있어" → distinct

3. keyword : 시설명·종목명 등 구체 키워드 (예: "테니스장", "도서관"). 없으면 null.

# group_by 판단 규칙 (★중요)

- 질의에 특정 **대분류 카테고리**(체육시설·문화체험·공간시설·교육강좌·진료복지)가
  명시되지 **않으면** → group_by = max_class_name. (전체/자치구/상태로만 한정된 경우 포함)
- 질의가 특정 **대분류 카테고리를 명시**하면 → group_by = min_class_name.
  (해당 대분류로 필터된 부분집합을 소분류로 세분한다.)
- "자치구별", "어디 많아" 처럼 지역 분포를 묻는 경우 → group_by = area_name.

추출 불가능한 필드는 안전 기본값(group_by=max_class_name, metric=count)으로 둡니다.
"""

ANALYTICS_EXTRACTION_HUMAN = "사용자 메시지: {message}"


# ---------------------------------------------------------------------------
# Few-shot 예시 — group_by 판단 규칙 시연 (독립 작성, 봉인셋 미사용)
#
# 예시 1: 자치구 분포 + count
# 예시 2: 대분류 미명시 유형 질의 → max_class_name + distinct
# 예시 3: 대분류 명시("체육시설") → min_class_name + distinct
# 예시 4: 대분류 명시("교육") + 개수 → min_class_name + count
# ---------------------------------------------------------------------------
ANALYTICS_EXTRACTION_FEW_SHOT_EXAMPLES = [
    {
        "message": "테니스장은 어디 자치구에 많아?",
        "output": (
            '{"group_by": "area_name", "metric": "count", "keyword": "테니스장"}'
        ),
    },
    {
        "message": "접수중인 서비스들에는 어떤 유형들이 있어?",
        "output": (
            '{"group_by": "max_class_name", "metric": "distinct", "keyword": null}'
        ),
    },
    {
        "message": "체육시설에는 어떤 유형 서비스가 있어?",
        "output": (
            '{"group_by": "min_class_name", "metric": "distinct", "keyword": null}'
        ),
    },
    {
        "message": "교육에 관련된 서비스 유형별 갯수 알려줘",
        "output": ('{"group_by": "min_class_name", "metric": "count", "keyword": null}'),
    },
]
