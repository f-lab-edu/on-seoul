"""Router 의도 분류 경계 케이스 봉인 테스트.

SQL_SEARCH(목록 열거) vs ANALYTICS(집계·분포·종류 파악) 경계를 Mock LLM으로 검증한다.
실제 LLM 호출 없음. 각 케이스에 경계 의도를 docstring으로 명시한다.
"""

from schemas.state import IntentType
from tests.test_router_agent import _make_agent as _make_agent_with_intent


class TestRouterClassificationBoundary:
    async def test_list_query_is_sql_search_not_analytics(self):
        """경계: "알려줘" 형태는 목록 열거 → SQL_SEARCH, ANALYTICS가 아님.

        "마포구 테니스장 알려줘"는 개별 시설 목록을 요청하는 것이지
        분포/집계를 원하는 것이 아니므로 SQL_SEARCH이어야 한다.
        """
        agent = _make_agent_with_intent(IntentType.SQL_SEARCH)

        result = await agent.classify("마포구 테니스장 알려줘")

        assert result.intent == IntentType.SQL_SEARCH
        assert result.intent != IntentType.ANALYTICS

    async def test_distribution_query_is_analytics(self):
        """경계: "어디에 많아" 형태는 분포 집계 → ANALYTICS.

        "테니스장 어디 자치구에 많아?"는 자치구별 분포를 원하는 것이므로
        개별 목록을 반환하는 SQL_SEARCH가 아닌 ANALYTICS이어야 한다.
        """
        agent = _make_agent_with_intent(IntentType.ANALYTICS)

        result = await agent.classify("테니스장 어디 자치구에 많아?")

        assert result.intent == IntentType.ANALYTICS
        assert result.intent != IntentType.SQL_SEARCH

    async def test_show_me_query_is_sql_search_not_analytics(self):
        """경계: "보여줘" 형태는 목록 열거 → SQL_SEARCH, ANALYTICS가 아님.

        "접수 중인 수영장 보여줘"는 조건에 맞는 시설 목록을 원하는 것이므로
        ANALYTICS가 아닌 SQL_SEARCH이어야 한다.
        """
        agent = _make_agent_with_intent(IntentType.SQL_SEARCH)

        result = await agent.classify("접수 중인 수영장 보여줘")

        assert result.intent == IntentType.SQL_SEARCH
        assert result.intent != IntentType.ANALYTICS

    async def test_count_by_category_is_analytics(self):
        """경계: "카테고리별 개수" 형태는 집계 → ANALYTICS.

        "카테고리별 공공서비스 개수 알려줘"는 카테고리별 통계 집계가 목적이므로
        개별 목록을 반환하는 SQL_SEARCH가 아닌 ANALYTICS이어야 한다.
        """
        agent = _make_agent_with_intent(IntentType.ANALYTICS)

        result = await agent.classify("카테고리별 공공서비스 개수 알려줘")

        assert result.intent == IntentType.ANALYTICS
        assert result.intent != IntentType.SQL_SEARCH

    async def test_what_types_exist_is_analytics(self):
        """경계: "어떤 유형이 있어" 형태는 종류 파악 → ANALYTICS.

        "강남구 체육시설 어떤 유형이 있어?"는 세부 유형 분류 파악이 목적이므로
        개별 시설을 열거하는 SQL_SEARCH가 아닌 ANALYTICS이어야 한다.
        """
        agent = _make_agent_with_intent(IntentType.ANALYTICS)

        result = await agent.classify("강남구 체육시설 어떤 유형이 있어?")

        assert result.intent == IntentType.ANALYTICS
        assert result.intent != IntentType.SQL_SEARCH

    async def test_can_apply_now_query_is_sql_search(self):
        """경계: "신청할 수 있는" 형태는 접수중 조건 목록 → SQL_SEARCH.

        "지금 신청할 수 있는 교육 강좌"는 접수중 상태인 강좌 목록을 요청하는 것이므로
        집계/분포를 원하는 ANALYTICS가 아닌 SQL_SEARCH이어야 한다.
        """
        agent = _make_agent_with_intent(IntentType.SQL_SEARCH)

        result = await agent.classify("지금 신청할 수 있는 교육 강좌")

        assert result.intent == IntentType.SQL_SEARCH
        assert result.intent != IntentType.ANALYTICS
