"""Router Agent — history 주입 분기 검증.

LLM 호출은 mock하고, 프롬프트에 컨텍스트가 들어갔는지 검증한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.router_agent import SEOUL_DISTRICTS, RouterAgent, _IntentOutput
from schemas.state import IntentType


def _make_agent(intent: IntentType) -> RouterAgent:
    """지정된 intent를 반환하는 Mock 체인이 주입된 RouterAgent."""
    agent = RouterAgent.__new__(RouterAgent)
    mock_llm = MagicMock()
    mock_structured = MagicMock()
    mock_structured.ainvoke = AsyncMock(return_value=_IntentOutput(intent=intent))
    mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
    agent._llm = mock_llm
    return agent


def _prompt_text(agent: RouterAgent) -> str:
    """ainvoke에 전달된 메시지 시퀀스를 단일 문자열로 합친다."""
    structured = agent._llm.with_structured_output.return_value
    messages = structured.ainvoke.call_args.args[0]
    return "\n".join(
        m.content if hasattr(m, "content") else str(m) for m in messages
    )


class TestRouterContextInjection:
    async def test_history_in_prompt(self):
        """history가 있으면 system prompt에 컨텍스트 섹션이 포함된다."""
        agent = _make_agent(IntentType.VECTOR_SEARCH)

        await agent.classify(
            message="성동구는?",
            history=[
                {"role": "user", "content": "테니스장 보여줘"},
                {"role": "assistant", "content": "강남구 테니스장 5건입니다."},
            ],
        )

        prompt_text = _prompt_text(agent)
        assert "이전 대화 이력" in prompt_text
        assert "[사용자] 테니스장 보여줘" in prompt_text
        assert "[어시스턴트] 강남구 테니스장 5건입니다." in prompt_text

    async def test_empty_history_omits_section(self):
        """history가 비어있으면 컨텍스트 섹션이 출력되지 않는다."""
        agent = _make_agent(IntentType.FALLBACK)

        await agent.classify(message="안녕", history=[])

        assert "이전 대화 이력" not in _prompt_text(agent)

    async def test_none_history_omits_section(self):
        """history가 None(기본값)이면 컨텍스트 섹션이 출력되지 않는다."""
        agent = _make_agent(IntentType.FALLBACK)

        await agent.classify(message="안녕")

        assert "이전 대화 이력" not in _prompt_text(agent)

    async def test_build_context_block_empty(self):
        """_build_context_block: 빈 입력은 빈 문자열을 반환한다."""
        agent = _make_agent(IntentType.FALLBACK)
        assert agent._build_context_block(None) == ""
        assert agent._build_context_block([]) == ""

    async def test_build_context_block_lists_turns(self):
        """_build_context_block: USER/ASSISTANT 턴을 라벨링하여 포함한다."""
        agent = _make_agent(IntentType.FALLBACK)
        block = agent._build_context_block(
            [
                {"role": "user", "content": "강남구 수영장"},
                {"role": "assistant", "content": "3건입니다."},
            ]
        )
        assert "이전 대화 이력" in block
        assert "- [사용자] 강남구 수영장" in block
        assert "- [어시스턴트] 3건입니다." in block

    async def test_orphan_user_turn_included(self):
        """ASSISTANT 응답 없는 USER 단독 턴도 방어적으로 포함된다."""
        agent = _make_agent(IntentType.FALLBACK)
        block = agent._build_context_block(
            [{"role": "user", "content": "마포구 풋살장"}]
        )
        assert "- [사용자] 마포구 풋살장" in block

    async def test_refined_query_returned_when_present(self):
        """LLM이 refined_query를 채워 반환하면 classify 결과에도 포함된다."""
        agent = RouterAgent.__new__(RouterAgent)
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.VECTOR_SEARCH,
                refined_query="성동구 테니스장",
            )
        )
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
        agent._llm = mock_llm

        result = await agent.classify(
            message="성동구는?",
            history=[{"role": "user", "content": "테니스장 보여줘"}],
        )

        assert result.intent == IntentType.VECTOR_SEARCH
        assert result.refined_query == "성동구 테니스장"

    async def test_refined_query_defaults_to_none_for_fallback(self):
        """FALLBACK 의도에서는 refined_query가 None으로 유지된다."""
        agent = _make_agent(IntentType.FALLBACK)  # default refined_query=None

        result = await agent.classify(message="안녕")

        assert result.intent == IntentType.FALLBACK
        assert result.refined_query is None

    async def test_metadata_postfilter_returned_when_extracted(self):
        """Router가 post-filter 메타데이터(max_class_name, area_name, service_status)를 함께 산출한다."""
        agent = RouterAgent.__new__(RouterAgent)
        mock_llm = MagicMock()
        mock_structured = MagicMock()
        mock_structured.ainvoke = AsyncMock(
            return_value=_IntentOutput(
                intent=IntentType.SQL_SEARCH,
                refined_query="강남구 체육시설 접수중",
                max_class_name="체육시설",
                area_name="강남구",
                service_status="접수중",
            )
        )
        mock_llm.with_structured_output = MagicMock(return_value=mock_structured)
        agent._llm = mock_llm

        result = await agent.classify("강남구 지금 접수 중인 체육시설")

        assert result.max_class_name == "체육시설"
        assert result.area_name == "강남구"
        assert result.service_status == "접수중"

    async def test_metadata_postfilter_defaults_to_none(self):
        """Router가 추출하지 못하면 메타데이터는 None으로 유지된다."""
        agent = _make_agent(IntentType.VECTOR_SEARCH)

        result = await agent.classify("아이랑 체험할 수 있는 시설")

        assert result.max_class_name is None
        assert result.area_name is None
        assert result.service_status is None

    async def test_invalid_max_class_name_normalized_to_none(self):
        """허용되지 않은 max_class_name 값은 field_validator로 None이 된다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, max_class_name="자유텍스트")
        assert rq.max_class_name is None

    async def test_invalid_service_status_normalized_to_none(self):
        """허용되지 않은 service_status 값은 field_validator로 None이 된다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, service_status="신청가능")
        assert rq.service_status is None

    async def test_valid_max_class_name_preserved(self):
        """허용된 max_class_name 값은 그대로 유지된다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, max_class_name="진료복지")
        assert rq.max_class_name == "진료복지"

    async def test_invalid_area_name_with_space_normalized_to_none(self):
        """공백이 포함된 자치구명("강 남구")은 None으로 정규화된다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, area_name="강 남구")
        assert rq.area_name is None

    async def test_invalid_area_name_english_normalized_to_none(self):
        """영문 자치구명("Gangnam")은 None으로 정규화된다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, area_name="Gangnam")
        assert rq.area_name is None

    async def test_invalid_area_name_without_gu_normalized_to_none(self):
        """ "구" 접미사 없는 축약형("강남")은 None으로 정규화된다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, area_name="강남")
        assert rq.area_name is None

    async def test_valid_area_name_preserved(self):
        """허용된 자치구명("강남구")은 그대로 유지된다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, area_name="강남구")
        assert rq.area_name == "강남구"

    async def test_none_area_name_preserved(self):
        """area_name=None은 None으로 유지된다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, area_name=None)
        assert rq.area_name is None

    @pytest.mark.parametrize("district", sorted(SEOUL_DISTRICTS))
    async def test_all_25_districts_pass_validator(self, district: str):
        """서울 25개 자치구 전체가 field_validator를 통과한다."""
        rq = _IntentOutput(intent=IntentType.SQL_SEARCH, area_name=district)
        assert rq.area_name == district

    async def test_long_history_compose_without_error(self):
        """매우 긴 turn content가 들어가도 prompt 합성에 실패하지 않는다."""
        agent = _make_agent(IntentType.VECTOR_SEARCH)
        long_history = [
            {"role": "user", "content": "가" * 1000} for _ in range(5)
        ]
        await agent.classify(message="후속", history=long_history)

        prompt_text = _prompt_text(agent)
        assert "이전 대화 이력" in prompt_text
        assert prompt_text.count("- [사용자] " + "가" * 1000) == 5
