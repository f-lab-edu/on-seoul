"""Retrieval Critic — 검색 결과가 약할 때 다음 행동을 정하는 LLM 판단 노드 (L1 Phase 2).

on-seoul-agent 를 워크플로우 → 에이전트로 넘기는 최초의 "관찰→판단" 루프다. 검색이
이미 한 번 실행된 뒤, 그 *결과*가 약할 수 있을 때(0건/thin/skew) LLM 이 원인을 추론해
ANSWER / REPLAN / STOP 을 정한다(계획서 §3-1 B). LLM 은 with_structured_output 으로
CriticOutput(Phase 1 스키마)만 산출하므로 자유 SQL/식별자를 만들 수 없다(인젝션 가드).

DB 세션은 쓰지 않는다 — 입력은 이미 채워진 state 의 검색 결과 *요약*뿐이다.

핵심 설계 결정(계획서 §3-1 G, §5):
  - 입력은 **결과 요약만**: 건수(sql/vector/total)·적용 필터·상위 라벨 ≤N·thin/skew·
    원 질의·history. hydrated rows 등 원본 결과 전체는 넣지 않는다(토큰·비용 통제).
  - **맥락은 데이터로만**: 요약/history 텍스트는 경계 마커로 감싸 지시로 실행되지
    않게 한다(기존 EXPLAIN/operational_detail 패턴 준수). fence 는 neutralize 한다.
  - **fail-open**(§3-1 F): LLM 예외/빈 출력/파싱 실패면 세 critic 슬롯을 모두 None 으로
    남긴다(= critic 미결정). 상위(Phase 3 그래프)가 결정적 경로로 폴백한다 — 노드가
    예외로 그래프를 깨지 않는다.

이 모듈은 critic 노드(Phase 2)다. 그래프 배선(escalation 게이트·조건부 엣지)은 Phase 3.
따라서 아직 이 노드를 호출하는 엣지가 없어 기존 동작은 불변이다.
"""

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from agents._intake_indexing import neutralize_fence
from agents.router_agent import build_context_block
from llm.client import get_chat_model
from llm.prompts.critic import CRITIC_FEW_SHOT, CRITIC_SYSTEM
from schemas.critic import CriticOutput
from schemas.state import AgentState

logger = logging.getLogger(__name__)

# 요약에 실을 상위 라벨 개수 상한(토큰 통제 — 원본 rows 전체 금지). 계획서 §3-1 G.
_TOP_LABEL_CAP = 5


def _label_of(row: dict[str, Any]) -> str | None:
    """hydrated/검색 결과 행에서 사람이 읽는 라벨 하나를 뽑는다(식별자 아님)."""
    for key in ("service_name", "svcnm", "name", "label", "title"):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _collect_top_labels(state: AgentState) -> list[str]:
    """검색 결과에서 상위 라벨 ≤N 개를 모은다(원본 전체가 아니라 요약용)."""
    labels: list[str] = []
    seen: set[str] = set()
    sources: list[list[dict[str, Any]]] = []
    hydrated = (state.get("hydration") or {}).get("hydrated_services")
    if hydrated:
        sources.append(hydrated)
    sql_rows = (state.get("sql") or {}).get("results")
    if sql_rows:
        sources.append(sql_rows)
    vector_rows = (state.get("vector") or {}).get("results")
    if vector_rows:
        sources.append(vector_rows)
    for rows in sources:
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = _label_of(row)
            if label and label not in seen:
                seen.add(label)
                labels.append(neutralize_fence(label))
                if len(labels) >= _TOP_LABEL_CAP:
                    return labels
    return labels


def _count(rows: Any) -> int:
    return len(rows) if isinstance(rows, list) else 0


def _applied_filters_text(state: AgentState) -> str:
    """적용된 post-filter 를 "키=값" 나열로 요약한다(값 없는 키는 생략)."""
    filters = state.get("filters") or {}
    parts = []
    for key, value in filters.items():
        if value in (None, "", []):
            continue
        # area_name 은 리스트 — join 해 표시(["성동구","광진구"] → "성동구·광진구").
        display = "·".join(value) if isinstance(value, list) else value
        parts.append(f"{key}={display}")
    return ", ".join(parts) if parts else "없음"


