"""intake 인덱스 계약 — 순수 함수 (LLM 무관·결정적·단위테스트 대상).

reference_resolution 규칙(_DEMONSTRATIVES/서수/라벨 부분일치)을 대체한다. LLM 이
prev_entities 의 1-based 인덱스를 선택하면 여기서 범위 검증 후 service_id 로 매핑한다.
"열거된 목록에서 고르게 한다"는 패턴이라 잘못된 ID 바인딩이 구조적으로 차단된다.

인덱스 표현(중요): LLM 에 열거·선택받는 인덱스는 1-based 이다. 사용자가 "2번",
"첫 번째"처럼 말하는 순서 그대로라 질의 어휘와 인덱스의 격차를 좁혀 참조가 덜 어긋난다.
저장·배열 접근은 0-based 이며 idx-1 로 변환해 읽는다.

핵심 불변식:
  - 1 ≤ idx ≤ len(prev_entities)(1-based 계약) 검증 → prev_entities[idx-1](0-based 접근).
  - 범위 밖/빈 prev_entities → 빈 결과(ID 환각 0).
"""

import re
from typing import Any

# prev_entities 열거 상한(≤10).
_ENUM_CAP = 10

# 라벨 길이 상한(열거 행 비대화·토큰 폭주 방지). 초과분은 절단.
_LABEL_CAP = 120
# 임의 공백류(개행·탭 포함) → 단일 스페이스 평탄화 패턴.
_WS = re.compile(r"\s+")
# fence 마커류(---..._START---/---..._END---) 무력화 패턴 — 외부 입력이 fence 를 조기
# 탈출해 시스템 지시를 위장하지 못하게 한다(prev_reasoning fence 와 동등 수준).
# single source of truth: 라벨/history content 양쪽이 이 패턴 하나만 공유한다.
_FENCE = re.compile(r"-{2,}\s*\w+_(?:START|END)\s*-{2,}")


def neutralize_fence(text: str) -> str:
    """fence 마커 토큰(---..._START---/---..._END---)만 스페이스로 치환한다.

    외부 입력(라벨·history content)이 위조 fence 로 경계 블록을 조기 탈출해 시스템
    지시를 위장하는 것을 막는 공유 헬퍼. _FENCE 패턴이 `\\w+_(?:START|END)` 형태만
    매칭하므로 일반 대시·하이픈("지하철-2호선", "A--B")은 보존된다. 공백 평탄화·길이
    캡은 호출부 정책(라벨 vs content)이 달라 여기서 적용하지 않는다.
    """
    return _FENCE.sub(" ", text)


def _sanitize_label(label: str) -> str:
    """열거 라벨을 injection 안전한 단일 라인으로 정규화한다.

    label 은 Spring 중계 클라이언트 제어값(DB 재대조 없음)이라 조작 라벨이 turn_kind/
    action 분류를 흔들 수 있다. prev_reasoning fence 와 동등 수준으로 (1) fence 마커
    토큰 제거 → (2) 공백류(개행 포함) 평탄화 → (3) 길이 상한 절단한다. service_id
    위조는 인덱스 계약으로 이미 차단되므로 여기선 표시 텍스트만 무력화한다.
    """
    cleaned = neutralize_fence(label)
    cleaned = _WS.sub(" ", cleaned).strip()
    if len(cleaned) > _LABEL_CAP:
        cleaned = cleaned[:_LABEL_CAP].rstrip()
    return cleaned


def resolve_ref_indices(
    indices: list[int] | None,
    prev_entities: list[dict[str, Any]] | None,
) -> list[str]:
    """LLM 이 선택한 1-based 인덱스를 prev_entities 의 service_id 로 매핑한다.

    범위검증(1 ≤ idx ≤ N) 통과한 인덱스만 0-based 로 변환해 service_id 를 읽는다.
    등장 순서를 보존하되 중복 service_id 는 제거한다(첫 등장 유지).

    Args:
        indices: LLM 이 반환한 1-based 인덱스 리스트.
        prev_entities: 직전 턴 결과 엔티티 [{service_id, label}, ...].

    Returns:
        범위 내 인덱스의 service_id 리스트. 범위 밖/빈 입력은 빈 리스트.
    """
    entities = prev_entities or []
    if not entities or not indices:
        return []
    n = len(entities)
    out: list[str] = []
    seen: set[str] = set()
    for idx in indices:
        if not isinstance(idx, int) or idx < 1 or idx > n:
            continue
        sid = entities[idx - 1].get("service_id")  # 1-based 계약 → 0-based 배열 접근
        if sid and sid not in seen:
            seen.add(sid)
            out.append(sid)
    return out


def enumerate_entities(prev_entities: list[dict[str, Any]] | None) -> str:
    """prev_entities 를 표시 순서대로 1..N 열거한 텍스트로 변환(라벨 포함, ≤10).

    LLM 이 이 목록에서 1-based 인덱스를 선택하도록 제시한다. 비어 있으면 빈 문자열.
    """
    entities = prev_entities or []
    if not entities:
        return ""
    lines: list[str] = []
    for i, ent in enumerate(entities[:_ENUM_CAP], start=1):
        label = _sanitize_label(ent.get("label") or "")
        lines.append(f"{i}. {label}")
    return "\n".join(lines)
