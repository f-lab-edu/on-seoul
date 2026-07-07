"""카드 정규화/렌더 가드/그룹핑 + 큐레이션 (leaf 모듈).

검색 결과 row 를 카드 렌더링용으로 정규화하고(필드 추출 + fallback URL/렌더 가드),
place_name 기준 그룹핑·focal 우선 재정렬·의도 적합도 큐레이션을 수행한다.

이 모듈은 leaf 다 — agents.answer 내부 모듈(agent/prompting)을 import 하지 않는다.
표준 라이브러리(datetime, re)에만 의존한다.
"""

import datetime
import re

_FALLBACK_URL = "https://yeyak.seoul.go.kr"

# 카드 상세 표시 상한. 이 값 초과분의 건수(extra_count)만 숫자로 LLM에 전달된다.
# 클래스 밖 모듈 상수로 두어 인스턴스 오버라이드로 프롬프트와 불일치하는 사고를 방지한다.
_DISPLAY_LIMIT: int = 5

# 예약 가능 상태 우선순위 — 작을수록 상단(접수중 > 안내중 > 마감류). 화이트리스트
# (router_agent._ALLOWED_SERVICE_STATUSES)의 정규값 기준. 미상/그 외는 마감과 동급으로
# 후순위 처리한다(과강등 방지 위해 정규값만 별도 가점).
_STATUS_RANK: dict[str, int] = {
    "접수중": 0,
    "안내중": 1,
    "예약일시중지": 2,
    "예약마감": 3,
    "접수종료": 3,
}
_STATUS_RANK_DEFAULT = 3

# 큐레이션 적합도 비교 대상 의도 제약 키(화이트리스트 정규값으로 비교한다).
_CURATE_INTENDED_KEYS: tuple[str, ...] = ("area_name", "max_class_name", "payment_type")


def _payment_matches(row_value: object, intended: str) -> bool:
    """큐레이션 payment_type 매칭을 sql_search 의미와 정렬한다(단일 출처).

    sql_search 는 "유료" 요청을 LIKE '유료%' 접두 매칭으로 두 유료 변형
    ("유료","유료(요금안내문의)")을 모두 포섭하고, "무료" 는 정확 일치한다
    (tools/sql_search.py:108-115). 큐레이션 row 의 payment_type 은 정규화되지 않은
    원천 DB 값이므로(_normalize_card_row 가 그대로 통과), 동일 규칙으로 비교해야
    정당한 유료 결과를 "대안"으로 오라벨하지 않는다.

    Args:
        row_value: row 의 원천 payment_type(정규화 전 값일 수 있음).
        intended: 사용자가 요청한 정규값("유료"/"무료"). None 은 호출 전 처리.
    """
    if intended == "유료":
        return str(row_value or "").startswith("유료")
    return row_value == intended


def _curate_score(row: dict, intended: dict[str, str | None]) -> tuple[int, ...]:
    """단일 결과의 적합도 정렬 키(작을수록 상단)를 산출한다(결정적, 무비용).

    5.2 우선순위 — area_name > max_class_name > payment_type(무료 요청 시 무료 우선)
    > 예약 가능 상태(접수중 > 안내중 > 마감류). 각 차원은 만족=0/불만족=1 로 점수화해
    사전식(lexicographic) 비교한다. 마지막 차원은 _STATUS_RANK 정수다.

    화이트리스트 정규값(area_name/max_class_name/payment_type)으로만 비교해 표기 변형에
    의한 과강등(R-1)을 피한다. intended 에 없는 차원은 만족(0)으로 둬 영향 없음.
    """
    score: list[int] = []
    for key in _CURATE_INTENDED_KEYS:
        want = intended.get(key)
        if want is None:
            score.append(0)
            continue
        if key == "payment_type":
            score.append(0 if _payment_matches(row.get(key), want) else 1)
        else:
            score.append(0 if _dim_matches(row.get(key), want) else 1)
    score.append(_STATUS_RANK.get(row.get("service_status"), _STATUS_RANK_DEFAULT))
    return tuple(score)


def _is_exact_match(row: dict, intended: dict[str, str | None]) -> bool:
    """결과가 의도 제약(area/category/payment)을 모두 만족하면 True(딱맞음).

    상태(service_status)는 딱맞음 판정에 넣지 않는다 — 마감 항목도 의도 제약을
    만족하면 "딱 맞는" 시설이고, 상태는 정렬-강등으로만 다룬다.
    """
    for key in _CURATE_INTENDED_KEYS:
        want = intended.get(key)
        if want is None:
            continue
        if key == "payment_type":
            if not _payment_matches(row.get(key), want):
                return False
        elif not _dim_matches(row.get(key), want):
            return False
    return True


