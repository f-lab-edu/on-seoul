"""Answer Agent — 자연어 답변 + 시설 카드 가공.

AgentState의 검색 결과(sql_results / vector_results / map_results)를 종합해
사용자에게 전달할 최종 답변과 시설 카드 목록을 생성한다.

title_needed=True 인 경우(첫 메시지) 대화 제목도 함께 생성한다.
"""

import json

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel

from llm.client import get_chat_model
from schemas.state import AgentState

# ---------------------------------------------------------------------------
# 답변 생성 프롬프트
# ---------------------------------------------------------------------------

_ANSWER_SYSTEM = """\
당신은 서울시 공공서비스 예약 안내 챗봇입니다.
아래 검색 결과를 바탕으로 사용자 질문에 친절하고 간결하게 답변하세요.

규칙:
- 검색 결과가 없으면 "죄송합니다, 조건에 맞는 시설을 찾지 못했습니다."라고 답하세요.
- 시설 정보는 service_name, area_name, service_status, receipt_start_dt, receipt_end_dt를 활용하세요.
- 예약 링크(service_url)가 있으면 안내에 포함하세요. 없으면 서울시 공공서비스 예약 사이트(https://yeyak.seoul.go.kr)를 안내하세요.
- 마크다운 없이 자연스러운 한국어로 작성하세요.
"""

_ANSWER_HUMAN = """\
사용자 질문: {message}

검색 결과:
{results_json}
"""

# ---------------------------------------------------------------------------
# 제목 생성 프롬프트
# ---------------------------------------------------------------------------

_TITLE_SYSTEM = """\
사용자 질문을 보고 대화 제목을 10자 이내로 만드세요.
특수문자나 이모지 없이 명사형으로 끝내세요.
"""

_TITLE_HUMAN = "사용자 질문: {message}"

_FALLBACK_URL = "https://yeyak.seoul.go.kr"


class _AnswerOutput(BaseModel):
    answer: str


class _TitleOutput(BaseModel):
    title: str


class AnswerAgent:
    """검색 결과 → 자연어 답변 + 시설 카드 + (선택) 제목 생성 에이전트."""

    def __init__(self, model: BaseChatModel | None = None) -> None:
        llm = model or get_chat_model()

        answer_prompt = ChatPromptTemplate.from_messages([
            ("system", _ANSWER_SYSTEM),
            ("human", _ANSWER_HUMAN),
        ])
        self._answer_chain = answer_prompt | llm.with_structured_output(_AnswerOutput)

        title_prompt = ChatPromptTemplate.from_messages([
            ("system", _TITLE_SYSTEM),
            ("human", _TITLE_HUMAN),
        ])
        self._title_chain = title_prompt | llm.with_structured_output(_TitleOutput)

    async def answer(self, state: AgentState) -> AgentState:
        """검색 결과를 종합해 answer(+title)을 채운 AgentState를 반환한다."""
        results = self._collect_results(state)
        results_json = json.dumps(results, ensure_ascii=False, default=str)

        answer_out: _AnswerOutput = await self._answer_chain.ainvoke({
            "message": state["message"],
            "results_json": results_json,
        })

        updates: dict = {"answer": answer_out.answer}

        if state.get("title_needed"):
            title_out: _TitleOutput = await self._title_chain.ainvoke(
                {"message": state["message"]}
            )
            updates["title"] = title_out.title

        return {**state, **updates}

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------

    def _collect_results(self, state: AgentState) -> list[dict]:
        """sql_results / vector_results / map_results를 단일 목록으로 합친다."""
        raw: list[dict] = []

        if state.get("sql_results"):
            raw.extend(state["sql_results"])
        if state.get("vector_results"):
            raw.extend(state["vector_results"])
        if state.get("map_results"):
            # map_results는 GeoJSON dict — features 배열 언팩
            features = state["map_results"].get("features", [])
            raw.extend(f.get("properties", {}) for f in features)

        return [self._normalize(r) for r in raw]

    @staticmethod
    def _normalize(row: dict) -> dict:
        """카드 렌더링에 필요한 필드만 추출하고 fallback URL을 보정한다."""
        # vector_results의 metadata가 dict인 경우 언팩
        metadata = row.get("metadata") or {}
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}

        service_url = (
            row.get("service_url")
            or metadata.get("service_url")
            or _FALLBACK_URL
        )

        return {
            "service_id": row.get("service_id") or metadata.get("service_id"),
            "service_name": row.get("service_name") or metadata.get("service_name"),
            "area_name": row.get("area_name") or metadata.get("area_name"),
            "place_name": row.get("place_name") or metadata.get("place_name"),
            "service_status": row.get("service_status") or metadata.get("service_status"),
            "receipt_start_dt": row.get("receipt_start_dt") or metadata.get("receipt_start_dt"),
            "receipt_end_dt": row.get("receipt_end_dt") or metadata.get("receipt_end_dt"),
            "service_url": service_url,
        }
