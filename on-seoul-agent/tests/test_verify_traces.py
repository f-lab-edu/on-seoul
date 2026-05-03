"""scripts/verify_traces.py — trace 검증 스크립트 단위 테스트 (mock DB)."""

from unittest.mock import AsyncMock, MagicMock


class TestVerifyTraces:
    async def test_valid_trace_passes(self):
        """필수 키를 모두 포함한 trace는 PASS로 분류된다."""
        from scripts.verify_traces import verify_trace_row

        trace = {
            "intent": "SQL_SEARCH",
            "node_path": ["router", "sql_agent", "answer"],
            "elapsed_ms": 1230,
        }
        result = verify_trace_row({"message_id": 1, "trace": trace})
        assert result["status"] == "PASS"
        assert result["missing_keys"] == []

    async def test_missing_intent_fails(self):
        """intent 키가 없으면 FAIL로 분류된다."""
        from scripts.verify_traces import verify_trace_row

        trace = {
            "node_path": ["router", "answer"],
            "elapsed_ms": 500,
        }
        result = verify_trace_row({"message_id": 2, "trace": trace})
        assert result["status"] == "FAIL"
        assert "intent" in result["missing_keys"]

    async def test_missing_node_path_fails(self):
        """node_path 키가 없으면 FAIL로 분류된다."""
        from scripts.verify_traces import verify_trace_row

        trace = {
            "intent": "VECTOR_SEARCH",
            "elapsed_ms": 800,
        }
        result = verify_trace_row({"message_id": 3, "trace": trace})
        assert result["status"] == "FAIL"
        assert "node_path" in result["missing_keys"]

    async def test_missing_elapsed_ms_fails(self):
        """elapsed_ms 키가 없으면 FAIL로 분류된다."""
        from scripts.verify_traces import verify_trace_row

        trace = {
            "intent": "FALLBACK",
            "node_path": ["router", "fallback", "answer"],
        }
        result = verify_trace_row({"message_id": 4, "trace": trace})
        assert result["status"] == "FAIL"
        assert "elapsed_ms" in result["missing_keys"]

    async def test_multiple_missing_keys_reported(self):
        """여러 키가 없으면 모두 missing_keys에 포함된다."""
        from scripts.verify_traces import verify_trace_row

        result = verify_trace_row({"message_id": 5, "trace": {}})
        assert result["status"] == "FAIL"
        assert set(result["missing_keys"]) == {"intent", "node_path", "elapsed_ms"}

    async def test_intent_type_validation(self):
        """intent가 유효한 값(SQL_SEARCH/VECTOR_SEARCH/MAP/FALLBACK)이면 PASS."""
        from scripts.verify_traces import verify_trace_row

        for intent in ("SQL_SEARCH", "VECTOR_SEARCH", "MAP", "FALLBACK"):
            trace = {"intent": intent, "node_path": [], "elapsed_ms": 100}
            result = verify_trace_row({"message_id": 10, "trace": trace})
            assert result["status"] == "PASS", f"{intent} should pass"

    async def test_invalid_intent_value_fails(self):
        """intent가 유효하지 않은 값이면 FAIL로 분류된다."""
        from scripts.verify_traces import verify_trace_row

        trace = {
            "intent": "UNKNOWN_TYPE",
            "node_path": ["router"],
            "elapsed_ms": 500,
        }
        result = verify_trace_row({"message_id": 6, "trace": trace})
        assert result["status"] == "FAIL"
        assert "intent" in result["missing_keys"]

    async def test_node_path_must_be_list(self):
        """node_path가 list가 아니면 FAIL로 분류된다."""
        from scripts.verify_traces import verify_trace_row

        trace = {
            "intent": "SQL_SEARCH",
            "node_path": "router,answer",  # str, not list
            "elapsed_ms": 300,
        }
        result = verify_trace_row({"message_id": 7, "trace": trace})
        assert result["status"] == "FAIL"
        assert "node_path" in result["missing_keys"]

    async def test_elapsed_ms_must_be_int(self):
        """elapsed_ms가 int가 아니면 FAIL로 분류된다."""
        from scripts.verify_traces import verify_trace_row

        trace = {
            "intent": "MAP",
            "node_path": ["router", "map_search"],
            "elapsed_ms": "fast",  # not an int
        }
        result = verify_trace_row({"message_id": 8, "trace": trace})
        assert result["status"] == "FAIL"
        assert "elapsed_ms" in result["missing_keys"]

    async def test_elapsed_ms_float_fails(self):
        """elapsed_ms가 float이면 FAIL로 분류된다 (workflow는 int로 저장)."""
        from scripts.verify_traces import verify_trace_row

        trace = {
            "intent": "SQL_SEARCH",
            "node_path": ["router", "sql_agent", "answer"],
            "elapsed_ms": 1.23,  # float, not int
        }
        result = verify_trace_row({"message_id": 9, "trace": trace})
        assert result["status"] == "FAIL"
        assert "elapsed_ms" in result["missing_keys"]

    async def test_fetch_and_verify_calls_db(self):
        """fetch_and_verify가 DB에서 N건을 조회하고 결과를 반환한다."""
        from scripts.verify_traces import fetch_and_verify

        rows = [
            {
                "message_id": 1,
                "trace": {
                    "intent": "SQL_SEARCH",
                    "node_path": ["router", "sql_agent", "answer"],
                    "elapsed_ms": 1500,
                },
            },
            {
                "message_id": 2,
                "trace": {
                    "intent": "FALLBACK",
                    "node_path": ["router", "fallback", "answer"],
                    "elapsed_ms": 300,
                },
            },
        ]

        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = rows

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        results = await fetch_and_verify(mock_session, limit=2)
        assert len(results) == 2
        assert all(r["status"] == "PASS" for r in results)

    async def test_fetch_and_verify_mixed_results(self):
        """fetch_and_verify가 PASS/FAIL 혼합 결과를 반환한다."""
        from scripts.verify_traces import fetch_and_verify

        rows = [
            {
                "message_id": 10,
                "trace": {
                    "intent": "VECTOR_SEARCH",
                    "node_path": ["router", "vector_agent", "answer"],
                    "elapsed_ms": 2100,
                },
            },
            {
                "message_id": 11,
                "trace": {
                    # intent 없음
                    "node_path": ["router"],
                    "elapsed_ms": 500,
                },
            },
        ]

        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = rows

        mock_session = MagicMock()
        mock_session.execute = AsyncMock(return_value=mock_result)

        results = await fetch_and_verify(mock_session, limit=2)
        statuses = {r["message_id"]: r["status"] for r in results}
        assert statuses[10] == "PASS"
        assert statuses[11] == "FAIL"