def _dim_matches(row_value: object, want: object) -> bool:
    """area_name/max_class_name 적합도 비교 — want 가 리스트면 멤버십, 아니면 동등.

    area_name 은 다중 지역 리스트(["성동구","광진구"])가 될 수 있으므로, 리스트일 때는
    행 값이 그 안에 포함되는지로 만족을 판정한다(list==str 오비교 방지).
    """
    if isinstance(want, (list, tuple)):
        return row_value in want
    return row_value == want


def _curate_display(
    all_results: list[dict],
    intended: dict[str, str | None],
    *,
    relaxed: bool,
    relaxed_filters: list[str] | None,
) -> tuple[list[dict], int]:
    """카드형 결과를 의도 적합도로 결정적 정렬한다. LLM/DB/추가검색 없음.

    하드 제외 없이 항상 적합도 정렬 후 전체를 반환한다: 마감 항목도
    제외하지 않고 강등만, 부족분은 이미 가져온 결과(top_k)로 채워진다. 호출자가
    `[:_DISPLAY_LIMIT]` 슬라이스를 취한다.

    동점(동일 적합도)은 Python sort 가 stable 하므로 입력 순서(RRF/_focal_first 또는
    SQL 순서)를 보존한다.

    Args:
        all_results: 정규화된 결과 목록(area_name/max_class_name/payment_type/
            service_status 키 접근).
        intended: 사용자가 원래 요청한 제약(완화로 드롭된 값 복원 포함). 호출자가
            applied(state["filters"]) ∪ relaxed_values 로 조립해 전달한다.
        relaxed: 완화 재시도 결과인지(현재 시그니처 보존용 — 정렬은 비완화/완화 단일
            규칙이라 동작에 쓰지 않으나, 호출 계약/향후 분기 여지를 위해 받는다).
        relaxed_filters: 완화로 드롭된 필터 키(동일 — 시그니처 보존).

    Returns:
        (curated, alt_count):
          - curated: 적합도 내림차순 정렬된 전체 결과.
          - alt_count: 상위 _DISPLAY_LIMIT 건 중 의도 제약을 모두 만족하지 *못한*
            ("대안") 항목 수. >0 이면 answer 가 "딱맞음/대안" 라벨 절을 추가한다.
    """
    del relaxed, relaxed_filters  # 단일 규칙 — 현재 정렬 동작에는 미사용(계약 보존).
    curated = sorted(all_results, key=lambda r: _curate_score(r, intended))
    top = curated[:_DISPLAY_LIMIT]
    alt_count = sum(1 for r in top if not _is_exact_match(r, intended))
    return curated, alt_count


def _group_by_place_name(
    rows: list[dict],
) -> tuple[dict | None, list[dict]]:
    """결과를 place_name 기준으로 그룹핑한다 (입력 순서 = RRF 랭킹 순서).

    focal 그룹 = 첫(RRF 최상위) 결과의 place_name 에 속한 모든 행. 결과는 이미
    랭킹순으로 들어오므로 첫 결과의 place_name 을 사용자가 지목한 핵심 시설로 본다.
    나머지 place_name 들은 등장 순서를 보존한 보조 그룹 목록으로 반환한다.

    Args:
        rows: 정규화 이전/이후 결과 목록 (place_name 키 접근, RRF 랭킹순).

    Returns:
        (focal, others):
          - focal: {"place_name": <focal place_name>, "rows": [<focal 행들>]} 또는
            rows 가 비면 None.
          - others: focal 외 place_name 그룹 리스트. 각 항목 동일 구조.
    """
    if not rows:
        return None, []

    groups: dict[object, dict] = {}
    order: list[object] = []
    for row in rows:
        key = row.get("place_name")
        if key not in groups:
            groups[key] = {"place_name": key, "rows": []}
            order.append(key)
        groups[key]["rows"].append(row)

    focal_key = order[0]
    focal = groups[focal_key]
    others = [groups[k] for k in order[1:]]
    return focal, others


def _focal_first(rows: list[dict]) -> list[dict]:
    """focal place_name 의 행들을 앞으로 끌어올려 재정렬한다.

    focal 공간이 _DISPLAY_LIMIT 슬라이스에서 무관 시설에 밀려 잘리지 않도록,
    focal 그룹을 맨 앞에 두고 나머지는 원래(RRF) 순서를 보존한다.
    """
    focal, others = _group_by_place_name(rows)
    if focal is None:
        return rows
    ordered = list(focal["rows"])
    for group in others:
        ordered.extend(group["rows"])
    return ordered


