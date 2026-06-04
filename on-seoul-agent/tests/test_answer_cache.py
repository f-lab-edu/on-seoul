"""core/cache.py — Answer Cache 단위 테스트."""

import json
from unittest.mock import AsyncMock, patch

import pytest


@pytest.fixture
def mock_redis():
    return AsyncMock()


@pytest.fixture
def sample_payload():
    return {
        "message_id": 3,
        "answer": "테니스장 안내입니다.",
        "intent": "VECTOR_SEARCH",
        "title": None,
    }


@pytest.fixture
def sample_state():
    return {
        "refined_query": "서울 테니스장",
        "vector_results": [{"service_id": "S1"}],
        "sql_results": None,
    }


class TestCacheKey:
    def test_key_strips_and_lowercases(self):
        from core.cache import _cache_key

        assert _cache_key("  서울 테니스장 ") == _cache_key("서울 테니스장")
        assert _cache_key("서울 테니스장") == _cache_key("서울 테니스장")

    def test_different_area_produces_different_key(self):
        """동일 query라도 area_name이 다르면 다른 키여야 한다 (cross-user 회귀)."""
        from core.cache import _cache_key

        k_gangnam = _cache_key("테니스장", area_name="강남구")
        k_seongdong = _cache_key("테니스장", area_name="성동구")
        assert k_gangnam != k_seongdong

    def test_different_max_class_produces_different_key(self):
        from core.cache import _cache_key

        k_culture = _cache_key("테니스장", max_class_name="문화행사")
        k_sport = _cache_key("테니스장", max_class_name="체육시설")
        assert k_culture != k_sport

    def test_different_service_status_produces_different_key(self):
        from core.cache import _cache_key

        k_open = _cache_key("테니스장", service_status="접수중")
        k_closed = _cache_key("테니스장", service_status="마감")
        assert k_open != k_closed

    def test_none_metadata_consistent_with_empty_string(self):
        """None과 ""는 동등 — 같은 키를 산출."""
        from core.cache import _cache_key

        k_none = _cache_key(
            "테니스장", max_class_name=None, area_name=None, service_status=None
        )
        k_empty = _cache_key(
            "테니스장", max_class_name="", area_name="", service_status=""
        )
        assert k_none == k_empty

    def test_no_metadata_equals_all_none(self):
        from core.cache import _cache_key

        assert _cache_key("테니스장") == _cache_key(
            "테니스장", max_class_name=None, area_name=None, service_status=None
        )

    def test_different_payment_type_produces_different_key(self):
        """동일 query라도 payment_type이 다르면 다른 키여야 한다.

        "강남구 문화행사"와 "강남구 무료 문화행사"가 같은 키로 충돌하지 않도록 한다.
        """
        from core.cache import _cache_key

        k_none = _cache_key("강남구 문화행사", area_name="강남구")
        k_free = _cache_key("강남구 무료 문화행사", area_name="강남구", payment_type="무료")
        k_paid = _cache_key("강남구 유료 문화행사", area_name="강남구", payment_type="유료")
        assert k_none != k_free
        assert k_free != k_paid

    def test_payment_type_none_equals_empty(self):
        from core.cache import _cache_key

        assert _cache_key("테니스장", payment_type=None) == _cache_key(
            "테니스장", payment_type=""
        )


class TestGetCachedAnswer:
    async def test_miss_returns_none(self, mock_redis):
        mock_redis.get.return_value = None
        from core.cache import get_cached_answer

        assert await get_cached_answer("서울 테니스장", mock_redis) is None

    async def test_hit_returns_envelope(self, mock_redis, sample_payload, sample_state):
        envelope = {"payload": sample_payload, "state": sample_state}
        mock_redis.get.return_value = json.dumps(envelope)
        from core.cache import get_cached_answer

        result = await get_cached_answer("서울 테니스장", mock_redis)
        assert result == envelope

    async def test_disabled_skips_redis(self, mock_redis):
        from core.cache import get_cached_answer
        from core.config import settings

        with patch.object(settings, "answer_cache_enabled", False):
            assert await get_cached_answer("q", mock_redis) is None
        mock_redis.get.assert_not_called()

    async def test_redis_error_returns_none(self, mock_redis):
        mock_redis.get.side_effect = RuntimeError("redis down")
        from core.cache import get_cached_answer

        assert await get_cached_answer("q", mock_redis) is None


