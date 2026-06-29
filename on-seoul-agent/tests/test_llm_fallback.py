"""모델 티어 fallback 테스트 — llm/client.py _FallbackChatModel.

primary 모델 호출이 *일시적 오류*로 실패하면 fallback 모델로 자동 재시도한다.
이는 벤더(provider) fallback과 별개의 "모델 티어 fallback"이며 항상 Gemini로 빌드한다.

검증:
(1) primary가 일시적 예외를 던지면 fallback이 호출되어 결과를 반환한다(_agenerate / raw 파이프 경로).
(2) with_structured_output 경로에서도 fallback 합성이 일어난다.
(3) 비일시적 예외(ConfigurationException류)는 fallback 없이 그대로 전파된다.
(4) llm_fallback_enabled=False면 raw primary 모델을 반환한다.

실제 API 호출 없이 mock으로만 실행된다.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from google.api_core.exceptions import ServiceUnavailable
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from core.exceptions import ConfigurationException
from llm.client import _TRANSIENT_EXC, _FallbackChatModel, get_chat_model


def _chat_result(text: str) -> ChatResult:
    return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


def _make_wrapper() -> tuple[_FallbackChatModel, MagicMock, MagicMock]:
    """primary/fallback이 모두 MagicMock인 _FallbackChatModel을 만든다."""
    primary = MagicMock(name="primary")
    fallback = MagicMock(name="fallback")
    wrapper = _FallbackChatModel(primary=primary, fallback=fallback)
    return wrapper, primary, fallback


# ---------------------------------------------------------------------------
# (4) llm_fallback_enabled 플래그
# ---------------------------------------------------------------------------


class TestFallbackFlag:
    def test_disabled_returns_raw_primary(self):
        """llm_fallback_enabled=False면 raw ChatGoogleGenerativeAI를 반환한다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI") as mock_cls,
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"
            mock_settings.llm_fallback_enabled = False

            result = get_chat_model(provider="gemini")

            assert result is mock_cls.return_value
            assert not isinstance(result, _FallbackChatModel)

    def test_enabled_returns_fallback_wrapper(self):
        """llm_fallback_enabled=True면 _FallbackChatModel 래퍼를 반환한다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI"),
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"
            mock_settings.gemini_fallback_model = "gemini-3.1-flash-lite"
            mock_settings.llm_fallback_enabled = True

            result = get_chat_model(provider="gemini")

            assert isinstance(result, _FallbackChatModel)

    def test_fallback_is_built_with_fallback_model(self):
        """fallback 모델은 settings.gemini_fallback_model로 빌드된다."""
        models_seen: list[str] = []

        def _capture(*args, **kwargs):
            models_seen.append(kwargs.get("model"))
            return MagicMock()

        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI", side_effect=_capture),
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"
            mock_settings.gemini_fallback_model = "gemini-3.1-flash-lite"
            mock_settings.llm_fallback_enabled = True

            get_chat_model(provider="gemini")

        assert "gemini-2.0-flash" in models_seen
        assert "gemini-3.1-flash-lite" in models_seen

    def test_openai_primary_gets_gemini_fallback(self):
        """primary가 openai여도 fallback은 항상 Gemini provider로 빌드된다."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatOpenAI") as mock_openai,
            patch("llm.client.ChatGoogleGenerativeAI") as mock_gemini,
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = "fake-openai-key"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gpt_model = "gpt-4o-mini"
            mock_settings.gemini_fallback_model = "gemini-3.1-flash-lite"
            mock_settings.llm_http_max_connections = 400
            mock_settings.llm_fallback_enabled = True

            result = get_chat_model(provider="openai")

            assert isinstance(result, _FallbackChatModel)
            assert result._primary is mock_openai.return_value
            assert result._fallback is mock_gemini.return_value

    def test_no_google_key_falls_back_to_primary_only(self):
        """fallback이 Gemini인데 google_api_key가 없으면 경고 후 raw primary만 반환(크래시 금지)."""
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatOpenAI") as mock_openai,
            patch("llm.client.ChatGoogleGenerativeAI") as mock_gemini,
        ):
            mock_settings.llm_provider = "openai"
            mock_settings.openai_api_key = "fake-openai-key"
            mock_settings.google_api_key = None
            mock_settings.gpt_model = "gpt-4o-mini"
            mock_settings.gemini_fallback_model = "gemini-3.1-flash-lite"
            mock_settings.llm_http_max_connections = 400
            mock_settings.llm_fallback_enabled = True

            result = get_chat_model(provider="openai")

            assert result is mock_openai.return_value
            assert not isinstance(result, _FallbackChatModel)
            mock_gemini.assert_not_called()

    def test_no_infinite_wrapping_primary_and_fallback_are_raw(self):
        """(d) primary·fallback 둘 다 raw 모델이어야 한다(_FallbackChatModel 중첩 금지).

        fallback은 get_chat_model 재귀가 아니라 _build_chat_model로 직접 빌드되므로,
        래퍼 안에 또 다른 래퍼가 들어가는 무한/중첩 래핑이 발생하지 않는다.
        """
        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI") as mock_gemini,
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"
            mock_settings.gemini_fallback_model = "gemini-3.1-flash-lite"
            mock_settings.llm_fallback_enabled = True

            result = get_chat_model(provider="gemini")

            assert isinstance(result, _FallbackChatModel)
            assert not isinstance(result._primary, _FallbackChatModel)
            assert not isinstance(result._fallback, _FallbackChatModel)
            # 두 모델 모두 raw 빌더가 만든 ChatGoogleGenerativeAI 인스턴스다.
            assert result._primary is mock_gemini.return_value
            assert result._fallback is mock_gemini.return_value
            # ChatGoogleGenerativeAI는 정확히 2회(primary 1 + fallback 1)만 생성된다.
            assert mock_gemini.call_count == 2

    def test_primary_equals_fallback_model_builds_two_separate_raw_instances(self):
        """(f) primary 모델명 == fallback 모델명이어도 무한루프 없이 동작한다.

        같은 모델명이어도 _build_chat_model을 각각 호출해 별도 raw 인스턴스 2개를
        만들 뿐이며, 위임은 _FallbackChatModel._agenerate가 fallback(raw)을 1회
        호출하는 것으로 끝난다(재귀 없음).
        """
        built_models: list[str] = []

        def _capture(*args, **kwargs):
            built_models.append(kwargs.get("model"))
            return MagicMock(name=f"gemini[{kwargs.get('model')}]")

        with (
            patch("llm.client.settings") as mock_settings,
            patch("llm.client.ChatGoogleGenerativeAI", side_effect=_capture),
        ):
            mock_settings.llm_provider = "gemini"
            mock_settings.google_api_key = "fake-google-key"
            mock_settings.gemini_model = "gemini-2.0-flash"
            # primary == fallback 모델명
            mock_settings.gemini_fallback_model = "gemini-2.0-flash"
            mock_settings.llm_fallback_enabled = True

            result = get_chat_model(provider="gemini")

            assert isinstance(result, _FallbackChatModel)
            # 두 번 빌드되며, 둘 다 같은 모델명이지만 서로 다른 인스턴스다.
            assert built_models == ["gemini-2.0-flash", "gemini-2.0-flash"]
            assert result._primary is not result._fallback
            assert not isinstance(result._fallback, _FallbackChatModel)

    async def test_primary_equals_fallback_no_recursion_on_transient(self):
        """(f) 같은 모델명이어도 일시적 오류 시 fallback이 단 1회만 호출된다(재귀 없음)."""
        wrapper, primary, fallback = _make_wrapper()
        primary._agenerate = AsyncMock(side_effect=ServiceUnavailable("503"))
        fallback._agenerate = AsyncMock(return_value=_chat_result("fb-once"))

        result = await wrapper._agenerate([])

        primary._agenerate.assert_awaited_once()
        fallback._agenerate.assert_awaited_once()
        assert result.generations[0].message.content == "fb-once"

    async def test_fallback_transient_error_propagates(self):
        """fallback도 일시적 오류를 던지면(2단 실패) 더 이상 재시도하지 않고 전파된다."""
        wrapper, primary, fallback = _make_wrapper()
        primary._agenerate = AsyncMock(side_effect=ServiceUnavailable("503-primary"))
        fallback._agenerate = AsyncMock(side_effect=ServiceUnavailable("503-fallback"))

        with pytest.raises(ServiceUnavailable, match="503-fallback"):
            await wrapper._agenerate([])

        primary._agenerate.assert_awaited_once()
        fallback._agenerate.assert_awaited_once()


