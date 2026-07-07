"""런타임 프롬프트 조립 (어떤 절을 끼울지).

카드형(SQL/VECTOR) intent 의 시스템 프롬프트를 호출마다 조건부로 조립하고,
완화 안내·미표시 건수 안내 같은 동적 절을 생성한다. 프롬프트 텍스트(무엇을 말할지)는
llm.prompts.answer 에서 받아오고, 자치구 화이트리스트는 router_agent 에서 받아온다.
"""

from agents.router_agent import SEOUL_DISTRICTS
from llm.prompts.answer import (
    _CLAUSE_ALT_LABEL,
    _CLAUSE_REFINE_HINT,
    _CLAUSE_RESERVATION_GUIDE,
    _CLAUSE_SKEW_OFFER,
    _CLAUSE_THIN_CAVEAT,
    _OUTPUT_RULES,
    _ROLE,
    _STRUCT_CARD_LIST,
    _VOICE_GUIDE,
)

# 필터 키 → 사용자 노출용 한국어 라벨. 완화 안내 문구를 동적으로 구성할 때 사용한다.
_FILTER_LABELS: dict[str, str] = {
    "payment_type": "요금 조건",
    "area_name": "지역",
    "service_status": "접수 상태",
    "max_class_name": "카테고리",
}


def _relaxed_notice(relaxed_filters: list[str] | None) -> str:
    """완화한 필터 항목을 사용자 라벨로 안내하는 시스템 절을 동적으로 구성한다.

    드롭한 필터(relaxed_filters)를 한국어 라벨로 치환해 "무엇을 완화했는지" 밝힌다.
    추적값이 없으면(완화 사실은 있으나 항목 미상) 항목을 특정하지 않는 일반 문구로
    시작한다. 어느 경우든 유료 시설을 무료라고 오안내하지 않도록 강제한다(기존 가드 보존).
    """
    labels = [_FILTER_LABELS[f] for f in (relaxed_filters or []) if f in _FILTER_LABELS]
    if labels:
        joined = ", ".join(labels)
        head = (
            f"요청하신 조건 중 {joined} 을(를) 완화한 결과입니다. "
            f'답변 첫머리에 "요청하신 {joined} 조건에 정확히 맞는 결과가 없어 '
            f'{joined} 을(를) 완화한 결과입니다"와 같이 무엇을 완화했는지 반드시 안내하세요.'
        )
    else:
        head = (
            "요청하신 세부 조건에 정확히 맞는 결과가 없어, 조건을 일부 완화한 결과입니다. "
            '답변 첫머리에 "요청하신 조건에 정확히 맞는 결과가 없어 조건을 완화한 결과입니다"와 '
            "같이 완화 사실을 반드시 안내하세요."
        )
    return (
        head
        + "\n유료 시설을 무료라고 표현하지 마세요. 각 카드의 실제 요금 정보를 그대로 전달하세요."
    )


def _compose(*blocks: str) -> str:
    """비어있지 않은 블록들을 빈 줄로 연결한다."""
    return "\n\n".join(b.strip() for b in blocks if b.strip())


def _has_district_in_message(message: str) -> bool:
    """사용자 메시지에 서울 25개 자치구 공식 명칭이 포함되어 있는지 반환한다.

    SEOUL_DISTRICTS(공식 명칭 화이트리스트)만 인정하며, "강남" 같은 비공식 표기는
    false를 반환한다. _build_card_system에서 _CLAUSE_REFINE_HINT 절 포함 여부를
    결정할 때 사용한다.

    Args:
        message: 사용자 원본 발화 문자열.

    Returns:
        True  — 공식 자치구명이 하나 이상 포함된 경우.
        False — 공식 자치구명이 없거나 비공식 표기("강남")만 포함된 경우.
    """
    return any(district in message for district in SEOUL_DISTRICTS)


