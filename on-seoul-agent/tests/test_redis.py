"""core/redis.py — get_redis() 팩토리 단위 테스트."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestGetRedis:
    async def test_get_redis_returns_client(self):
        """get_redis()가 redis.asyncio 클라이언트를 반환한다."""
        from core.redis import get_redis

        mock_client = MagicMock()
        with patch("core.redis.aioredis.from_url", return_value=mock_client) as mock_from_url:
            client = get_redis()
            assert client is mock_client
            mock_from_url.assert_called_once()

    async def test_get_redis_uses_settings_url(self):
        """get_redis()가 settings.redis_url을 사용한다."""
        from core.redis import get_redis

        with patch("core.redis.aioredis.from_url", return_value=MagicMock()) as mock_from_url:
            get_redis()
            call_args = mock_from_url.call_args
            # 첫 번째 인자가 redis URL
            url_arg = call_args[0][0] if call_args[0] else call_args[1].get("url")
            assert url_arg is not None
            assert "redis" in url_arg

    async def test_get_redis_decode_responses(self):
        """get_redis()가 decode_responses=True로 클라이언트를 생성한다."""
        from core.redis import get_redis

        with patch("core.redis.aioredis.from_url", return_value=MagicMock()) as mock_from_url:
            get_redis()
            _, kwargs = mock_from_url.call_args
            assert kwargs.get("decode_responses") is True

    async def test_get_redis_ping_callable(self):
        """반환된 클라이언트가 ping()을 가진다 (async mock 검증)."""
        from core.redis import get_redis

        mock_client = MagicMock()
        mock_client.ping = AsyncMock(return_value=True)

        with patch("core.redis.aioredis.from_url", return_value=mock_client):
            client = get_redis()
            result = await client.ping()
            assert result is True
