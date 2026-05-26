"""_run_services_sync 워커 단위 테스트.

HTTP 엔드포인트가 아닌 백그라운드 워커 로직을 직접 검증한다.
- process_service 위임 여부
- service_id 조회 실패 시 warning 로그 및 스킵
- delete SQL 실행
- Semaphore 동시성 제한
- 개별 upsert 실패 격리 (다른 항목 처리 계속)
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch


from routers.embeddings import _run_services_sync


def _make_service_row(service_id: str = "S001") -> dict:
    """테스트용 ServiceRecord 대역 딕셔너리."""
    return {
        "service_id": service_id,
        "service_name": f"테스트시설_{service_id}",
        "service_gubun": "체육시설",
        "max_class_name": "체육시설",
        "min_class_name": "수영장",
        "area_name": "마포구",
        "place_name": "마포체육관",
        "service_status": "접수중",
        "payment_type": "유료",
        "target_info": "일반",
        "service_url": "https://example.com",
        "detail_content": "3. 상세내용\n수영장 이용 안내\n4. 주의사항",
        "receipt_start_dt": None,
        "receipt_end_dt": None,
        "service_open_start_dt": None,
        "service_open_end_dt": None,
        "coord_x": None,
        "coord_y": None,
    }


@asynccontextmanager
async def _noop_session_ctx():
    """아무것도 하지 않는 세션 컨텍스트 매니저."""
    yield MagicMock()


def _make_session_maker(session_mock):
    """async_sessionmaker 대역: `async with session_maker() as s` 패턴 지원."""
    maker = MagicMock()
    maker.return_value = MagicMock(
        __aenter__=AsyncMock(return_value=session_mock),
        __aexit__=AsyncMock(return_value=None),
    )
    return maker


# ---------------------------------------------------------------------------
# 공통 패치 컨텍스트
# ---------------------------------------------------------------------------

_PATCH_ENGINE = "routers.embeddings.create_async_engine"
_PATCH_SESSION = "routers.embeddings.async_sessionmaker"
_PATCH_EMBEDDINGS = "routers.embeddings.get_embeddings"
_PATCH_LLM = "routers.embeddings.get_chat_model"
_PATCH_PROCESS = "routers.embeddings.process_service"
_PATCH_FETCH = "routers.embeddings._fetch_service_row"


def _make_engine_mock():
    engine = MagicMock()
    engine.dispose = AsyncMock()
    return engine


class TestRunServicesSyncUpsert:
    """upsert 경로 테스트."""

    async def test_upsert_delegates_to_process_service(self):
        """upsert된 service_id마다 process_service가 호출된다."""
        row = _make_service_row("S001")

        with (
            patch(_PATCH_ENGINE, return_value=_make_engine_mock()),
            patch(_PATCH_SESSION, return_value=MagicMock()),
            patch(_PATCH_EMBEDDINGS, return_value=MagicMock()),
            patch(_PATCH_LLM, return_value=MagicMock()),
            patch(_PATCH_FETCH, new=AsyncMock(return_value=row)) as mock_fetch,
            patch(_PATCH_PROCESS, new=AsyncMock()) as mock_process,
        ):
            await _run_services_sync(["S001"], [])

        mock_fetch.assert_awaited_once()
        mock_process.assert_awaited_once()
        _, kwargs = mock_process.call_args
        assert kwargs.get("tracks") == {"A", "B", "C"}

    async def test_upsert_multiple_services(self):
        """여러 service_id가 있을 때 각각 process_service가 호출된다."""

        async def _fetch_by_id(session, service_id):
            return _make_service_row(service_id)

        with (
            patch(_PATCH_ENGINE, return_value=_make_engine_mock()),
            patch(_PATCH_SESSION, return_value=MagicMock()),
            patch(_PATCH_EMBEDDINGS, return_value=MagicMock()),
            patch(_PATCH_LLM, return_value=MagicMock()),
            patch(_PATCH_FETCH, side_effect=_fetch_by_id),
            patch(_PATCH_PROCESS, new=AsyncMock()) as mock_process,
        ):
            await _run_services_sync(["S001", "S002", "S003"], [])

        assert mock_process.await_count == 3


class TestRunServicesSyncMissingRow:
    """service_id 조회 실패 시 동작 테스트."""

    async def test_missing_service_logs_warning_and_skips(self):
        """_fetch_service_row가 None을 반환하면 WARNING을 남기고 process_service를 호출하지 않는다."""
        with (
            patch(_PATCH_ENGINE, return_value=_make_engine_mock()),
            patch(_PATCH_SESSION, return_value=MagicMock()),
            patch(_PATCH_EMBEDDINGS, return_value=MagicMock()),
            patch(_PATCH_LLM, return_value=MagicMock()),
            patch(_PATCH_FETCH, new=AsyncMock(return_value=None)),
            patch(_PATCH_PROCESS, new=AsyncMock()) as mock_process,
            patch("routers.embeddings.logger") as mock_logger,
        ):
            await _run_services_sync(["MISSING_ID"], [])

        mock_process.assert_not_awaited()
        warning_calls = mock_logger.warning.call_args_list
        assert any(
            "MISSING_ID" in str(args) or "MISSING_ID" in str(kwargs)
            for args, kwargs in warning_calls
        )


class TestRunServicesSyncDelete:
    """delete 경로 테스트."""

    async def test_delete_executes_sql_for_each_id(self):
        """delete 목록의 각 service_id마다 DELETE SQL이 실행된다."""
        ai_session_mock = MagicMock()
        ai_session_mock.execute = AsyncMock()
        ai_session_mock.begin = MagicMock(
            return_value=MagicMock(
                __aenter__=AsyncMock(return_value=None),
                __aexit__=AsyncMock(return_value=None),
            )
        )

        session_maker = MagicMock()
        session_maker.return_value = MagicMock(
            __aenter__=AsyncMock(return_value=ai_session_mock),
            __aexit__=AsyncMock(return_value=None),
        )

        with (
            patch(_PATCH_ENGINE, return_value=_make_engine_mock()),
            patch(_PATCH_SESSION, return_value=session_maker),
            patch(_PATCH_EMBEDDINGS, return_value=MagicMock()),
            patch(_PATCH_LLM, return_value=MagicMock()),
        ):
            await _run_services_sync([], ["D001", "D002"])

        # execute가 2회 호출되고 각 호출에 service_id가 전달됐는지 확인
        assert ai_session_mock.execute.await_count == 2
        called_sids = {
            call_args.kwargs.get("sid") or call_args.args[1].get("sid")
            for call_args in ai_session_mock.execute.call_args_list
        }
        assert called_sids == {"D001", "D002"}

    async def test_upsert_only_no_delete_sql(self):
        """delete 목록이 비어 있으면 DELETE SQL이 실행되지 않는다."""
        row = _make_service_row("S001")

        with (
            patch(_PATCH_ENGINE, return_value=_make_engine_mock()),
            patch(_PATCH_SESSION, return_value=MagicMock()),
            patch(_PATCH_EMBEDDINGS, return_value=MagicMock()),
            patch(_PATCH_LLM, return_value=MagicMock()),
            patch(_PATCH_FETCH, new=AsyncMock(return_value=row)),
            patch(_PATCH_PROCESS, new=AsyncMock()),
        ):
            # delete=[] → 예외 없이 정상 완료만 확인
            await _run_services_sync(["S001"], [])


class TestRunServicesSyncFailureIsolation:
    """개별 upsert 실패 격리 테스트."""

    async def test_one_failure_does_not_stop_others(self):
        """S002 처리 중 예외가 발생해도 S001·S003은 정상 처리된다."""
        call_order: list[str] = []

        async def _fetch(session, service_id):
            return _make_service_row(service_id)

        async def _process(row, *, session, embedder, llm_client, tracks):
            sid = row["service_id"]
            if sid == "S002":
                raise RuntimeError("의도적 실패")
            call_order.append(sid)

        with (
            patch(_PATCH_ENGINE, return_value=_make_engine_mock()),
            patch(_PATCH_SESSION, return_value=MagicMock()),
            patch(_PATCH_EMBEDDINGS, return_value=MagicMock()),
            patch(_PATCH_LLM, return_value=MagicMock()),
            patch(_PATCH_FETCH, side_effect=_fetch),
            patch(_PATCH_PROCESS, side_effect=_process),
        ):
            # 예외가 밖으로 전파되지 않아야 한다
            await _run_services_sync(["S001", "S002", "S003"], [])

        assert set(call_order) == {"S001", "S003"}

    async def test_failure_logs_exception(self):
        """process_service 예외 발생 시 로그에 service_id가 기록된다."""

        async def _fetch(session, service_id):
            return _make_service_row(service_id)

        async def _process(row, *, session, embedder, llm_client, tracks):
            raise ValueError("임베딩 오류")

        with (
            patch(_PATCH_ENGINE, return_value=_make_engine_mock()),
            patch(_PATCH_SESSION, return_value=MagicMock()),
            patch(_PATCH_EMBEDDINGS, return_value=MagicMock()),
            patch(_PATCH_LLM, return_value=MagicMock()),
            patch(_PATCH_FETCH, side_effect=_fetch),
            patch(_PATCH_PROCESS, side_effect=_process),
            patch("routers.embeddings.logger") as mock_logger,
        ):
            await _run_services_sync(["ERR001"], [])

        # logger.exception("임베딩 처리 실패: service_id=%s", "ERR001")
        exc_calls = mock_logger.exception.call_args_list
        assert any(
            "ERR001" in str(args) or "ERR001" in str(kwargs)
            for args, kwargs in exc_calls
        )


class TestRunServicesSyncConcurrency:
    """Semaphore 동시성 제한 테스트."""

    async def test_semaphore_limits_concurrent_upserts(self):
        """settings.embedding_sync_concurrency보다 많은 upsert가 요청돼도
        동시 실행 수가 concurrency를 초과하지 않는다."""
        max_concurrency = 2
        current_concurrent = 0
        peak_concurrent = 0

        async def _fetch(session, service_id):
            return _make_service_row(service_id)

        async def _slow_process(row, *, session, embedder, llm_client, tracks):
            nonlocal current_concurrent, peak_concurrent
            current_concurrent += 1
            peak_concurrent = max(peak_concurrent, current_concurrent)
            await asyncio.sleep(0)  # yield 한 번
            current_concurrent -= 1

        mock_settings = MagicMock()
        mock_settings.on_data_database_url = "postgresql+asyncpg://test/data"
        mock_settings.on_ai_database_url = "postgresql+asyncpg://test/ai"
        mock_settings.embedding_sync_concurrency = max_concurrency

        with (
            patch(_PATCH_ENGINE, return_value=_make_engine_mock()),
            patch(_PATCH_SESSION, return_value=MagicMock()),
            patch(_PATCH_EMBEDDINGS, return_value=MagicMock()),
            patch(_PATCH_LLM, return_value=MagicMock()),
            patch(_PATCH_FETCH, side_effect=_fetch),
            patch(_PATCH_PROCESS, side_effect=_slow_process),
            patch("routers.embeddings.settings", mock_settings),
        ):
            sids = [f"S{i:03d}" for i in range(6)]
            await _run_services_sync(sids, [])

        assert peak_concurrent <= max_concurrency