class TestSetCachedAnswer:
    async def test_set_stores_envelope_with_ttl(
        self, mock_redis, sample_payload, sample_state
    ):
        from core.cache import set_cached_answer
        from core.config import settings

        await set_cached_answer(
            "서울 테니스장", sample_payload, sample_state, mock_redis
        )
        mock_redis.set.assert_called_once()
        kwargs = mock_redis.set.call_args.kwargs
        assert kwargs["ex"] == settings.answer_cache_ttl
        body = json.loads(mock_redis.set.call_args.args[1])
        assert body["payload"] == sample_payload
        assert body["state"] == sample_state

    async def test_empty_results_uses_short_ttl(self, mock_redis, sample_payload):
        empty_state = {"refined_query": "x", "vector_results": [], "sql_results": []}
        from core.cache import set_cached_answer
        from core.config import settings

        await set_cached_answer("x", sample_payload, empty_state, mock_redis)
        assert mock_redis.set.call_args.kwargs["ex"] == settings.answer_cache_empty_ttl

    async def test_disabled_skips_set(self, mock_redis, sample_payload, sample_state):
        from core.cache import set_cached_answer
        from core.config import settings

        with patch.object(settings, "answer_cache_enabled", False):
            await set_cached_answer("q", sample_payload, sample_state, mock_redis)
        mock_redis.set.assert_not_called()

    async def test_redis_error_does_not_raise(
        self, mock_redis, sample_payload, sample_state
    ):
        mock_redis.set.side_effect = RuntimeError("redis down")
        from core.cache import set_cached_answer

        await set_cached_answer(
            "q", sample_payload, sample_state, mock_redis
        )  # no raise

    async def test_set_serializes_non_json_native_types_in_service_cards(
        self, mock_redis, sample_state
    ):
        """회귀: payload.service_cards 가 datetime/date/Decimal 을 담아도 SET 이 깨지지 않는다.

        set_cached_answer 는 json.dumps(default=str) 로 직렬화하므로, DB numeric
        (Decimal) / timestamp (datetime) / date 컬럼이 service_cards 를 통해
        흘러들어와도 TypeError 없이 문자열로 폴백되어야 한다. 폴백이 깨지면
        캐시 쓰기가 조용히 실패(except 삼킴)해 cache hit 률이 0 이 된다.
        """
        import datetime as _dt
        from decimal import Decimal

        from core.cache import set_cached_answer

        payload = {
            "message_id": 7,
            "answer": "안내",
            "intent": "VECTOR_SEARCH",
            "title": None,
            "service_cards": [
                {
                    "service_id": "S1",
                    "receipt_start_dt": _dt.datetime(2025, 11, 1, 9, 0, 0),
                    "open_date": _dt.date(2025, 12, 31),
                    "fee": Decimal("3000.50"),
                }
            ],
        }

        await set_cached_answer("q", payload, sample_state, mock_redis)

        mock_redis.set.assert_called_once()
        # 직렬화가 깨지지 않고 호출됐으며, 비-네이티브 타입은 문자열로 폴백된다.
        body = json.loads(mock_redis.set.call_args.args[1])
        card = body["payload"]["service_cards"][0]
        assert card["receipt_start_dt"].startswith("2025-11-01")
        assert card["open_date"] == "2025-12-31"
        assert card["fee"] == "3000.50"


