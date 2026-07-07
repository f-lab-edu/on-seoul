"""노드 페이즈 공유 상수·헬퍼.

여러 페이즈가 공유하는 폴백 문구·user_rationale 정제 로직을 모은다.
페이즈 모듈은 이곳에서 import 한다(단방향 의존 — _shared 는 페이즈를 import 하지 않음).
"""

import re
from typing import Any

from tools.target_audience import matches_audience

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
# direct_answer_node 의 except 블록과 빈 답변 가드가 같은 출처를 재사용한다(drift 방지).
_FALLBACK_ANSWER = (
    "죄송합니다, 일시적인 오류가 발생했습니다. 잠시 후 다시 시도해 주세요."
)


# ---------------------------------------------------------------------------
# OUT_OF_SCOPE 서브타입 동형 그룹 (식별 검색 + 정직 리다이렉트)
# ---------------------------------------------------------------------------
# attribute_gap 과 operational_detail 은 *검색 routing* 이 동형이다 — 둘 다 시설 식별
# 검색(vector)을 돌리고 0건 게이트·retry·종료를 공유한다. domain_outside(진짜 범위 밖)만
# 전면 거절한다. 이 predicate 는 "식별 검색이 필요한가"를 묻는 단일 출처다.
#
# 분기 지점: 검색은 동형이되 *답변 경로*는 갈린다. out_of_scope_node 가
# operational_detail 에는 전용 vector_sub_intent="operational_detail" 을 세팅하고,
# pre_answer prep 이 focal detail_content 를 발췌해 detail_excerpt 를 적재하면 answer 가
# 운영-상세 발췌 실답변을 생성한다(사례 162-163 근본 해소). 발췌가 없으면(키워드 부재 등)
# attribute_gap interim 리다이렉트로 정직 폴백한다. attribute_gap 자체는 현행 유지.
# 따라서 검색 routing 분기(graph route/0건 게이트/retry)는 여전히 is_gap_oos 동형이고,
# 답변 분기만 out_of_scope_type 원본으로 갈린다.
_GAP_OOS_TYPES: frozenset[str] = frozenset({"attribute_gap", "operational_detail"})


def is_gap_oos(oos_type: str | None) -> bool:
    """식별 검색이 필요한 동형 그룹(attribute_gap/operational_detail) 판정.

    domain_outside(진짜 범위 밖, 전면 거절)와 구분하는 단일 출처 predicate 다. *검색
    routing* 만 동형이며, 답변 경로는 out_of_scope_type 원본으로 분기한다.
    """
    return oos_type in _GAP_OOS_TYPES


# ---------------------------------------------------------------------------
# post-RRF 구조화 게이트 (채널 누출 차단, 계획서 P1+P2)
# ---------------------------------------------------------------------------


def apply_structured_gate(
    rows: list[dict[str, Any]],
    *,
    area_names: list[str] | None,
    target_audience: str | None,
) -> list[dict[str, Any]]:
    """hydrated 행을 구조화 필터(area_name 멤버십 + target_audience)로 거른다.

    벡터 검색은 identity(Track A) 채널에만 area post-filter 가 걸리므로,
    summary/question/bm25 채널로 들어온 타 지역·상충 대상 행이 RRF 병합 상위에
    생존한다(행23 강동구 누출). hydration 이후 원본 area_name/target_info 로 최종 교정한다.

    · area_names 지정 시: 행.area_name 이 area_names 집합에 없으면 drop.
    · target_audience 지정 시: matches_audience 로 drop(제한없음/가족 항상 통과).
    둘 다 None/[] 이면 해당 축은 미적용(no-op). SQL 경로는 WHERE 로 이미 걸리므로 이
    게이트를 다시 통과해도 결과 불변이라 무해하다(matches_audience 공용).

    max_class_name 은 여기서 하드 게이트하지 않는다: 배제("체육시설 말고")는 SQL WHERE
    의 여집합 ANY 로 이미 하드 처리되고, 카테고리는 큐레이션에서 soft 강등(대안 라벨)
    으로 다뤄 few-exact 시 빈약 대신 대안으로 표면화한다(_curate_display 계약).
    """
    # 방어: area_names 가 단일 문자열로 새어들어와도 chars 로 쪼개지지 않게 감싼다
    # (계약상 리스트지만 상류 오주입 시 char-set 오필터를 막는다).
    if isinstance(area_names, str):
        area_names = [area_names]
    area_set = set(area_names) if area_names else None
    if area_set is None and not target_audience:
        return rows
    kept: list[dict[str, Any]] = []
    for row in rows:
        if area_set is not None and row.get("area_name") not in area_set:
            continue
        if not matches_audience(row.get("target_info"), target_audience):
            continue
        kept.append(row)
    return kept
