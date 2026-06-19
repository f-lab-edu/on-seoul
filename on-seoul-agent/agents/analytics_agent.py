"""Analytics Agent — 집계/분포 질의 파라미터 추출 + tools.analytics_search 위임.

LLM 으로 사용자 메시지에서 **집계 차원(group_by) + metric + keyword** 만 구조화 추출한 뒤,
tools.analytics_search 를 통해 on_data_reader 세션으로 GROUP BY COUNT / DISTINCT 를 수행한다.

LLM 이 SQL 도 group_by 컬럼명도 자유 문자열로 생성하지 않는다.
group_by 는 _AnalyticsParams 의 Literal + field_validator 로 화이트리스트
(_DIMENSION_COLUMNS 키)에 강제되므로 analytics_search 의 f-string 인젝션 위험이 없다.

필터(max_class_name/area_name/service_status)는 router 산출 state 값을 재사용한다.
"""

from typing import Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate, FewShotChatMessagePromptTemplate
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from llm.client import get_chat_model
from llm.prompts.analytics_extraction import (
    ANALYTICS_EXTRACTION_FEW_SHOT_EXAMPLES,
    ANALYTICS_EXTRACTION_HUMAN,
    ANALYTICS_EXTRACTION_SYSTEM,
)
from schemas.state import AgentState
from tools.analytics_search import TOP_K as _TOP_K
from tools.analytics_search import analytics_search

# 안전 기본값 — LLM 이 화이트리스트를 벗어난 값을 반환하면 여기로 정규화한다.
_DEFAULT_GROUP_BY = "max_class_name"
_DEFAULT_METRIC = "count"

_ALLOWED_GROUP_BY: frozenset[str] = frozenset(
    ["area_name", "max_class_name", "min_class_name", "service_status"]
)
_ALLOWED_METRIC: frozenset[str] = frozenset(["count", "distinct"])


class _AnalyticsParams(BaseModel):
    """LLM 이 추출하는 집계 파라미터.

    group_by/metric 은 화이트리스트 Literal 이며, validator 가 허용 외 값을
    안전 기본값으로 정규화한다 (LLM 이 임의 컬럼명을 흘려도 analytics_search 의
    KeyError 로 새지 않도록 강제).
    """

    group_by: Literal[
        "area_name", "max_class_name", "min_class_name", "service_status"
    ] = _DEFAULT_GROUP_BY
    metric: Literal["count", "distinct"] = _DEFAULT_METRIC
    keyword: str | None = None

    @field_validator("group_by", mode="before")
    @classmethod
    def _validate_group_by(cls, v: object) -> str:
        return v if v in _ALLOWED_GROUP_BY else _DEFAULT_GROUP_BY  # type: ignore[return-value]

    @field_validator("metric", mode="before")
    @classmethod
    def _validate_metric(cls, v: object) -> str:
        return v if v in _ALLOWED_METRIC else _DEFAULT_METRIC  # type: ignore[return-value]


class AnalyticsAgent:
    """LLM 파라미터 추출 + tools.analytics_search 위임 에이전트.

    세션은 호출자(워크플로우 또는 테스트)가 주입한다.
    집계 실행 로직은 tools/analytics_search.py 에 위임한다.
    """

    def __init__(self, model: BaseChatModel | None = None) -> None:
        # temperature=0: 차원·metric 분류는 결정론적 추출 — 창의성 불필요.
        llm = model or get_chat_model(temperature=0)

        few_shot = FewShotChatMessagePromptTemplate(
            example_prompt=ChatPromptTemplate.from_messages(
                [
                    ("human", "사용자 메시지: {message}"),
                    ("ai", "{output}"),
                ]
            ),
            examples=ANALYTICS_EXTRACTION_FEW_SHOT_EXAMPLES,
        )
        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", ANALYTICS_EXTRACTION_SYSTEM),
                few_shot,
                ("human", ANALYTICS_EXTRACTION_HUMAN),
            ]
        )
        self._chain = prompt | llm.with_structured_output(_AnalyticsParams)

    async def run(
        self,
        state: AgentState,
        session: AsyncSession,
        *,
        top_k: int | None = None,
    ) -> AgentState:
        """메시지에서 집계 파라미터 추출 후 집계 조회. analytics_* 슬롯을 채운 AgentState 반환.

        router 가 산출한 max_class_name/area_name/service_status 는 그대로 재사용한다.

        정합성 가드:
            group_by 가 min_class_name 인데 max_class_name 필터가 없으면 소분류 단독
            그룹핑은 카디널리티가 과도하므로 group_by 를 max_class_name 으로 폴백한다.
        """
        plan = state.get("plan") or {}
        filters = state.get("filters") or {}
        input_message = plan.get("refined_query") or state["message"]
        params: _AnalyticsParams = await self._chain.ainvoke(
            {"message": input_message}
        )

        max_class_name = filters.get("max_class_name")
        area_name = filters.get("area_name")
        service_status = filters.get("service_status")

        # 정합성 가드: 소분류 그룹핑은 대분류 필터가 있을 때만 허용.
        group_by = params.group_by
        if group_by == "min_class_name" and not max_class_name:
            # 필터 부재 시 차원만 강등(max_class_name 필터는 여전히 None이므로
            # 대분류 전체를 max 차원으로 그룹핑).
            group_by = "max_class_name"

        rows = await analytics_search(
            session,
            group_by=group_by,
            metric=params.metric,
            max_class_name=max_class_name,
            area_name=area_name,
            service_status=service_status,
            keyword=params.keyword,
            top_k=top_k if top_k is not None else _TOP_K,
        )
        return {
            **state,
            "analytics": {
                "results": rows,
                "group_by": group_by,
                "metric": params.metric,
                "keyword": params.keyword,
            },
        }
