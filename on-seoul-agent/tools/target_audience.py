"""대상(target_audience) 토큰맵 — SQL 술어·post-RRF gate 공용 단일 출처.

target_info 는 자유텍스트지만 프로그램이 받는 *모든* 대상을 콤마로 나열한다
("성인, 초등학생, 청소년"). 따라서 콤마분리·괄호파싱 없이 "질의그룹 토큰이 하나라도
들어있으면 통과" 로 다중값을 자동 흡수한다. `제한없음`·`가족`은 항상 통과.

이 토큰맵을 단일 출처로 두고 Python 헬퍼(matches_audience)와 SQL 술어 빌더
(build_audience_sql)가 같은 맵에서 파생돼 로직 이중화를 막는다(계획서 P1+P2).

비대칭 의도적:
  - SENIOR 는 `성인` 유지, `유아/어린이` 배제(행25: 어르신 질의에 성인 프로그램 유지).
  - CHILD 는 `성인/청년` 배제(행20·30: 초등/아이 질의에 성인 누출 차단).
"""

from typing import Any

# 질의그룹(coarse 4종) → 매칭 토큰. 토큰맵은 튜닝 가능하나 단일 출처로만 수정한다.
AUDIENCE_TOKENS: dict[str, tuple[str, ...]] = {
    "CHILD": ("유아", "어린이", "초등학생", "청소년", "중학생", "고등학생"),
    "ADULT": ("성인", "청년", "어르신"),
    "SENIOR": ("어르신", "성인"),
    "FAMILY": ("가족",),
}

# 대상 무관 통과 토큰 — target_info 에 이 중 하나라도 포함되면 그룹 무관 항상 통과.
_ALWAYS_PASS_TOKENS: tuple[str, ...] = ("제한없음", "가족")

# router/vector 추출 검증용 — 허용 enum(그 외는 None 으로 정규화).
ALLOWED_AUDIENCES: frozenset[str] = frozenset(AUDIENCE_TOKENS)


def matches_audience(target_info: str | None, group: str | None) -> bool:
    """target_info 가 질의그룹(group)에 부합하는지 부분문자열 포함 매칭으로 판정한다.

    규칙(계획서 P1+P2):
      keep IF  '제한없음' 또는 '가족' 포함
            OR 그룹 토큰 중 하나라도 포함
      drop otherwise (상충 대상만 남은 행).

    group 이 None/미지(허용 enum 밖)면 필터 미적용으로 보아 항상 통과한다.
    target_info 가 None/빈값이면 그룹 명시 시 drop(대상 정보 없는 행은 상충으로 간주).
    """
    tokens = AUDIENCE_TOKENS.get(group) if group else None
    if tokens is None:
        return True  # 그룹 미지정/미지 → 필터 없음
    if not target_info:
        return False
    if any(tok in target_info for tok in _ALWAYS_PASS_TOKENS):
        return True
    return any(tok in target_info for tok in tokens)


def build_audience_sql(
    group: str | None,
    *,
    column: str = "target_info",
    param_prefix: str = "aud",
) -> tuple[str | None, dict[str, Any]]:
    """group → SQL OR-LIKE 술어 문자열 + bind dict 를 파생한다(파라미터화 유지).

    술어는 matches_audience 와 동일 로직:
      (col LIKE %제한없음% OR col LIKE %가족% OR col LIKE %<토큰>% OR ...)

    토큰은 서버측 고정맵이지만 사용자 값 삽입 금지 원칙대로 전부 bind 파라미터로 넣는다.
    group 이 None/미지면 (None, {}) 반환 → 호출자가 조건을 생략한다.
    컬럼명(column)은 신뢰된 내부 상수만 전달한다(사용자 입력 아님).
    """
    tokens = AUDIENCE_TOKENS.get(group) if group else None
    if tokens is None:
        return None, {}
    all_tokens = (*_ALWAYS_PASS_TOKENS, *tokens)
    clauses: list[str] = []
    bind: dict[str, Any] = {}
    for i, tok in enumerate(all_tokens):
        name = f"{param_prefix}_{i}"
        clauses.append(f"{column} LIKE :{name}")
        bind[name] = f"%{tok}%"
    return "(" + " OR ".join(clauses) + ")", bind
