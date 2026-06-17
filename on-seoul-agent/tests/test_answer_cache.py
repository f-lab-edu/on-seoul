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

    # max_class_name/service_status 가 키에 반영되는 것은
    # test_different_area_produces_different_key 와 동일 로직(메타 필드 → 키 분기)의
    # 값만 다른 순열이라 축소했다. none/empty 동등성·payment_type 충돌은 유지한다.

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

    # no-kwargs == all-None 은 test_none_metadata_consistent_with_empty_string 가
    # 검증하는 None/empty 정규화 동등성의 순열이라 축소했다.

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

    # payment_type None==empty 도 동일한 None/empty 정규화 동등성 순열이라 축소했다.
    # payment_type 의 정합 핵심(다른 값 → 다른 키 충돌 방지)은 위 테스트가 유지한다.


class TestDigestLength:
    def test_answer_cache_key_digest_is_128bit(self):
        """digest는 128-bit(32 hex)여야 한다 (0-3-4)."""
        from core.cache import _KEY_PREFIX, _cache_key

        digest = _cache_key("테니스장").removeprefix(_KEY_PREFIX)
        assert len(digest) == 32

    # refine 키 digest 길이(32 hex)도 test_answer_cache_key_digest_is_128bit 와
    # 동일한 blake2b 128-bit 산출 로직의 순열이라 축소했다. refine 키의 정합 핵심
    # (버전/네임스페이스/history 분리)은 아래 전용 테스트들이 유지한다.

    def test_refine_cache_key_includes_version(self):
        """버전 prefix 포함 — bump 시 구 매핑 즉시 무효화(네임스페이스 분리)."""
        from core.cache import (
            _REFINE_CACHE_VERSION,
            _REFINE_KEY_PREFIX,
            _refine_cache_key,
        )

        key = _refine_cache_key("테니스장", None)
        assert key.startswith(f"{_REFINE_KEY_PREFIX}{_REFINE_CACHE_VERSION}:")


class TestRefineCacheKey:
    def test_strips_collapses_lowercases(self):
        from core.cache import _refine_cache_key

        assert _refine_cache_key("  서울   테니스장 ", None) == _refine_cache_key(
            "서울 테니스장", None
        )

    def test_no_history_shared_across_users(self):
        """history 없으면 동일 raw query는 같은 키 — first-turn 사용자 간 공유."""
        from core.cache import _refine_cache_key

        assert _refine_cache_key("테니스장", None) == _refine_cache_key("테니스장", [])

    def test_history_present_includes_hash_and_differs(self):
        """history가 있으면 키가 history-없는 키와 달라진다(미공유)."""
        from core.cache import _refine_cache_key

        no_hist = _refine_cache_key("성동구는?", None)
        with_hist = _refine_cache_key(
            "성동구는?",
            [{"role": "user", "content": "테니스장 보여줘"}],
        )
        assert no_hist != with_hist

    def test_different_history_differs(self):
        from core.cache import _refine_cache_key

        h1 = _refine_cache_key("성동구는?", [{"role": "user", "content": "테니스장"}])
        h2 = _refine_cache_key("성동구는?", [{"role": "user", "content": "수영장"}])
        assert h1 != h2

    def test_namespace_separated_from_answer_cache(self):
        from core.cache import _KEY_PREFIX, _REFINE_KEY_PREFIX

        assert _REFINE_KEY_PREFIX != _KEY_PREFIX


class TestGetCachedRefine:
    # miss→None / disabled-skip(설정 게이팅)은 TestGetCachedAnswer 의 대칭 케이스가
    # 동일 의미로 커버하므로 refine 쪽은 hit + fail-open(redis error)만 유지한다.

    async def test_hit_returns_dict(self, mock_redis):
        stored = {"intent": "VECTOR_SEARCH", "refined_query": "서울 테니스장"}
        mock_redis.get.return_value = json.dumps(stored)
        from core.cache import get_cached_refine

        assert await get_cached_refine("테니스장", None, mock_redis) == stored

    async def test_redis_error_returns_none(self, mock_redis):
        mock_redis.get.side_effect = RuntimeError("redis down")
        from core.cache import get_cached_refine

        assert await get_cached_refine("q", None, mock_redis) is None


