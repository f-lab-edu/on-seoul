"""참조 해소(reference resolution) 규칙 — W1.

현재 message 가 직전 턴의 결과 엔티티를 가리키는 "지시 참조"인지 판정하고,
referential 이면 prev_entities 에서 대상 service_id 를 바인딩한다.

설계 결정(§4-4 판정 신호):
    규칙 기반(LLM 미사용). 결정적·저지연·무비용. 신호 3종:
      ① 지시대명사 — 이 곳/저기/그거/여기/방금/위에/방금그거 등
      ② 서수 — 첫번째/세번째/3번째/1번/두번째 ... (한글 + 아라비아)
      ③ 직전 라벨 부분일치 — prev_entities[].label 의 의미 토큰이 message 에 등장

게이트(과잉 carryover 방지):
    prev_entities 가 비어 있으면 무조건 non-referential([]).

다중 참조("1번이랑 3번 비교") 시 복수 service_id 를 순서대로 바인딩한다.
"""

import re

# ① 지시대명사·지시 표현. 부분 문자열 매칭(공백 제거 후)으로 검사한다.
_DEMONSTRATIVES: tuple[str, ...] = (
    "이곳",
    "이거",
    "그거",
    "저거",
    "저기",
    "여기",
    "방금",
    "위에",
    "그곳",
    "거기",
    "이것",
    "그것",
    "저것",
    "해당",
)

# ② 한글 서수 → 0-base 인덱스. "첫번째"=0, "세번째"=2 ...
# 주의: "첫번쨰"는 흔한 오타 1종을 의도적으로 포함한다(첫번째의 오타).
_KOREAN_ORDINALS: dict[str, int] = {
    "첫번째": 0,
    "첫번쨰": 0,  # 흔한 오타 1종
    "두번째": 1,
    "세번째": 2,
    "네번째": 3,
    "다섯번째": 4,
    "여섯번째": 5,
    "일곱번째": 6,
    "여덟번째": 7,
    "아홉번째": 8,
    "열번째": 9,
}

# 아라비아 서수 — 명시적 서수 "3번째"(번 뒤 째 필수). 1-base 숫자를 캡처.
# "3번 출구" / "10번 버스" 같은 순수 "N번"은 서수가 아니므로 여기서 잡지 않는다.
_NUMERIC_ORDINAL_RE = re.compile(r"(\d+)\s*번째")
# 순수 "N번"(째 없음) — 서수 신뢰도 낮음. 지시 신호(지시어/한글서수)가 함께 있을 때만 채택.
_NUMERIC_BEON_RE = re.compile(r"(\d+)\s*번(?!째)")

# 라벨 부분일치 시 무시할 일반 토큰(시설 카테고리 일반명사) — 과잉 매칭 방지.
_GENERIC_LABEL_TOKENS: frozenset[str] = frozenset(
    {
        "테니스장",
        "수영장",
        "풋살장",
        "체육관",
        "공원",
        "센터",
        "시설",
        "강좌",
        "교육",
        "행사",
        "공공서비스",
        "예약",
    }
)

# 순수 "N번"(째 없음)을 서수로 채택할 때만 쓰는 좁은 단서.
# "알려/추천/설명" 등 일반 검색 동사는 화제 전환에도 흔하므로 제외하고,
# 비교/선택 맥락을 강하게 시사하는 표현만 둔다("3번 출구 알려줘"는 비-서수).
_ORDINAL_CUES: tuple[str, ...] = (
    "어때",
    "어떤",
    "비교",
    "골라",
    "선택",
    "중에",
)


def _norm(text: str) -> str:
    """공백을 제거한 정규화 문자열 — 부분 문자열 매칭용."""
    return re.sub(r"\s+", "", text)


def _has_ordinal_cue(norm: str) -> bool:
    """순수 "N번" 채택용 보조 단서(비교/선택 맥락 또는 지시 표현)가 있는지."""
    return any(c in norm for c in _ORDINAL_CUES) or any(
        d in norm for d in _DEMONSTRATIVES
    )


def _ordinal_indices(message: str, norm: str, *, has_cue: bool) -> list[int]:
    """message 에서 서수(한글+아라비아)를 추출해 0-base 인덱스 리스트로 반환한다.

    출현 순서를 보존하지 않고, 한글 서수 → 아라비아 서수 순으로 모아 중복 제거한다
    (다중 참조의 바인딩 순서는 prev_entities 인덱스 오름차순으로 정규화).

    "N번째"(째 명시)는 항상 서수로 채택한다. 순수 "N번"(째 없음)은 서수 신뢰도가
    낮아("3번 출구", "10번 버스" 같은 비-서수 오탐) 참조 단서(has_cue)가 있을 때만
    채택한다.
    """
    indices: list[int] = []
    for word, idx in _KOREAN_ORDINALS.items():
        if word in norm:
            indices.append(idx)
    for m in _NUMERIC_ORDINAL_RE.finditer(message):
        n = int(m.group(1))
        if n >= 1:
            indices.append(n - 1)  # 1-base → 0-base
    # 순수 "N번"은 한글 서수가 함께 있거나 참조 단서가 있을 때만 채택.
    if indices or has_cue:
        for m in _NUMERIC_BEON_RE.finditer(message):
            n = int(m.group(1))
            if n >= 1:
                indices.append(n - 1)
    # 중복 제거 + 오름차순 정규화
    return sorted(set(indices))


