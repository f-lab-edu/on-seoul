"""노드 페이즈 공유 상수·헬퍼.

여러 페이즈가 공유하는 폴백 문구·user_rationale 정제 로직을 모은다.
페이즈 모듈은 이곳에서 import 한다(단방향 의존 — _shared 는 페이즈를 import 하지 않음).
"""

import re

# ---------------------------------------------------------------------------
# user_rationale sanitize
# ---------------------------------------------------------------------------

_RATIONALE_MAX_LEN = 200
_RATIONALE_ELLIPSIS = "..."
# 내부 시스템 패턴: 줄 시작이 '__'인 경우만 필터링한다.
# 예) "__internal_key: val", "__result: ..." 등 LLM이 내부 메타데이터를 줄 머리에 출력하는 패턴.
# "파이썬 __init__ 사용법"처럼 줄 중간에 __ 가 등장하는 정상 기술 설명은 보존한다.
_INTERNAL_LINE_PATTERN = re.compile(r"^__")


def sanitize_user_rationale(text: str | None) -> str | None:
    """TriageAgent LLM 출력에서 사용자 노출용 근거 1문장을 정제한다.

    정제 순서:
      1. None / 빈 문자열 → None 반환.
      2. 내부 메시지 패턴 제거: 줄 시작이 '__'인 줄만 제거(정규식 ^__).
         ("파이썬 __init__ 사용법"처럼 줄 중간에 '__'가 등장하는 정상 설명은 보존.)
      3. 최대 200자 truncate — 초과 시 말줄임표 추가.
      4. 결과가 빈 문자열이면 None 반환.
    """
    if not text:
        return None

    # 줄 단위로 내부 패턴 제거
    clean_lines = []
    for line in text.splitlines():
        if _INTERNAL_LINE_PATTERN.search(line):
            continue
        clean_lines.append(line)
    cleaned = " ".join(clean_lines).strip()

    if not cleaned:
        return None

    # 최대 길이 truncate
    if len(cleaned) > _RATIONALE_MAX_LEN:
        cleaned = (
            cleaned[: _RATIONALE_MAX_LEN - len(_RATIONALE_ELLIPSIS)]
            + _RATIONALE_ELLIPSIS
        )

    return cleaned if cleaned else None


# LLM 예외 / 빈 답변 시 공유 폴백 문구.
# direct_answer_node 의 except 블록과 빈 답변 가드(S1)가 같은 출처를 재사용한다(drift 방지).
_FALLBACK_ANSWER = (
    "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
)


# ---------------------------------------------------------------------------
# OUT_OF_SCOPE 서브타입 동형 그룹 (식별 검색 + 정직 리다이렉트)
# ---------------------------------------------------------------------------
# attribute_gap 과 operational_detail 은 P5 전까지 동형이다 — 둘 다 시설 식별 검색을
# 돌려 "운영 상세는 공식 페이지/바로가기에서 확인" 으로 정직하게 리다이렉트한다(부재
# 단정 금지). domain_outside(진짜 범위 밖)만 전면 거절한다.
#
# operational_detail 은 intake 병합으로 신설된 운영성 질문(폭염·휴무·주차·우천)이나
# 소비처가 없어 domain_outside 거절로 새던 회귀(사례 162-163)를 막기 위해 attribute_gap
# 과 같은 분기로 흘린다(out_of_scope_node/라우팅/0건 게이트/self-correction 모두 동형).
#
# P5 연계: P5 에서 operational_detail 은 detail_content 발췌 답변 경로로 분리된다 —
# 그 전까지 attribute_gap interim 리다이렉트로 정직 처리한다(거짓 단정만 제거).
_GAP_OOS_TYPES: frozenset[str] = frozenset({"attribute_gap", "operational_detail"})


def is_gap_oos(oos_type: str | None) -> bool:
    """식별 검색 + 정직 리다이렉트 동형 그룹(attribute_gap/operational_detail) 판정.

    domain_outside(진짜 범위 밖, 전면 거절)와 구분하는 단일 출처 predicate 다.
    """
    return oos_type in _GAP_OOS_TYPES