# ---------------------------------------------------------------------------
# (1) _agenerate / raw 파이프 경로의 fallback 위임
# ---------------------------------------------------------------------------


class TestAgenerateFallback:
    async def test_transient_error_delegates_to_fallback(self):
        """primary가 일시적 예외를 던지면 fallback._agenerate가 호출되어 결과를 반환한다."""
        wrapper, primary, fallback = _make_wrapper()
        primary._agenerate = AsyncMock(side_effect=ServiceUnavailable("503"))
        fallback._agenerate = AsyncMock(return_value=_chat_result("fallback-answer"))

        result = await wrapper._agenerate([])

        primary._agenerate.assert_awaited_once()
        fallback._agenerate.assert_awaited_once()
        assert result.generations[0].message.content == "fallback-answer"

    async def test_primary_success_does_not_call_fallback(self):
        """primary 성공 시 fallback은 호출되지 않는다."""
        wrapper, primary, fallback = _make_wrapper()
        primary._agenerate = AsyncMock(return_value=_chat_result("primary-answer"))
        fallback._agenerate = AsyncMock(return_value=_chat_result("fallback-answer"))

        result = await wrapper._agenerate([])

        primary._agenerate.assert_awaited_once()
        fallback._agenerate.assert_not_awaited()
        assert result.generations[0].message.content == "primary-answer"

    async def test_non_transient_error_propagates(self):
        """비일시적 예외는 fallback 없이 그대로 전파된다."""
        wrapper, primary, fallback = _make_wrapper()
        primary._agenerate = AsyncMock(side_effect=ConfigurationException("bad config"))
        fallback._agenerate = AsyncMock(return_value=_chat_result("fallback-answer"))

        with pytest.raises(ConfigurationException, match="bad config"):
            await wrapper._agenerate([])

        fallback._agenerate.assert_not_awaited()

    async def test_value_error_propagates(self):
        """일반 ValueError(비일시적)도 fallback 없이 전파된다."""
        wrapper, primary, fallback = _make_wrapper()
        primary._agenerate = AsyncMock(side_effect=ValueError("oops"))
        fallback._agenerate = AsyncMock(return_value=_chat_result("x"))

        with pytest.raises(ValueError, match="oops"):
            await wrapper._agenerate([])

        fallback._agenerate.assert_not_awaited()


