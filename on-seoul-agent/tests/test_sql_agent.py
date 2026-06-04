"""SqlAgent 단위 테스트.

LLM 파라미터 추출과 SQL 쿼리 로직을 Mock으로 분리하여 검증한다.
"""

from datetime import date
from unittest.mock import ANY, AsyncMock, MagicMock

from tests.helpers import make_agent_state
from agents.sql_agent import SqlAgent, _SqlParams
from schemas.state import AgentState, IntentType


def _make_state(message: str = "수영장 알려줘") -> AgentState:
    return make_agent_state(message=message, intent=IntentType.SQL_SEARCH)


def _make_agent(params: _SqlParams, db_rows: list[dict]) -> tuple[SqlAgent, MagicMock]:
    """지정 파라미터와 DB 결과를 반환하는 Mock Agent와 Mock Session."""
    agent = SqlAgent.__new__(SqlAgent)
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=params)
    agent._chain = mock_chain

    # Mock DB session
    mock_result = MagicMock()
    mock_result.keys.return_value = list(db_rows[0].keys()) if db_rows else []
    mock_result.fetchall.return_value = [tuple(r.values()) for r in db_rows]

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(return_value=mock_result)

    return agent, mock_session


class TestSqlAgent:
    async def test_search_populates_sql_results(self):
        """search는 DB 조회 결과를 sql_results에 채운다."""
        rows = [{"service_id": "S001", "service_name": "수영장", "area_name": "강남구"}]
        agent, session = _make_agent(_SqlParams(), rows)

        result = await agent.search(_make_state(), session)

        assert result["sql_results"] == rows

    async def test_search_preserves_state_fields(self):
        """search는 sql_results만 변경하고 나머지를 보존한다."""
        agent, session = _make_agent(_SqlParams(), [])
        state = _make_state("수영장")
        state["room_id"] = 7

        result = await agent.search(state, session)

        assert result["room_id"] == 7
        assert result["message"] == "수영장"

    async def test_chain_receives_message_and_today(self):
        """LLM 체인에 message와 today가 함께 전달된다."""
        agent, session = _make_agent(_SqlParams(), [])
        state = _make_state("강남구 체육시설")

        await agent.search(state, session)

        agent._chain.ainvoke.assert_called_once_with(
            {"message": "강남구 체육시설", "today": ANY}
        )

    async def test_chain_receives_today_as_iso_date(self):
        """today는 YYYY-MM-DD ISO 형식 문자열로 전달된다."""
        agent, session = _make_agent(_SqlParams(), [])
        await agent.search(_make_state(), session)

        call_kwargs = agent._chain.ainvoke.call_args[0][0]
        today_value = call_kwargs["today"]
        # ISO 형식 검증
        date.fromisoformat(today_value)  # 파싱 실패 시 ValueError

    async def test_chain_receives_refined_query_when_router_present(self):
        """Router refined_query가 있으면 LLM에 refined_query가 전달된다."""
        agent, session = _make_agent(_SqlParams(), [])
        state = _make_state("마포구 수영장 알려줘")
        state["refined_query"] = "마포구 수영장"

        await agent.search(state, session)

        call_kwargs = agent._chain.ainvoke.call_args[0][0]
        assert call_kwargs["message"] == "마포구 수영장"

    async def test_router_fields_override_llm_when_refined_query_present(self):
        """refined_query가 있으면 max_class_name/area_name/service_status는 state 값을 사용한다."""
        agent, session = _make_agent(
            _SqlParams(max_class_name="교육", area_name="관악구"),
            [],
        )
        state = _make_state("마포구 체육시설")
        state["refined_query"] = "마포구 체육시설"
        state["max_class_name"] = "체육시설"
        state["area_name"] = "마포구"
        state["service_status"] = "접수중"

        await agent.search(state, session)

        bind = session.execute.call_args[0][1]
        # LLM 반환값(교육/관악구)이 아닌 state 값(체육시설/마포구)을 사용해야 한다
        assert bind.get("max_class_name") == "체육시설"
        assert bind.get("area_name") == "마포구"
        assert bind.get("service_status") == "접수중"

    async def test_payment_type_from_state_when_refined_present(self):
        """refined_query가 있으면 state.payment_type를 sql_search로 전달한다."""
        agent, session = _make_agent(_SqlParams(payment_type="유료"), [])
        state = _make_state("강남구 무료 문화행사")
        state["refined_query"] = "강남구 무료 문화행사"
        state["payment_type"] = "무료"

        await agent.search(state, session)

        bind = session.execute.call_args[0][1]
        # LLM 반환(유료)이 아닌 state 값(무료=정확매칭)이 사용된다
        assert bind.get("payment_type") == "무료"

    async def test_payment_type_from_llm_when_no_refined(self):
        """refined_query가 없으면 LLM 추출 payment_type을 사용한다."""
        agent, session = _make_agent(_SqlParams(payment_type="유료"), [])
        state = _make_state("강남구 유료 체육시설")

        await agent.search(state, session)

        bind = session.execute.call_args[0][1]
        assert bind.get("payment_type") == "유료%"

    async def test_payment_type_validator_normalizes(self):
        """_SqlParams payment_type validator: 무료/유료만 통과, 그 외 None."""
        assert _SqlParams(payment_type="무료").payment_type == "무료"
        assert _SqlParams(payment_type="유료").payment_type == "유료"
        assert _SqlParams(payment_type="회원제").payment_type is None

    async def test_query_builds_category_filter(self):
        """max_class_name 파라미터가 있으면 WHERE에 포함된다."""
        agent, session = _make_agent(_SqlParams(max_class_name="체육시설"), [])
        await agent.search(_make_state(), session)

        call_args = session.execute.call_args
        sql_str = str(call_args[0][0])
        bind = call_args[0][1]

        assert "max_class_name" in sql_str
        assert bind.get("max_class_name") == "체육시설"

    async def test_query_builds_area_filter(self):
        """area_name 파라미터가 있으면 WHERE에 포함된다."""
        agent, session = _make_agent(_SqlParams(area_name="마포구"), [])
        await agent.search(_make_state(), session)

        bind = session.execute.call_args[0][1]
        assert bind.get("area_name") == "마포구"

    async def test_query_builds_keyword_filter(self):
        """keyword 파라미터가 있으면 ILIKE 패턴으로 변환된다."""
        agent, session = _make_agent(_SqlParams(keyword="수영"), [])
        await agent.search(_make_state(), session)

        bind = session.execute.call_args[0][1]
        assert bind.get("keyword") == "%수영%"

    async def test_query_builds_date_filters(self):
        """receipt_date_from/to가 있으면 날짜 조건이 bind에 포함된다."""
        d_from = date(2026, 5, 18)
        d_to = date(2026, 5, 24)
        agent, session = _make_agent(
            _SqlParams(receipt_date_from=d_from, receipt_date_to=d_to), []
        )
        await agent.search(_make_state(), session)

        sql_str = str(session.execute.call_args[0][0])
        bind = session.execute.call_args[0][1]

        assert "receipt_date_from" in sql_str
        assert "receipt_date_to" in sql_str
        assert bind.get("receipt_date_from") == d_from
        assert bind.get("receipt_date_to") == d_to

    async def test_query_no_extra_filter_when_params_empty(self):
        """파라미터가 모두 None이면 deleted_at IS NULL 조건만 포함되고, top_k만 bind된다."""
        from tools.sql_search import TOP_K as _TOP_K

        agent, session = _make_agent(_SqlParams(), [])
        await agent.search(_make_state(), session)

        sql_str = str(session.execute.call_args[0][0])
        bind = session.execute.call_args[0][1]

        assert "deleted_at IS NULL" in sql_str
        # 사용자 입력 유래 필터 파라미터는 없어야 한다
        assert "max_class_name" not in bind
        assert "area_name" not in bind
        assert "keyword" not in bind
        assert "receipt_date_from" not in bind
        assert "receipt_date_to" not in bind
        # top_k는 상수 바인드로 항상 포함된다
        assert bind["top_k"] == _TOP_K

    async def test_query_no_ilike_when_keyword_is_none(self):
        """keyword=None이면 ILIKE 조건이 SQL에 추가되지 않는다."""
        agent, session = _make_agent(_SqlParams(keyword=None, area_name="강남구"), [])
        await agent.search(_make_state(), session)

        sql_str = str(session.execute.call_args[0][0])
        bind = session.execute.call_args[0][1]

        assert "ILIKE" not in sql_str
        assert "keyword" not in bind

    async def test_sql_injection_llm_value_never_in_sql_text(self):
        """LLM이 생성한 값(keyword)은 bind 파라미터로만 전달되고 SQL 문자열에 직접 삽입되지 않는다."""
        malicious = "'; DROP TABLE public_service_reservations; --"
        agent, session = _make_agent(_SqlParams(keyword=malicious), [])
        await agent.search(_make_state(), session)

        sql_str = str(session.execute.call_args[0][0])
        bind = session.execute.call_args[0][1]

        from tools.sql_search import _escape_like

        # 악성 문자열이 SQL 텍스트에 직접 포함되어선 안 된다
        assert malicious not in sql_str
        # _escape_like 처리 후 %...% 래핑되어 bind 파라미터로만 전달되어야 한다
        assert bind["keyword"] == f"%{_escape_like(malicious)}%"

    async def test_search_returns_empty_list_when_no_rows(self):
        """DB 결과가 없으면 sql_results는 빈 리스트다."""
        agent, session = _make_agent(_SqlParams(), [])
        result = await agent.search(_make_state(), session)

        assert result["sql_results"] == []