def _build_card_system(
    message: str,
    results: list[dict],
    area_name: str | None,
    *,
    retry_relaxed: bool = False,
    relaxed_filters: list[str] | None = None,
    result_quality: dict | None = None,
    reservation_guide_shown: bool = False,
    alt_count: int = 0,
) -> str:
    """카드형(SQL/VECTOR) intent의 시스템 프롬프트를 런타임에 조립한다.

    보이스 지침(_VOICE_GUIDE)을 상단 1회 고정하고, 조건부 꼬리말 절(_CLAUSE_*)을
    결과 자각(result_quality)·맥락(reservation_guide_shown)에 따라 조건화한다.

    조건부 절:
    - _CLAUSE_RESERVATION_GUIDE: 결과 중 service_status="접수중" 시설이 있고,
      직전 턴에 이미 안내하지 않았을 때(reservation_guide_shown=False)만 추가(반복 억제).
    - 지역 쏠림 자각 시(result_quality.skew_field=="area_name") _CLAUSE_REFINE_HINT 대신
      _CLAUSE_SKEW_OFFER(skew_value 치환)로 치환 — "어느 구냐" 되묻기 → 자각한 제안.
    - _CLAUSE_THIN_CAVEAT: result_quality.thin 이면 정직 캐비엇 추가. 단 완화
      재시도(retry_relaxed)와 겹치면 완화 안내로 통합하고 thin 캐비엇은 생략(중복 방지).
    - _CLAUSE_REFINE_HINT: 자치구 미해소 + 쏠림 자각 없을 때만 추가(현행 가드 유지).

    area_name 게이트:
        Router가 이미 해소한 state["filters"]["area_name"](현재 질문 또는 history
        병합)을 우선 확인한다. area_name이 채워져 있으면 follow-up("그 중 무료인 것만")
        에서도 refine hint를 생략하여 이미 지정한 자치구를 다시 묻지 않는다.
        _has_district_in_message는 area_name 미해소 시의 보조 fallback이다.

    Args:
        message: 사용자 원본 발화 (자치구 명시 여부 fallback 판단용).
        results: 정규화 이전 또는 이후 결과 목록 (service_status 키 접근).
        area_name: Router가 해소한 자치구명. 해소 실패 시 None.
        result_quality: 결과 품질 자각 패스 산출 플래그(쏠림·빈약) 또는 None(현행 조립).
        reservation_guide_shown: 직전 턴에 통합회원 안내가 이미 나갔는지(반복 억제).

    Returns:
        조립된 시스템 프롬프트 문자열.
    """
    rq = result_quality or {}
    is_area_skew = rq.get("skew_field") == "area_name"
    is_thin = bool(rq.get("thin"))

    blocks = [_ROLE, _VOICE_GUIDE, _STRUCT_CARD_LIST]
    if not reservation_guide_shown and any(
        r.get("service_status") == "접수중" for r in results
    ):
        blocks.append(_CLAUSE_RESERVATION_GUIDE)
    # 완화 재시도 결과(0건 후 조건 완화)이고 표시할 결과가 있으면 완화 안내 절을 추가한다.
    # 실제 드롭된 필터(relaxed_filters)를 사용자 라벨로 안내한다(동적 구성).
    relaxed_added = bool(retry_relaxed and results)
    if relaxed_added:
        blocks.append(_relaxed_notice(relaxed_filters))
    # 딱맞음/대안 라벨 절 — 상위 카드 중 대안이 섞였을 때만. 완화 안내가 이미
    # "조건을 넓힌 결과"임을 고지하면(relaxed_added) 중복이라 생략(길이/혼선 관리).
    if alt_count > 0 and results and not relaxed_added:
        blocks.append(_CLAUSE_ALT_LABEL)
    # 빈약 캐비엇 — 완화 안내와 겹치면(둘 다 "조건을 좁혀보라" 취지) 생략해 중복/길이 관리.
    if is_thin and results and not relaxed_added:
        blocks.append(_CLAUSE_THIN_CAVEAT)
    # 쏠림 자각 → SKEW_OFFER 치환, 아니면 자치구 미해소 시 기존 REFINE_HINT.
    if is_area_skew:
        blocks.append(_CLAUSE_SKEW_OFFER.format(skew_value=rq.get("skew_value") or ""))
    elif not area_name and not _has_district_in_message(message):
        blocks.append(_CLAUSE_REFINE_HINT)
    blocks.append(_OUTPUT_RULES)
    return _compose(*blocks)


def _more_notice(extra_count: int) -> str:
    """extra_count로부터 미표시 건수 안내 문구를 결정적으로 생성한다.

    LLM에 렌더 가능한 숫자 "0"을 노출하지 않기 위해 extra_count 값에 따라
    분기한다. extra_count가 0이면 "외 0건" 류 오출력을 막는 금지 지시를,
    0보다 크면 "외 N건"을 반드시 표기하라는 명시 지시를 반환한다.

    Args:
        extra_count: _DISPLAY_LIMIT 초과로 표시되지 않은 시설 건수 (>= 0).

    Returns:
        human 메시지 {more_notice} 자리에 주입할 안내 문장.
    """
    if extra_count > 0:
        return (
            f"표시되지 않은 시설이 {extra_count}건 더 있습니다. "
            f"목록 맨 끝 줄에 '외 {extra_count}건'을 반드시 표기하세요."
        )
    return "모든 결과를 표시했습니다. '외 N건' 류 표기를 절대 하지 마세요."