class TestGenerateFallback:
    def test_sync_transient_error_delegates_to_fallback(self):
        """동기 _generate도 일시적 예외 시 fallback에 위임한다."""
        wrapper, primary, fallback = _make_wrapper()
        primary._generate = MagicMock(side_effect=ServiceUnavailable("503"))
        fallback._generate = MagicMock(return_value=_chat_result("fb"))

        result = wrapper._generate([])

        primary._generate.assert_called_once()
        fallback._generate.assert_called_once()
        assert result.generations[0].message.content == "fb"

    def test_sync_non_transient_propagates(self):
        wrapper, primary, fallback = _make_wrapper()
        primary._generate = MagicMock(side_effect=ValueError("nope"))
        fallback._generate = MagicMock(return_value=_chat_result("fb"))

        with pytest.raises(ValueError, match="nope"):
            wrapper._generate([])

        fallback._generate.assert_not_called()


# ---------------------------------------------------------------------------
# (2) with_structured_output 합성
# ---------------------------------------------------------------------------


class TestStructuredOutputComposition:
    def test_with_structured_output_composes_fallbacks(self):
        """primary.with_structured_output(...).with_fallbacks([fallback...]) 합성을 검증한다."""
        wrapper, primary, fallback = _make_wrapper()

        primary_runnable = MagicMock(name="primary_structured")
        fallback_runnable = MagicMock(name="fallback_structured")
        composed = MagicMock(name="composed")
        primary_runnable.with_fallbacks.return_value = composed
        primary.with_structured_output.return_value = primary_runnable
        fallback.with_structured_output.return_value = fallback_runnable

        schema = MagicMock(name="Schema")
        result = wrapper.with_structured_output(schema)

        primary.with_structured_output.assert_called_once_with(schema)
        fallback.with_structured_output.assert_called_once_with(schema)
        primary_runnable.with_fallbacks.assert_called_once()
        # fallback 합성에 fallback structured runnable이 포함된다
        args, kwargs = primary_runnable.with_fallbacks.call_args
        assert fallback_runnable in args[0]
        # 좁은 예외 집합으로 제한된다
        assert kwargs["exceptions_to_handle"] == _TRANSIENT_EXC
        assert result is composed

    def test_with_structured_output_forwards_kwargs(self):
        """추가 kwargs가 양쪽 모델에 전달된다."""
        wrapper, primary, fallback = _make_wrapper()
        primary.with_structured_output.return_value.with_fallbacks.return_value = (
            MagicMock()
        )
        fallback.with_structured_output.return_value = MagicMock()

        schema = MagicMock()
        wrapper.with_structured_output(schema, method="json_mode")

        primary.with_structured_output.assert_called_once_with(
            schema, method="json_mode"
        )
        fallback.with_structured_output.assert_called_once_with(
            schema, method="json_mode"
        )

    def test_bind_tools_composes_fallbacks(self):
        """(대칭성) bind_tools도 동일 패턴으로 합성되고 예외 집합이 좁혀진다."""
        wrapper, primary, fallback = _make_wrapper()

        primary_bound = MagicMock(name="primary_bound")
        fallback_bound = MagicMock(name="fallback_bound")
        composed = MagicMock(name="composed")
        primary_bound.with_fallbacks.return_value = composed
        primary.bind_tools.return_value = primary_bound
        fallback.bind_tools.return_value = fallback_bound

        tools = [MagicMock(name="tool")]
        result = wrapper.bind_tools(tools)

        primary.bind_tools.assert_called_once_with(tools)
        fallback.bind_tools.assert_called_once_with(tools)
        args, kwargs = primary_bound.with_fallbacks.call_args
        assert fallback_bound in args[0]
        assert kwargs["exceptions_to_handle"] == _TRANSIENT_EXC
        assert result is composed