class TestSqlAgentCoT:
    async def test_reasoning_field_not_passed_to_sql_search(self):
        """reasoning 필드는 CoT 내부용이며 sql_search bind 파라미터에 전달되지 않는다."""
        agent, session = _make_agent(
            _SqlParams(
                reasoning="오늘이 2026-05-22이므로 이번 주는 05-18~05-24.",
                area_name="마포구",
            ),
            [],
        )
        await agent.search(_make_state(), session)

        bind = session.execute.call_args[0][1]
        assert "reasoning" not in bind

    async def test_reasoning_preserved_in_params(self):
        """reasoning이 있는 _SqlParams는 reasoning 값이 유지된다."""
        p = _SqlParams(reasoning="5월 계산: 2026-05-01 ~ 2026-05-31.")
        assert p.reasoning == "5월 계산: 2026-05-01 ~ 2026-05-31."

    async def test_reasoning_defaults_to_none(self):
        """reasoning 없이 생성하면 None이다."""
        p = _SqlParams(max_class_name="체육시설")
        assert p.reasoning is None


class TestSqlParamsValidators:
    def test_invalid_max_class_name_coerced_to_none(self):
        """화이트리스트에 없는 max_class_name은 None으로 정규화된다."""
        p = _SqlParams(max_class_name="운동시설")
        assert p.max_class_name is None

    def test_valid_max_class_name_preserved(self):
        """화이트리스트에 있는 max_class_name은 그대로 반환된다."""
        p = _SqlParams(max_class_name="체육시설")
        assert p.max_class_name == "체육시설"

    def test_invalid_area_name_coerced_to_none(self):
        """25개 자치구가 아닌 area_name은 None으로 정규화된다."""
        p = _SqlParams(area_name="강남")  # '강남구'가 아님
        assert p.area_name is None

    def test_valid_area_name_preserved(self):
        """정확한 자치구명은 그대로 반환된다."""
        p = _SqlParams(area_name="마포구")
        assert p.area_name == "마포구"

    def test_invalid_service_status_coerced_to_none(self):
        """화이트리스트에 없는 service_status는 None으로 정규화된다."""
        p = _SqlParams(service_status="진행중")
        assert p.service_status is None

    def test_valid_service_status_preserved(self):
        """화이트리스트에 있는 service_status는 그대로 반환된다."""
        p = _SqlParams(service_status="접수중")
        assert p.service_status == "접수중"