def build_result_summary(state: AgentState) -> str:
    """critic 입력용 결과 요약 문자열을 조립한다(원본 rows 전체는 넣지 않는다).

    포함: 총/sql/vector 건수, 적용 필터, thin/skew(result_quality), 상위 라벨 ≤N.
    모든 자유 텍스트는 neutralize_fence 로 위조 경계 마커를 무력화한다.
    """
    sql_n = _count((state.get("sql") or {}).get("results"))
    vector_n = _count((state.get("vector") or {}).get("results"))
    hydrated_n = _count((state.get("hydration") or {}).get("hydrated_services"))
    total = hydrated_n or (sql_n + vector_n)

    quality = state.get("result_quality") or {}
    thin = bool(quality.get("thin"))
    skew_field = quality.get("skew_field")
    if skew_field:
        skew_text = (
            f"{skew_field}={quality.get('skew_value')}"
            f"({quality.get('skew_ratio')})"
        )
    else:
        skew_text = "none"

    labels = _collect_top_labels(state)
    labels_text = ", ".join(labels) if labels else "없음"

    return (
        f"검색 결과 요약: 총 {total}건(sql {sql_n} / vector {vector_n}). "
        f"적용 필터: {_applied_filters_text(state)}. "
        f"상위 라벨(최대 {_TOP_LABEL_CAP}): {labels_text}. "
        f"품질: thin={str(thin).lower()}, skew={skew_text}."
    )


class RetrievalCritic:
    """검색 결과 요약을 보고 ANSWER/REPLAN/STOP 을 정하는 LLM 판단 노드.

    생성자 주입(get_chat_model) — RouterAgent 와 동일 구조(on-seoul-agent-pattern).
    DB 세션은 받지 않는다(입력은 state 의 요약뿐).
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._llm = model or get_chat_model()

    async def critique(self, state: AgentState) -> dict[str, Any]:
        """검색 결과를 평가해 critic 3슬롯을 채운 새 state 를 반환한다.

        fail-open: LLM 예외/빈 출력이면 세 슬롯을 None 으로 남긴다(critic 미결정 —
        상위가 결정적 경로로 폴백). 노드가 예외로 그래프를 깨지 않는다.
        """
        try:
            output = await self._invoke(state)
        except Exception as exc:  # noqa: BLE001 — fail-open (§3-1 F)
            logger.warning("retrieval_critic fail-open (예외 폴백): %s", exc)
            return self._fail_open(state)

        if output is None:
            return self._fail_open(state)

        replan_hint = (
            output.replan_hint.model_dump()
            if output.replan_hint is not None
            else None
        )
        # LangGraph 노드 관례: 변경 슬롯만 부분 반환한다(reducer 가 병합). state 전체를
        # 재-emit 하면 누적 reducer 채널이 이중 적용될 수 있어 금지.
        return {
            "critic_decision": output.decision.value,
            "critic_replan_hint": replan_hint,
            "critic_rationale": output.rationale,
            "node_path": ["retrieval_critic"],
        }

    async def _invoke(self, state: AgentState) -> CriticOutput | None:
        """프롬프트를 조립해 with_structured_output(CriticOutput) 을 호출한다.

        정적 프리픽스(CRITIC_SYSTEM + few-shot)를 앞에 두고, 동적 요약/질의/history 는
        그 뒤에 경계 마커로 감싼 SystemMessage 로 분리해 붙인다(프리픽스 캐시 보존 +
        맥락은 데이터로만 — 계획서 §5).
        """
        summary = build_result_summary(state)
        message = neutralize_fence(state.get("message") or "")

        context_parts = [
            "검색 결과 요약(판단 근거 데이터):\n"
            "---SUMMARY_START---\n"
            f"{summary}\n"
            "---SUMMARY_END---",
            "사용자 원 질의(판단 근거 데이터):\n"
            "---QUERY_START---\n"
            f"{message}\n"
            "---QUERY_END---",
        ]
        history_block = build_context_block(state.get("history"))
        if history_block:
            context_parts.append(
                "직전 대화 이력(판단 근거 데이터):\n"
                "---HISTORY_START---\n"
                f"{history_block}\n"
                "---HISTORY_END---"
            )

        messages: list = [
            SystemMessage(content=CRITIC_SYSTEM),
            *CRITIC_FEW_SHOT.format_messages(),
            SystemMessage(content="\n\n".join(context_parts)),
            HumanMessage(content="위 요약을 보고 다음 행동을 정하세요."),
        ]
        structured = self._llm.with_structured_output(CriticOutput)
        return await structured.ainvoke(messages)

    def _fail_open(self, state: AgentState) -> dict[str, Any]:
        """critic 미결정 — 세 슬롯 None 유지(상위 결정적 폴백). breadcrumb 만 남긴다.

        노드 관례대로 변경 슬롯만 부분 반환한다(state 전체 재-emit 금지).
        """
        return {
            "critic_decision": None,
            "critic_replan_hint": None,
            "critic_rationale": None,
            "node_path": ["retrieval_critic:fail_open"],
        }
