"""Router Agent — recent_queries 주입 분기 검증.

LLM 호출은 mock하고, 프롬프트에 컨텍스트가 들어갔는지 검증한다.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from agents.router_agent import SEOUL_DISTRICTS, RouterAgent, _IntentOutput
from core.config import settings
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


class TestRouterContextInjection:
    async def test_recent_queries_in_prompt(self):
        """recent_queries가 있으면 system prompt에 컨텍스트 섹션이 포함된다."""
        agent = _make_agent(IntentType.VECTOR_SEARCH)

        await agent.classify(
            message="성동구는?",
            recent_queries=["테니스장 보여줘"],
        )

        # ainvoke에 전달된 메시지 시퀀스를 추출
        structured = agent._llm.with_structured_output.return_value
        call_args = structured.ainvoke.call_args
        messages = call_args.args[0]
        prompt_text = "\n".join(
            m.content if hasattr(m, "content") else str(m) for m in messages
        )

        assert "이전 사용자 발화" in prompt_text
        assert "테니스장 보여줘" in prompt_text

    async def test_empty_recent_queries_omits_section(self):
        """recent_queries가 비어있으면 컨텍스트 섹션이 출력되지 않는다."""
        agent = _make_agent(IntentType.FALLBACK)

        await agent.classify(message="안녕", recent_queries=[])

        structured = agent._llm.with_structured_output.return_value
        call_args = structured.ainvoke.call_args
        messages = call_args.args[0]
        prompt_text = "\n".join(
            m.content if hasattr(m, "content") else str(m) for m in messages
        )

        assert "이전 사용자 발화" not in prompt_text

    async def test_none_recent_queries_omits_section(self):
        """recent_queries가 None(기본값)이면 컨텍스트 섹션이 출력되지 않는다."""
        agent = _make_agent(IntentType.FALLBACK)

        await agent.classify(message="안녕")

        structured = agent._llm.with_structured_output.return_value
        call_args = structured.ainvoke.call_args
        messages = call_args.args[0]
        prompt_text = "\n".join(
            m.content if hasattr(m, "content") else str(m) for m in messages
        )

        assert "이전 사용자 발화" not in prompt_text

    async def test_build_context_block_empty(self):
        """_build_context_block: 빈 입력은 빈 문자열을 반환한다."""
        agent = _make_agent(IntentType.FALLBACK)
        assert agent._build_context_block(None) == ""
        assert agent._build_context_block([]) == ""

    async def test_build_context_block_lists_queries(self):
        """_build_context_block: 입력된 질의를 bullet list로 포함한다."""
        agent = _make_agent(IntentType.FALLBACK)
        block = agent._build_context_block(["q1", "q2"])
        assert "이전 사용자 발화" in block
        assert "- q1" in block
        assert "- q2" in block

    async def test_build_context_block_truncates_to_settings_max(self, monkeypatch):
        """recent_queries는 settings.recent_queries_max 만큼만 사용된다.

        기본값(5) 가정 없이 monkeypatch로 명시 후 검증한다.
        core/recent_queries.py의 LPUSH/LTRIM/LRANGE와 동일 설정으로
        의도 통일을 보장한다.
        """
        monkeypatch.setattr(settings, "recent_queries_max", 5)
        agent = _make_agent(IntentType.FALLBACK)
        block = agent._build_context_block([f"q{i}" for i in range(7)])
        # q0..q4는 포함, q5/q6은 제외
        for i in range(5):
            assert f"- q{i}" in block
        assert "- q5" not in block
        assert "- q6" not in block

    async def test_build_context_block_respects_changed_max(self, monkeypatch):
        """settings.recent_queries_max를 3으로 바꾸면 7개 입력에서 3개만 포함된다.

        운영자가 max를 변경해도 Router 주입 개수가 일관되게 따라가야 한다 (회귀).
        """
        monkeypatch.setattr(settings, "recent_queries_max", 3)
        agent = _make_agent(IntentType.FALLBACK)
        block = agent._build_context_block([f"q{i}" for i in range(7)])
        for i in range(3):
            assert f"- q{i}" in block
        for i in range(3, 7):
            assert f"- q{i}" not in block

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
            recent_queries=["테니스장 보여줘"],
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

    async def test_long_queries_compose_without_error(self, monkeypatch):
        """매우 긴 query 5개가 들어가도 prompt 합성에 실패하지 않는다.

        recent_queries_max 기본값(5)에 의존하므로 monkeypatch로 명시한다.
        """
        monkeypatch.setattr(settings, "recent_queries_max", 5)
        agent = _make_agent(IntentType.VECTOR_SEARCH)
        long_queries = ["가" * 500 for _ in range(5)]
        await agent.classify(message="후속", recent_queries=long_queries)

        structured = agent._llm.with_structured_output.return_value
        messages = structured.ainvoke.call_args.args[0]
        prompt_text = "\n".join(
            m.content if hasattr(m, "content") else str(m) for m in messages
        )
        assert "이전 사용자 발화" in prompt_text
        # 모든 5개 항목이 포함됨
        assert prompt_text.count("- " + "가" * 500) == 5