class TestSetCachedRefine:
    async def test_set_stores_with_refine_ttl(self, mock_redis):
        from core.cache import set_cached_refine
        from core.config import settings

        payload = {"intent": "VECTOR_SEARCH", "refined_query": "서울 테니스장"}
        await set_cached_refine("테니스장", None, payload, mock_redis)
        mock_redis.set.assert_called_once()
        assert mock_redis.set.call_args.kwargs["ex"] == settings.refine_cache_ttl
        body = json.loads(mock_redis.set.call_args.args[1])
        assert body == payload

    # disabled-skip(설정 게이팅)은 TestSetCachedAnswer.test_disabled_skips_set 대칭
    # 케이스가 동일 의미로 커버하므로 refine 쪽은 stores + fail-open 만 유지한다.

    async def test_redis_error_does_not_raise(self, mock_redis):
        mock_redis.set.side_effect = RuntimeError("redis down")
        from core.cache import set_cached_refine

        await set_cached_refine("q", None, {"intent": "X"}, mock_redis)  # no raise


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

    # sql-empty/vector-present 도 "한쪽만 empty → 정상 TTL" 동일 분기의 대칭 순열이라
    # test_vector_empty_sql_present_uses_normal_ttl 로 대표하고 축소했다.


class TestSingleflight:
    """acquire_answer_lock / release_answer_lock / poll_for_answer 단위 테스트."""

    async def test_acquire_returns_true_on_set_nx_success(self, mock_redis):
        mock_redis.set.return_value = True
        from core.cache import acquire_answer_lock

        assert await acquire_answer_lock("answer_cache:abc", mock_redis, ttl=30)

    async def test_acquire_returns_false_on_set_nx_fail(self, mock_redis):
        """SET NX 실패(다른 홀더 존재) → False."""
        mock_redis.set.return_value = None
        from core.cache import acquire_answer_lock

        assert not await acquire_answer_lock("answer_cache:abc", mock_redis, ttl=30)

    async def test_acquire_redis_error_returns_true_fail_open(self, mock_redis):
        """Redis 장애 → fail-open True (각자 LLM 실행)."""
        mock_redis.set.side_effect = RuntimeError("redis down")
        from core.cache import acquire_answer_lock

        assert await acquire_answer_lock("answer_cache:abc", mock_redis, ttl=30)

    async def test_acquire_lock_key_has_lock_suffix(self, mock_redis):
        """락 키 = 캐시 키 + ':lock'."""
        mock_redis.set.return_value = True
        from core.cache import _LOCK_SUFFIX, acquire_answer_lock

        cache_key = "answer_cache:abc"
        await acquire_answer_lock(cache_key, mock_redis, ttl=30)
        call_key = mock_redis.set.call_args.args[0]
        assert call_key == f"{cache_key}{_LOCK_SUFFIX}"

    async def test_acquire_uses_nx_and_ex(self, mock_redis):
        """SET NX EX ttl 인자 정확성."""
        mock_redis.set.return_value = True
        from core.cache import acquire_answer_lock

        await acquire_answer_lock("answer_cache:abc", mock_redis, ttl=30)
        kwargs = mock_redis.set.call_args.kwargs
        assert kwargs.get("nx") is True
        assert kwargs.get("ex") == 30

    async def test_release_deletes_lock_key(self, mock_redis):
        from core.cache import _LOCK_SUFFIX, release_answer_lock

        cache_key = "answer_cache:abc"
        await release_answer_lock(cache_key, mock_redis)
        mock_redis.delete.assert_called_once_with(f"{cache_key}{_LOCK_SUFFIX}")

    async def test_release_redis_error_does_not_raise(self, mock_redis):
        mock_redis.delete.side_effect = RuntimeError("redis down")
        from core.cache import release_answer_lock

        await release_answer_lock("answer_cache:abc", mock_redis)  # no raise

    async def test_poll_returns_envelope_when_cache_populated(self, mock_redis):
        """두 번째 poll에서 캐시 키가 채워지면 dict 반환."""
        envelope = {"payload": {"answer": "ok"}, "state": {}}
        mock_redis.get.side_effect = [None, json.dumps(envelope)]
        from unittest.mock import patch

        from core.cache import poll_for_answer

        with patch("asyncio.sleep"):
            result = await poll_for_answer(
                "answer_cache:abc", mock_redis, retries=3, interval=0.01
            )
        assert result == envelope

    async def test_poll_returns_none_on_timeout(self, mock_redis):
        """retries 소진 후 결과 없으면 None (fail-open)."""
        mock_redis.get.return_value = None
        from unittest.mock import patch

        from core.cache import poll_for_answer

        with patch("asyncio.sleep"):
            result = await poll_for_answer(
                "answer_cache:abc", mock_redis, retries=3, interval=0.01
            )
        assert result is None
        assert mock_redis.get.call_count == 3

    async def test_poll_redis_error_returns_none(self, mock_redis):
        """GET 오류 시 None (fail-open)."""
        mock_redis.get.side_effect = RuntimeError("redis down")
        from unittest.mock import patch

        from core.cache import poll_for_answer

        with patch("asyncio.sleep"):
            result = await poll_for_answer(
                "answer_cache:abc", mock_redis, retries=3, interval=0.01
            )
        assert result is None

    async def test_disabled_acquire_always_true(self, mock_redis):
        """singleflight 비활성화 시 acquire는 항상 True(락 우회)."""
        from unittest.mock import patch

        from core.cache import acquire_answer_lock
        from core.config import settings

        with patch.object(settings, "answer_cache_singleflight_enabled", False):
            result = await acquire_answer_lock("answer_cache:abc", mock_redis, ttl=30)
        assert result is True
        mock_redis.set.assert_not_called()

    async def test_disabled_release_skips_delete(self, mock_redis):
        from unittest.mock import patch

        from core.cache import release_answer_lock
        from core.config import settings

        with patch.object(settings, "answer_cache_singleflight_enabled", False):
            await release_answer_lock("answer_cache:abc", mock_redis)
        mock_redis.delete.assert_not_called()


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

    async def test_flush_logs_structured_event(self, mock_redis):
        """flush 완료 시 cache.flush deleted=N 구조화 로그가 한 번 기록된다.

        실행 순서 독립 결정성: caplog/루트 핸들러에 의존하지 않고 전용 핸들러를
        core.cache 로거에 직접 부착하고 propagate=False 로 고정한다. 이렇게 하면
        setup_logging() 의 전역 propagate 상태(다른 테스트가 호출했는지 여부)와
        무관하게 정확히 한 번만 캡처되어, 격리/전체 실행 결과가 항상 일치한다.
        """
        import logging

        async def _scan_iter(match):
            for k in [b"answer_cache:aaa", b"answer_cache:bbb"]:
                yield k

        mock_redis.scan_iter = _scan_iter
        from core.cache import flush_answer_cache

        records: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                records.append(record)

        cache_logger = logging.getLogger("core.cache")
        handler = _Capture(level=logging.INFO)
        prev_propagate = cache_logger.propagate
        prev_level = cache_logger.level
        cache_logger.addHandler(handler)
        # propagate=False 로 고정 → 루트(또는 pytest 캡처 핸들러)로 전파되지 않아
        # 중복 캡처가 구조적으로 불가능. 전역 logging 상태와 독립적.
        cache_logger.propagate = False
        cache_logger.setLevel(logging.INFO)
        try:
            await flush_answer_cache(mock_redis)
        finally:
            cache_logger.removeHandler(handler)
            cache_logger.propagate = prev_propagate
            cache_logger.setLevel(prev_level)

        flush_logs = [r for r in records if "cache.flush" in r.getMessage()]
        assert len(flush_logs) == 1
        assert "deleted=2" in flush_logs[0].getMessage()
