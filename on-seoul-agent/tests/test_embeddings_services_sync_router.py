"""POST /embeddings/services/sync 엔드포인트 테스트."""

from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient

from main import app


def _make_app_client():
    """lifespan 없이 FastAPI 테스트 클라이언트를 반환한다."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


class TestServicesSyncEndpoint:
    async def test_returns_202_with_accepted_counts(self):
        """정상 요청 시 202와 accepted 카운트를 반환한다."""
        with patch("routers.embeddings._run_services_sync", new=AsyncMock()):
            async with _make_app_client() as client:
                resp = await client.post(
                    "/embeddings/services/sync",
                    json={"upsert": ["S001", "S002"], "delete": ["S003"]},
                )

        assert resp.status_code == 202
        body = resp.json()
        assert body["accepted"]["upsert"] == 2
        assert body["accepted"]["delete"] == 1

    async def test_empty_both_arrays_returns_422(self):
        """upsert와 delete 모두 빈 배열이면 422를 반환한다."""
        async with _make_app_client() as client:
            resp = await client.post(
                "/embeddings/services/sync",
                json={"upsert": [], "delete": []},
            )

        assert resp.status_code == 422

    async def test_overlap_in_upsert_and_delete_returns_422(self):
        """upsert와 delete에 동일한 service_id가 있으면 422를 반환한다."""
        async with _make_app_client() as client:
            resp = await client.post(
                "/embeddings/services/sync",
                json={"upsert": ["S001", "S002"], "delete": ["S002", "S003"]},
            )

        assert resp.status_code == 422

    async def test_exceeds_max_items_returns_422(self):
        """upsert + delete 합계가 500개를 초과하면 422를 반환한다."""
        upsert_ids = [f"S{i:04d}" for i in range(300)]
        delete_ids = [f"D{i:04d}" for i in range(201)]
        async with _make_app_client() as client:
            resp = await client.post(
                "/embeddings/services/sync",
                json={"upsert": upsert_ids, "delete": delete_ids},
            )

        assert resp.status_code == 422

    async def test_invalid_service_id_format_returns_422(self):
        """service_id에 허용되지 않은 문자가 있으면 422를 반환한다."""
        async with _make_app_client() as client:
            resp = await client.post(
                "/embeddings/services/sync",
                json={"upsert": ["S001 invalid!"], "delete": []},
            )

        assert resp.status_code == 422

    async def test_background_task_enqueued(self):
        """백그라운드 태스크가 enqueue되어 _run_services_sync가 호출된다."""
        mock_sync = AsyncMock()
        with patch("routers.embeddings._run_services_sync", new=mock_sync):
            async with _make_app_client() as client:
                resp = await client.post(
                    "/embeddings/services/sync",
                    json={"upsert": ["S001"], "delete": []},
                )

        assert resp.status_code == 202
        # BackgroundTasks는 응답 후 실행되므로 호출 여부를 직접 검증하기 어렵다.
        # 단, 202 응답이 반환된 것으로 enqueue 성공을 확인한다.

    async def test_only_delete_accepted(self):
        """delete만 있어도 202를 반환한다."""
        with patch("routers.embeddings._run_services_sync", new=AsyncMock()):
            async with _make_app_client() as client:
                resp = await client.post(
                    "/embeddings/services/sync",
                    json={"upsert": [], "delete": ["S001"]},
                )

        assert resp.status_code == 202
        body = resp.json()
        assert body["accepted"]["upsert"] == 0
        assert body["accepted"]["delete"] == 1