def _iso_or_none(value):
    """datetime/date 값을 ISO 8601 문자열로 변환한다.

    프론트 계약은 receipt_*_dt 가
    "2025-11-01T00:00:00" 형태 ISO 8601 로 직렬화되기를 요구한다.
    sse_frame 의 json.dumps(default=str) 폴백은 str(datetime) → 공백 구분자
    ("2025-11-01 00:00:00") 를 내므로, _normalize 단에서 명시적으로 isoformat()
    하여 'T' 구분자를 보장한다. (default=str 은 다른 타입 방어용으로 유지)

    이미 str 이거나 None 이면 그대로 통과한다.
    """
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


# tel_no 표시 가드. phone-shape(숫자/괄호/+/-/,/./슬래시/공백)만 허용한다.
# 실DB 검증: present 2206건 중 87.1% phone-shape, 11.3%(250건)에 한글 포함
# (담당자명·부가설명 등 garbage 혼재). 한글 등 비-phone 문자가 하나라도 섞이면
# 통째로 omit 한다. 복수번호("02-…, 0232")는 구분자가 허용 집합이라 정상 통과.
_TEL_PHONE_RE = re.compile(r"^[0-9()+\-,./\s]+$")


def _guarded_tel_no(value):
    """phone-shape 가 아니면 None 으로 omit, 맞으면 원본 유지."""
    if value is None:
        return None
    return value if _TEL_PHONE_RE.match(str(value)) else None


def _parse_time(value) -> datetime.time | None:
    """비교용으로 use_time 값을 datetime.time 으로 파싱한다.

    DB async 드라이버는 datetime.time 을, 일부 경로/테스트는 isoformat str
    ("09:00:00")을 줄 수 있으므로 둘 다 처리한다. 파싱 불가는 None.
    """
    if value is None:
        return None
    if isinstance(value, datetime.time):
        return value
    try:
        return datetime.time.fromisoformat(str(value))
    except ValueError:
        return None


def _guarded_use_time(start, end):
    """use_time 렌더 가드 — 오염값을 (None, None) 으로 omit 한다.

    실DB 검증: 미입력을 00:00-00:00 으로 인코딩한 placeholder(399건)와, 24h
    정규화 부작용(08:00-00:00 = 원래 08:00-24:00 이 24:00→00:00 으로 망가진
    artifact)이 섞여 있다. start>=end 위반율은 진료복지 40.9%, 교육강좌 30.4%,
    공간시설 29.3% 등으로, 이 위반의 정체가 곧 위 두 오염이다. 도메인상 자정넘김
    운영창(예: 22:00-02:00)은 전무(Q4b)하므로 start>=end 를 전부 오염으로 보고
    둘 다 omit 해도 정상값 오삭제 위험이 없다. 한쪽이라도 None 이면 함께 omit.

    raw 값으로 비교한 뒤, 살아남은 경우에만 호출부에서 _iso_or_none 직렬화한다.
    """
    ts, te = _parse_time(start), _parse_time(end)
    if ts is None or te is None or ts >= te:
        return None, None
    return start, end


def _normalize_card_row(row: dict) -> dict:
    """카드 렌더링용 필드 추출 + fallback URL/렌더 가드 적용(모듈 레벨 단일 출처).

    AnswerAgent._normalize 와 pre_answer_gate 큐레이션이 동일 정규화를 공유하도록
    클래스 밖으로 끌어냈다. 동작/필드 카탈로그는 AnswerAgent._normalize docstring 참조.
    """
    # service_url 스킴 가드: http(s):// 로 시작하지 않으면 fallback URL 로 강등.
    url = row.get("service_url")
    if not url or not str(url).startswith(("http://", "https://")):
        url = _FALLBACK_URL

    use_start, use_end = _guarded_use_time(
        row.get("use_time_start"), row.get("use_time_end")
    )

    return {
        "service_id": row.get("service_id"),
        "service_name": row.get("service_name"),
        "area_name": row.get("area_name"),
        "place_name": row.get("place_name"),
        "max_class_name": row.get("max_class_name"),
        "min_class_name": row.get("min_class_name"),
        "service_status": row.get("service_status"),
        "payment_type": row.get("payment_type"),
        "target_info": row.get("target_info"),
        "receipt_start_dt": _iso_or_none(row.get("receipt_start_dt")),
        "receipt_end_dt": _iso_or_none(row.get("receipt_end_dt")),
        "use_time_start": _iso_or_none(use_start),
        "use_time_end": _iso_or_none(use_end),
        "cancel_std_type": row.get("cancel_std_type"),
        "cancel_std_days": row.get("cancel_std_days"),
        "tel_no": _guarded_tel_no(row.get("tel_no")),
        "service_url": url,
    }