class TestEmptyStateTTL:
    async def test_both_none_uses_empty_ttl(self, mock_redis, sample_payload):
        """vector_results=None, sql_results=None 도 empty 로 인식."""
        none_state = {"refined_query": "x", "vector_results": None, "sql_results": None}
        from core.cache import set_cached_answer
        from core.config import settings

        await set_cached_answer("x", sample_payload, none_state, mock_redis)
        assert mock_redis.set.call_args.kwargs["ex"] == settings.answer_cache_empty_ttl

    async def test_vector_empty_sql_present_uses_normal_ttl(
        self, mock_redis, sample_payload
    ):
        """한쪽만 empty 면 정상 TTL."""
        asym_state = {
            "refined_query": "x",
            "vector_results": [],
            "sql_results": [{"a": 1}],
        }
        from core.cache import set_cached_answer
        from core.config import settings

        await set_cached_answer("x", sample_payload, asym_state, mock_redis)
        assert mock_redis.set.call_args.kwargs["ex"] == settings.answer_cache_ttl

    async def test_sql_empty_vector_present_uses_normal_ttl(
        self, mock_redis, sample_payload
    ):
        asym_state = {
            "refined_query": "x",
            "vector_results": [{"service_id": "S1"}],
            "sql_results": [],
        }
        from core.cache import set_cached_answer
        from core.config import settings

        await set_cached_answer("x", sample_payload, asym_state, mock_redis)
        assert mock_redis.set.call_args.kwargs["ex"] == settings.answer_cache_ttl


class TestFlush:
    async def test_flush_scans_and_deletes(self, mock_redis):
        async def _scan_iter(match):
            for k in [b"answer_cache:aaa", b"answer_cache:bbb"]:
                yield k

        mock_redis.scan_iter = _scan_iter
        from core.cache import flush_answer_cache

        deleted = await flush_answer_cache(mock_redis)
        assert deleted == 2
        mock_redis.delete.assert_called()

    async def test_flush_batches_at_500(self, mock_redis):
        """500개 초과 시 배치로 나뉘어 delete 호출."""

        async def _scan_iter(match):
            for i in range(750):
                yield f"answer_cache:{i:04d}".encode()

        mock_redis.scan_iter = _scan_iter
        from core.cache import flush_answer_cache

        deleted = await flush_answer_cache(mock_redis)
        assert deleted == 750
        assert mock_redis.delete.call_count == 2

    async def test_flush_empty_no_delete_call(self, mock_redis):
        """매칭 키 0개면 delete 호출 없이 0 반환."""

        async def _scan_iter(match):
            if False:
                yield None

        mock_redis.scan_iter = _scan_iter
        from core.cache import flush_answer_cache

        deleted = await flush_answer_cache(mock_redis)
        assert deleted == 0
        mock_redis.delete.assert_not_called()

    async def test_flush_redis_error_returns_zero(self, mock_redis):
        """scan_iter 도중 예외 발생해도 0 반환, raise 없음 (fail-open)."""

        def _scan_iter(match):
            raise RuntimeError("redis down")

        mock_redis.scan_iter = _scan_iter
        from core.cache import flush_answer_cache

        deleted = await flush_answer_cache(mock_redis)
        assert deleted == 0

    async def test_flush_logs_structured_event(self, mock_redis, caplog):
        """flush 완료 시 cache.flush deleted=N 구조화 로그가 한 번 기록된다."""
        import logging

        async def _scan_iter(match):
            for k in [b"answer_cache:aaa", b"answer_cache:bbb"]:
                yield k

        mock_redis.scan_iter = _scan_iter
        from core.cache import flush_answer_cache

        # setup_logging()이 core.* propagate=False를 설정할 수 있으므로
        # caplog.handler를 직접 core.cache 로거에 연결한다.
        cache_logger = logging.getLogger("core.cache")
        prev_propagate = cache_logger.propagate
        cache_logger.addHandler(caplog.handler)
        cache_logger.propagate = True
        try:
            with caplog.at_level(logging.INFO, logger="core.cache"):
                await flush_answer_cache(mock_redis)
        finally:
            cache_logger.removeHandler(caplog.handler)
            cache_logger.propagate = prev_propagate
        flush_logs = [r for r in caplog.records if "cache.flush" in r.getMessage()]
        assert len(flush_logs) == 1
        assert "deleted=2" in flush_logs[0].getMessage()
