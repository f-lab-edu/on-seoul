"""search_persist_node 단위 테스트.

GraphNodes.search_persist_node 가 chat_search_queries / chat_search_results 를
올바르게 적재하는지, best-effort 동작(실패 시 그래프 영향 없음)이 맞는지,
0건 결과 정책(query 행은 항상 기록)이 작동하는지 검증한다.
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from agents.nodes import GraphNodes
from schemas.search import (
    ChannelData,
    ChannelHit,
    ChannelQuery,
    SearchChannel,
    SearchKind,
)


# ---------------------------------------------------------------------------
# 헬퍼 — 테스트용 ChannelData 생성
# ---------------------------------------------------------------------------


def _make_channel(
    kind: str,
    query_text: str | None = "수영장",
    parameters: dict | None = None,
    hits: list[ChannelHit] | None = None,
) -> ChannelData:
    return ChannelData(
        kind=kind,
        query=ChannelQuery(
            query_text=query_text,
            parameters=parameters or {"top_k": 5},
        ),
        hits=hits or [],
    )


def _make_hit(
    rank: int, service_id: str = "SVC001", score: float | None = 0.9
) -> ChannelHit:
    return ChannelHit(rank=rank, service_id=service_id, score=score, meta={})


def _make_nodes(ai_session: Any) -> GraphNodes:
    """GraphNodes 인스턴스를 반환한다.

    제안 0 이후 GraphNodes 는 무상태이므로 세션은 인스턴스가 아니라 노드 메서드
    인자로 전달한다. ai_session 인자는 호환을 위해 받지만 무시되며, 호출부가
    search_persist_node(state, ai_session) 로 직접 주입한다.
    """
    return GraphNodes(
        router=MagicMock(),
        sql_agent=MagicMock(),
        vector_agent=MagicMock(),
        answer_agent=MagicMock(),
        analytics_agent=MagicMock(),
    )


def _make_session(execute_side_effect=None, commit_side_effect=None):
    """AsyncMock 세션을 반환한다."""
    session = MagicMock()
    if execute_side_effect is not None:
        session.execute = AsyncMock(side_effect=execute_side_effect)
    else:
        session.execute = AsyncMock(return_value=MagicMock())
    if commit_side_effect is not None:
        session.commit = AsyncMock(side_effect=commit_side_effect)
    else:
        session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _base_state(**kwargs) -> dict:
    return {
        "message_id": 42,
        "search_channels": {},
        **kwargs,
    }


# ---------------------------------------------------------------------------
# 테스트
# ---------------------------------------------------------------------------


class TestSearchPersistNodeEmptyChannels:
    async def test_empty_channels_skips_all_inserts(self):
        """search_channels 가 빈 dict 면 execute 를 호출하지 않는다."""
        session = _make_session()
        nodes = _make_nodes(session)
        state = _base_state(search_channels={})

        result = await nodes.search_persist_node(state, session)

        assert set(result) <= {"node_path"}
        session.execute.assert_not_called()
        session.commit.assert_not_called()

    async def test_none_channels_skips_all_inserts(self):
        """search_channels 가 None 이어도 skip 된다."""
        session = _make_session()
        nodes = _make_nodes(session)
        state = _base_state(search_channels=None)

        result = await nodes.search_persist_node(state, session)

        assert set(result) <= {"node_path"}
        session.execute.assert_not_called()

    async def test_empty_channels_appends_skip_to_node_path(self):
        session = _make_session()
        nodes = _make_nodes(session)
        state = _base_state(search_channels={})

        result = await nodes.search_persist_node(state, session)

        assert "search_persist_skip" in result["node_path"]


class TestSearchPersistNodeInsertion:
    async def test_inserts_query_row_per_channel(self):
        """sql / vector / final 채널 → queries 테이블에 3회 execute (queries + results + commit)."""
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            SearchChannel.SQL: _make_channel(SearchKind.SQL, query_text="헬스장"),
            SearchChannel.VECTOR: _make_channel(SearchKind.VECTOR),
            SearchChannel.FINAL: _make_channel(SearchKind.FINAL, query_text=None),
        }
        state = _base_state(search_channels=channels)

        result = await nodes.search_persist_node(state, session)

        assert set(result) <= {"node_path"}
        # queries INSERT (query_rows=3) + results INSERT (hits 없어서 result_rows=0 → skip)
        # execute 는 queries 한 번만 호출 (result_rows=0 이면 results INSERT 생략)
        calls = session.execute.call_args_list
        # 첫 번째 call이 queries SQL 이어야 한다
        assert len(calls) == 1
        first_call_sql = str(calls[0].args[0])
        assert "chat_search_queries" in first_call_sql
        assert len(calls[0].args[1]) == 3  # 3 채널

    async def test_inserts_result_rows_per_hit(self):
        """hits 가 있는 채널은 results 테이블에도 INSERT 된다."""
        session = _make_session()
        nodes = _make_nodes(session)
        hits = [_make_hit(1, "SVC001", 0.95), _make_hit(2, "SVC002", 0.80)]
        channels = {
            SearchChannel.SQL: _make_channel(SearchKind.SQL, hits=hits),
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        assert len(calls) == 2  # queries + results
        results_call = calls[1]
        assert "chat_search_results" in str(results_call.args[0])
        assert len(results_call.args[1]) == 2  # 2 hits

    async def test_kind_and_channel_consistent_in_both_tables(self):
        """같은 채널의 queries / results 행에서 kind 와 channel 이 일치한다."""
        session = _make_session()
        nodes = _make_nodes(session)
        hits = [_make_hit(1)]
        channels = {
            SearchChannel.VECTOR_A: _make_channel(
                SearchKind.VECTOR,
                query_text="공원",
                hits=hits,
            )
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        query_row = calls[0].args[1][0]
        result_row = calls[1].args[1][0]

        assert query_row["kind"] == SearchKind.VECTOR
        assert query_row["channel"] == SearchChannel.VECTOR_A
        assert result_row["kind"] == SearchKind.VECTOR
        assert result_row["channel"] == SearchChannel.VECTOR_A

    async def test_zero_hits_still_writes_query_row(self):
        """hits 가 비어도 chat_search_queries 에는 1행 INSERT 한다.

        "검색했는데 결과 없음" 이 분석 가치가 있기 때문 (recall 부족 / stopword 과적용 진단).
        """
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            SearchChannel.BM25: _make_channel(SearchKind.BM25, hits=[]),
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        # queries INSERT 만 호출되어야 한다
        assert len(calls) == 1
        assert "chat_search_queries" in str(calls[0].args[0])
        assert len(calls[0].args[1]) == 1

    async def test_message_id_passed_correctly(self):
        """state["message_id"] 가 모든 INSERT 행에 올바르게 전달된다."""
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            SearchChannel.SQL: _make_channel(SearchKind.SQL, hits=[_make_hit(1)]),
        }
        state = _base_state(message_id=9999, search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        query_row = calls[0].args[1][0]
        result_row = calls[1].args[1][0]
        assert query_row["message_id"] == 9999
        assert result_row["message_id"] == 9999

    async def test_parameters_and_meta_serialized_as_json(self):
        """parameters / meta 는 JSON 직렬화 후 전달된다 (CAST(:x AS jsonb) 용)."""
        session = _make_session()
        nodes = _make_nodes(session)
        params = {"top_k": 5, "min_similarity": 0.7}
        meta = {"intent_label": "체육시설"}
        hit = ChannelHit(rank=1, service_id="SVC001", score=0.9, meta=meta)
        channels = {
            SearchChannel.VECTOR: _make_channel(
                SearchKind.VECTOR, parameters=params, hits=[hit]
            )
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        query_row = calls[0].args[1][0]
        result_row = calls[1].args[1][0]

        # JSON 문자열인지 확인 (round-trip 가능)
        assert json.loads(query_row["parameters"]) == params
        assert json.loads(result_row["meta"]) == meta

    async def test_rrf_channel_query_text_is_none(self):
        """rrf 채널의 query_text 는 None 이어도 정상 저장된다."""
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            SearchChannel.RRF: _make_channel(
                SearchKind.RRF,
                query_text=None,
                parameters={"source_channels": ["vector", "bm25"]},
            )
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        query_row = calls[0].args[1][0]
        assert query_row["query_text"] is None

    async def test_freeform_channel_accepted(self):
        """미등록 채널명도 INSERT 가능 (channel 에 CHECK 없음)."""
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            "future_channel": ChannelData(
                kind=SearchKind.VECTOR,
                query=ChannelQuery(query_text="test", parameters={}),
                hits=[],
            )
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        assert len(calls) == 1
        query_row = calls[0].args[1][0]
        assert query_row["channel"] == "future_channel"

    async def test_correct_insert_sql_used(self):
        """올바른 SQL 상수(ON CONFLICT ... DO NOTHING 포함)가 사용된다."""
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            SearchChannel.SQL: _make_channel(SearchKind.SQL, hits=[_make_hit(1)]),
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        # sqlalchemy text() 객체를 str로 변환해서 비교
        queries_sql = str(calls[0].args[0])
        results_sql = str(calls[1].args[0])

        assert "chat_search_queries" in queries_sql
        assert "ON CONFLICT" in queries_sql
        assert "chat_search_results" in results_sql
        assert "ON CONFLICT" in results_sql

    async def test_commit_called_once(self):
        """두 테이블 INSERT 후 commit 이 한 번만 호출된다 (단일 트랜잭션)."""
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            SearchChannel.SQL: _make_channel(SearchKind.SQL, hits=[_make_hit(1)]),
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        session.commit.assert_called_once()

    async def test_node_path_appended_on_success(self):
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        state = _base_state(search_channels=channels)

        result = await nodes.search_persist_node(state, session)

        assert "search_persist" in result["node_path"]


class TestSearchPersistNodeBestEffort:
    async def test_execute_failure_returns_empty_dict(self):
        """execute 예외 시 {} 를 반환하여 그래프를 계속 진행시킨다."""
        session = _make_session(execute_side_effect=RuntimeError("DB 오류"))
        nodes = _make_nodes(session)
        channels = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        state = _base_state(search_channels=channels)

        result = await nodes.search_persist_node(state, session)

        assert set(result) <= {"node_path"}

    async def test_execute_failure_calls_rollback(self):
        """execute 예외 시 rollback 이 호출된다."""
        session = _make_session(execute_side_effect=RuntimeError("DB 오류"))
        nodes = _make_nodes(session)
        channels = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        session.rollback.assert_called_once()

    async def test_commit_failure_calls_rollback(self):
        """commit 예외 시에도 rollback 이 호출된다."""
        session = _make_session(commit_side_effect=RuntimeError("commit 실패"))
        nodes = _make_nodes(session)
        channels = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        state = _base_state(search_channels=channels)

        result = await nodes.search_persist_node(state, session)

        assert set(result) <= {"node_path"}
        session.rollback.assert_called_once()

    async def test_rollback_failure_is_swallowed(self):
        """rollback 자체도 실패할 때 예외가 전파되지 않는다."""
        session = _make_session(execute_side_effect=RuntimeError("execute 실패"))
        session.rollback = AsyncMock(side_effect=RuntimeError("rollback 실패"))
        nodes = _make_nodes(session)
        channels = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        state = _base_state(search_channels=channels)

        # 예외가 전파되지 않아야 한다
        result = await nodes.search_persist_node(state, session)
        assert set(result) <= {"node_path"}

    async def test_failure_appends_error_to_node_path(self):
        session = _make_session(execute_side_effect=RuntimeError("오류"))
        nodes = _make_nodes(session)
        channels = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        state = _base_state(search_channels=channels)

        result = await nodes.search_persist_node(state, session)

        assert "search_persist_error" in result["node_path"]

    async def test_failure_logs_warning(self):
        """execute 실패 시 logger.warning 이 호출된다."""
        session = _make_session(execute_side_effect=RuntimeError("오류"))
        nodes = _make_nodes(session)
        channels = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        state = _base_state(search_channels=channels)

        with patch("agents.nodes.logger") as mock_logger:
            await nodes.search_persist_node(state, session)
            mock_logger.warning.assert_called_once()


class TestRetryPrepResetsSearchChannels:
    async def test_retry_prep_resets_search_channels(self):
        """retry_prep_node 가 RESET_CHANNELS sentinel 을 반환한다."""
        from schemas.search import RESET_CHANNELS

        nodes = GraphNodes(
            router=MagicMock(),
            sql_agent=MagicMock(),
            vector_agent=MagicMock(),
            answer_agent=MagicMock(),
            analytics_agent=MagicMock(),
        )
        state = {
            "room_id": 1,
            "message_id": 42,
            "retry_count": 0,
            "search_channels": {SearchChannel.SQL: _make_channel(SearchKind.SQL)},
        }

        result = await nodes.retry_prep_node(state)

        assert "search_channels" in result
        # sentinel 자체가 반환되어야 reducer 가 명시적 리셋으로 인식
        assert result["search_channels"] is RESET_CHANNELS

    async def test_search_channels_reducer_handles_explicit_reset(self):
        """reducer 가 RESET_CHANNELS sentinel 만 리셋 신호로 처리한다."""
        from schemas.search import RESET_CHANNELS, search_channels_reducer

        old = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        # 명시적 리셋: sentinel 전달 시 빈 dict 반환
        assert search_channels_reducer(old, RESET_CHANNELS) == {}

    async def test_search_channels_reducer_empty_dict_is_noop(self):
        """빈 dict 와 None 은 더 이상 리셋이 아니라 no-op (기존 old 유지)."""
        from schemas.search import search_channels_reducer

        old = {SearchChannel.SQL: _make_channel(SearchKind.SQL)}
        # 빈 dict → no-op
        assert search_channels_reducer(old, {}) == old
        # None → no-op
        assert search_channels_reducer(old, None) == old
        # old 가 None 일 때 빈 dict 반환 (안전한 기본값)
        assert search_channels_reducer(None, {}) == {}
        assert search_channels_reducer(None, None) == {}

    async def test_search_channels_reducer_merges_normally(self):
        """일반 채널 추가는 누적 병합된다."""
        from schemas.search import search_channels_reducer

        sql_data = _make_channel(SearchKind.SQL)
        vec_data = _make_channel(SearchKind.VECTOR)

        merged = search_channels_reducer(
            {SearchChannel.SQL: sql_data},
            {SearchChannel.VECTOR: vec_data},
        )
        assert SearchChannel.SQL in merged
        assert SearchChannel.VECTOR in merged

    async def test_search_channels_reducer_overwrites_same_key(self):
        """같은 채널 키가 재등장하면 최신 데이터로 덮어쓴다 (재시도 마지막 값 유지)."""
        from schemas.search import search_channels_reducer

        old_data = _make_channel(SearchKind.SQL, query_text="이전")
        new_data = _make_channel(SearchKind.SQL, query_text="새로운")

        result = search_channels_reducer(
            {SearchChannel.SQL: old_data},
            {SearchChannel.SQL: new_data},
        )
        assert result[SearchChannel.SQL]["query"]["query_text"] == "새로운"


class TestSearchPersistNodeAtomicity:
    """results INSERT 실패 시 queries INSERT 도 함께 롤백되는지 (트랜잭션 원자성) 검증."""

    async def test_results_insert_failure_rolls_back_both_tables(self):
        """첫 번째 execute(queries) 는 성공하고 두 번째 execute(results) 가 실패할 때
        rollback 이 호출되어 두 테이블 모두 롤백됨을 보장한다.
        """
        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("results INSERT 실패")
            return MagicMock()

        session = _make_session(execute_side_effect=execute_side_effect)
        nodes = _make_nodes(session)
        # hits 가 있어야 result_rows 가 생겨 두 번째 execute 가 호출된다.
        hits = [_make_hit(1, "SVC001", 0.9)]
        channels = {
            SearchChannel.SQL: _make_channel(SearchKind.SQL, hits=hits),
        }
        state = _base_state(search_channels=channels)

        result = await nodes.search_persist_node(state, session)

        assert session.execute.call_count == 2  # queries + results 시도
        session.rollback.assert_called_once()
        assert "search_persist_error" in result["node_path"]

    async def test_results_insert_failure_no_commit(self):
        """results INSERT 실패 시 commit 은 호출되지 않는다."""
        call_count = 0

        async def execute_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("results INSERT 실패")
            return MagicMock()

        session = _make_session(execute_side_effect=execute_side_effect)
        nodes = _make_nodes(session)
        hits = [_make_hit(1)]
        channels = {
            SearchChannel.VECTOR: _make_channel(SearchKind.VECTOR, hits=hits),
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        session.commit.assert_not_called()


class TestSearchPersistNodeMultiChannelKinds:
    """여러 채널이 혼합된 경우 각 채널별로 kind 가 올바르게 INSERT 되는지 검증."""

    async def test_mixed_channels_produce_correct_kind_per_row(self):
        """sql, vector, final 채널이 혼합된 상태에서 각 query 행의 kind 가
        채널에 대응하는 올바른 SearchKind 값으로 설정된다.
        """
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            SearchChannel.SQL: _make_channel(SearchKind.SQL, query_text="헬스장"),
            SearchChannel.VECTOR: _make_channel(SearchKind.VECTOR, query_text="공원"),
            SearchChannel.FINAL: _make_channel(SearchKind.FINAL, query_text=None),
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        # 모든 hits 가 없으므로 queries execute 1회만 호출된다.
        assert len(calls) == 1
        rows: list[dict] = calls[0].args[1]

        # channel 이름 → 행 매핑
        row_by_channel = {r["channel"]: r for r in rows}

        assert row_by_channel[SearchChannel.SQL]["kind"] == SearchKind.SQL
        assert row_by_channel[SearchChannel.VECTOR]["kind"] == SearchKind.VECTOR
        assert row_by_channel[SearchChannel.FINAL]["kind"] == SearchKind.FINAL

    async def test_mixed_channels_with_hits_produce_correct_kind_in_results_rows(self):
        """hits 가 있는 sql, vector 채널의 results 행에서도 kind 가 올바르게 분리된다."""
        session = _make_session()
        nodes = _make_nodes(session)
        channels = {
            SearchChannel.SQL: _make_channel(
                SearchKind.SQL,
                query_text="수영장",
                hits=[_make_hit(1, "SVC_SQL", None)],
            ),
            SearchChannel.VECTOR: _make_channel(
                SearchKind.VECTOR,
                query_text="공원",
                hits=[_make_hit(1, "SVC_VEC", 0.88)],
            ),
        }
        state = _base_state(search_channels=channels)

        await nodes.search_persist_node(state, session)

        calls = session.execute.call_args_list
        assert len(calls) == 2  # queries + results
        result_rows: list[dict] = calls[1].args[1]

        by_service = {r["service_id"]: r for r in result_rows}
        assert by_service["SVC_SQL"]["kind"] == SearchKind.SQL
        assert by_service["SVC_SQL"]["channel"] == SearchChannel.SQL
        assert by_service["SVC_VEC"]["kind"] == SearchKind.VECTOR
        assert by_service["SVC_VEC"]["channel"] == SearchChannel.VECTOR
