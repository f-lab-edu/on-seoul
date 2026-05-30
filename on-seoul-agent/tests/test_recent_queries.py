"""core/recent_queries.py 단위 테스트."""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_redis():
    return AsyncMock()


class TestGetRecentQueries:
    async def test_returns_latest_first(self, mock_redis):
        mock_redis.lrange.return_value = [
            b"\xec\x84\xb1\xeb\x8f\x99\xea\xb5\xac\xeb\x8a\x94?",
            b"\xed\x85\x8c\xeb\x8b\x88\xec\x8a\xa4\xec\x9e\xa5 \xeb\xb3\xb4\xec\x97\xac\xec\xa4\x98",
        ]
        from core.recent_queries import get_recent_queries

        result = await get_recent_queries(room_id=1, redis=mock_redis)
        assert result == ["성동구는?", "테니스장 보여줘"]

    async def test_empty_returns_empty_list(self, mock_redis):
        mock_redis.lrange.return_value = []
        from core.recent_queries import get_recent_queries

        assert await get_recent_queries(room_id=1, redis=mock_redis) == []

    async def test_disabled_skips_redis(self, mock_redis):
        from core.config import settings
        from core.recent_queries import get_recent_queries

        with patch.object(settings, "recent_queries_enabled", False):
            assert await get_recent_queries(room_id=1, redis=mock_redis) == []
        mock_redis.lrange.assert_not_called()

    async def test_redis_error_returns_empty(self, mock_redis):
        mock_redis.lrange.side_effect = RuntimeError("down")
        from core.recent_queries import get_recent_queries

        assert await get_recent_queries(room_id=1, redis=mock_redis) == []


class TestPushRecentQuery:
    async def test_push_then_trim_then_expire(self, mock_redis):
        from core.config import settings
        from core.recent_queries import push_recent_query

        await push_recent_query(room_id=1, message="테니스장", redis=mock_redis)
        mock_redis.lpush.assert_called_once()
        mock_redis.ltrim.assert_called_once()
        trim_args = mock_redis.ltrim.call_args.args
        assert trim_args[1] == 0
        assert trim_args[2] == settings.recent_queries_max - 1
        mock_redis.expire.assert_called_once()

    async def test_disabled_skips(self, mock_redis):
        from core.config import settings
        from core.recent_queries import push_recent_query

        with patch.object(settings, "recent_queries_enabled", False):
            await push_recent_query(room_id=1, message="x", redis=mock_redis)
        mock_redis.lpush.assert_not_called()

    async def test_redis_error_does_not_raise(self, mock_redis):
        mock_redis.lpush.side_effect = RuntimeError("down")
        from core.recent_queries import push_recent_query

        await push_recent_query(room_id=1, message="x", redis=mock_redis)

    async def test_whitespace_only_message_skipped(self, mock_redis):
        from core.recent_queries import push_recent_query

        await push_recent_query(room_id=1, message="   \t\n  ", redis=mock_redis)
        mock_redis.lpush.assert_not_called()
        mock_redis.ltrim.assert_not_called()
        mock_redis.expire.assert_not_called()

    async def test_empty_message_skipped(self, mock_redis):
        from core.recent_queries import push_recent_query

        await push_recent_query(room_id=1, message="", redis=mock_redis)
        mock_redis.lpush.assert_not_called()

    async def test_message_is_stripped_before_lpush(self, mock_redis):
        from core.recent_queries import push_recent_query

        await push_recent_query(room_id=1, message="  hello  ", redis=mock_redis)
        # call_args.args = (key, value)
        assert mock_redis.lpush.call_args.args[1] == "hello"

    async def test_call_order_lpush_ltrim_expire(self, mock_redis):
        from unittest.mock import call

        from core.recent_queries import push_recent_query

        parent = AsyncMock()
        parent.lpush = mock_redis.lpush
        parent.ltrim = mock_redis.ltrim
        parent.expire = mock_redis.expire
        # attach children so parent.mock_calls records ordered calls
        manager = AsyncMock()
        manager.attach_mock(mock_redis.lpush, "lpush")
        manager.attach_mock(mock_redis.ltrim, "ltrim")
        manager.attach_mock(mock_redis.expire, "expire")

        await push_recent_query(room_id=42, message="q", redis=mock_redis)

        names = [
            c[0] for c in manager.mock_calls if c[0] in {"lpush", "ltrim", "expire"}
        ]
        assert names == ["lpush", "ltrim", "expire"]
        # LTRIM args (key, 0, max-1)
        from core.config import settings

        ltrim_args = mock_redis.ltrim.call_args.args
        assert ltrim_args[0] == "recent_queries:room:42"
        assert ltrim_args[1] == 0
        assert ltrim_args[2] == settings.recent_queries_max - 1
        # EXPIRE args (key, ttl)
        expire_args = mock_redis.expire.call_args.args
        assert expire_args[0] == "recent_queries:room:42"
        assert expire_args[1] == settings.recent_queries_ttl
        # silence unused
        del call, parent


class TestKeyFormat:
    @pytest.mark.parametrize("room_id", [0, 1, -1, 99999999])
    async def test_key_format(self, mock_redis, room_id):
        mock_redis.lrange.return_value = []
        from core.recent_queries import get_recent_queries

        await get_recent_queries(room_id=room_id, redis=mock_redis)
        from core.config import settings

        mock_redis.lrange.assert_called_once_with(
            f"recent_queries:room:{room_id}", 0, settings.recent_queries_max - 1
        )


class TestStrDecodePath:
    async def test_get_handles_str_items(self, mock_redis):
        """decode_responses=True인 Redis 클라이언트는 str을 반환한다."""
        mock_redis.lrange.return_value = ["테니스장", "공원"]
        from core.recent_queries import get_recent_queries

        result = await get_recent_queries(room_id=7, redis=mock_redis)
        assert result == ["테니스장", "공원"]

    async def test_get_handles_mixed_bytes_str(self, mock_redis):
        mock_redis.lrange.return_value = [b"\xea\xb0\x80", "나"]
        from core.recent_queries import get_recent_queries

        result = await get_recent_queries(room_id=7, redis=mock_redis)
        assert result == ["가", "나"]


class TestPushDisabledNoCalls:
    async def test_disabled_emits_no_redis_calls(self, mock_redis):
        from core.config import settings
        from core.recent_queries import push_recent_query

        with patch.object(settings, "recent_queries_enabled", False):
            await push_recent_query(room_id=1, message="hello", redis=mock_redis)
        mock_redis.lpush.assert_not_called()
        mock_redis.ltrim.assert_not_called()
        mock_redis.expire.assert_not_called()