# ---------------------------------------------------------------------------
# 전이 예외 집합 / 메타데이터
# ---------------------------------------------------------------------------


class TestTransientExcSet:
    def test_includes_google_transient(self):
        from google.api_core.exceptions import (
            DeadlineExceeded,
            InternalServerError,
            ResourceExhausted,
            ServiceUnavailable,
        )

        for exc in (
            ResourceExhausted,
            ServiceUnavailable,
            InternalServerError,
            DeadlineExceeded,
        ):
            assert exc in _TRANSIENT_EXC

    def test_includes_httpx_transient(self):
        import httpx

        assert httpx.TimeoutException in _TRANSIENT_EXC
        assert httpx.TransportError in _TRANSIENT_EXC

    def test_includes_rate_limit_and_parser_exc(self):
        from langchain_core.exceptions import OutputParserException

        from core.exceptions import RateLimitException

        assert RateLimitException in _TRANSIENT_EXC
        assert OutputParserException in _TRANSIENT_EXC

    def test_excludes_non_transient(self):
        """ConfigurationException / ValueError 같은 비일시적 예외는 포함하지 않는다."""
        assert ConfigurationException not in _TRANSIENT_EXC
        assert ValueError not in _TRANSIENT_EXC

    def test_llm_type_property(self):
        wrapper, _, _ = _make_wrapper()
        assert isinstance(wrapper._llm_type, str)
        assert wrapper._llm_type