def _label_matched_indices(prev_entities: list[dict], norm: str) -> list[int]:
    """직전 라벨이 message 에 부분일치하는 엔티티 인덱스를 반환한다.

    매칭 신호(강→약):
      1) 라벨 전체(정규화)가 message 에 포함 → 무조건 매칭.
      2) 라벨의 변별 토큰(비-일반, 2자 이상)이 2개 이상 동시 일치 → 무조건 매칭.
      3) 변별 토큰 1개만 일치 → 약한 신호. message 가 매칭 라벨에 없는 *다른*
         일반 카테고리 토큰을 도입하면(예: 라벨 "마포 코딩 강좌" vs 질의 "마포 수영장")
         화제 전환으로 보고 매칭하지 않는다. 그 외에는 매칭.

    의도: 단일 토큰(특히 자치구/지역명 같은 prefix)이 무관한 화제 전환 질의에
    오매칭하여 과잉 carryover 가 발생하는 것을 막는다.
    """
    matched: list[int] = []
    for i, ent in enumerate(prev_entities):
        label = ent.get("label") or ""
        label_norm = _norm(label)
        if label_norm and label_norm in norm:
            matched.append(i)
            continue
        label_tokens = [t for t in re.split(r"\s+", label) if len(t) >= 2]
        distinctive = [t for t in label_tokens if t not in _GENERIC_LABEL_TOKENS]
        hits = [t for t in distinctive if _norm(t) in norm]
        if len(hits) >= 2:
            matched.append(i)
            continue
        if len(hits) == 1:
            # 약한 신호: message 가 이 라벨에 없는 일반 카테고리 토큰을 들고 오면
            # 새 검색(화제 전환)으로 간주하고 바인딩하지 않는다.
            # 단, 변별 토큰의 일부로 포함된 일반 토큰("마루공원" 안의 "공원")은
            # 도입으로 보지 않는다(거짓 양성 방지).
            distinctive_blob = _norm(" ".join(distinctive))
            intro_generic = any(
                g in norm and g not in distinctive_blob
                for g in _GENERIC_LABEL_TOKENS
            )
            if not intro_generic:
                matched.append(i)
    return matched


def resolve_reference(
    message: str,
    prev_entities: list[dict] | None,
) -> list[str]:
    """현재 message 가 지시 참조인지 판정하고 대상 service_id 를 바인딩한다.

    다중 참조 순서 계약: 바인딩 결과는 message 의 등장 순서가 아니라
    prev_entities 인덱스 오름차순으로 정규화된다("3번이랑 1번" → [1번, 3번]).

    Returns:
        referential 이면 바인딩된 service_id 리스트(다중 가능).
        non-referential 이면 빈 리스트.

    게이트: prev_entities 가 비어 있으면 무조건 [] (non-referential).
    """
    entities = prev_entities or []
    if not entities:
        return []

    norm = _norm(message)
    has_cue = _has_ordinal_cue(norm)

    # ② 서수 — 명시적이므로 최우선. 범위 밖 인덱스는 무시.
    ord_indices = [
        i
        for i in _ordinal_indices(message, norm, has_cue=has_cue)
        if 0 <= i < len(entities)
    ]
    if ord_indices:
        return [entities[i]["service_id"] for i in ord_indices]

    # ③ 라벨 부분일치 — 특정 엔티티만 가리키는 경우.
    label_indices = _label_matched_indices(entities, norm)
    if label_indices:
        return [entities[i]["service_id"] for i in label_indices]

    # ① 지시대명사 — 특정 엔티티 미지정이면 직전 엔티티 전체(소량)를 대상으로.
    #    가장 흔한 케이스("이 곳이 어떤 곳이야?")는 직전 1건을 가리키므로
    #    prev_entities 가 1건이면 그 1건, 다건이면 첫 엔티티를 대상으로 한다
    #    (다건 모호 시 첫 엔티티 = 직전 답변의 대표 항목).
    if any(d in norm for d in _DEMONSTRATIVES):
        return [entities[0]["service_id"]]

    return []
