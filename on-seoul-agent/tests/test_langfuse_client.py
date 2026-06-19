"""core/langfuse_client.py 단위 테스트.

핵심 보증:
- langfuse_enabled=False (기본) 또는 키 미설정 → 완전 no-op (None 반환). Langfuse/CallbackHandler 미생성.
- langfuse_enabled=True + 키 설정 → CallbackHandler 반환, get_langfuse_handler() 로 접근 가능.
- 초기화 예외 → fail-open (None 반환, 예외 전파 안 됨).
- shutdown_langfuse() → flush + shutdown best-effort, 예외 무시.
- 실 네트워크 전송은 전혀 일어나지 않도록 Langfuse/CallbackHandler 진입점을 모킹한다.
"""

from unittest.mock import MagicMock, patch

import pytest

import core.langfuse_client as lf


@pytest.fixture(autouse=True)
def _isolate_langfuse_state():
    """각 테스트 후 모듈 전역 상태를 원복한다."""
    yield
    lf.shutdown_langfuse()
    lf._CLIENT = None
    lf._HANDLER = None


def _make_settings(**overrides):
    base = {
        "langfuse_enabled": False,
        "langfuse_public_key": "pk-test",
        "langfuse_secret_key": "sk-test",
        "langfuse_host": "https://cloud.langfuse.com",
        "otel_environment": "test",
        "app_version": "0.1.0",
    }
    base.update(overrides)
    s = MagicMock()
    for k, v in base.items():
        setattr(s, k, v)
    return s


def test_init_langfuse_disabled_is_noop():
    """langfuse_enabled=False → Langfuse/CallbackHandler 미생성, None 반환."""
    with (
        patch.object(lf, "settings", _make_settings(langfuse_enabled=False)),
        patch.object(lf, "Langfuse") as client_cls,
        patch.object(lf, "CallbackHandler") as handler_cls,
    ):
        result = lf.init_langfuse()

    assert result is None
    client_cls.assert_not_called()
    handler_cls.assert_not_called()
    assert lf.get_langfuse_handler() is None


@pytest.mark.parametrize("blank_key", ["langfuse_public_key", "langfuse_secret_key"])
def test_init_langfuse_blank_key_is_noop(blank_key):
    """키 미설정 시 enabled=True여도 no-op (fail-open)."""
    with (
        patch.object(
            lf, "settings", _make_settings(langfuse_enabled=True, **{blank_key: ""})
        ),
        patch.object(lf, "Langfuse") as client_cls,
        patch.object(lf, "CallbackHandler") as handler_cls,
    ):
        result = lf.init_langfuse()

    assert result is None
    client_cls.assert_not_called()
    handler_cls.assert_not_called()


def test_init_langfuse_enabled_returns_handler():
    """enabled=True + 키 설정 → Langfuse 클라이언트 + CallbackHandler 생성."""
    fake_handler = MagicMock(name="handler")
    with (
        patch.object(lf, "settings", _make_settings(langfuse_enabled=True)),
        patch.object(lf, "Langfuse") as client_cls,
        patch.object(lf, "CallbackHandler", return_value=fake_handler) as handler_cls,
    ):
        result = lf.init_langfuse()

    assert result is fake_handler
    client_cls.assert_called_once()
    # 키/호스트가 클라이언트로 전달되는지 확인.
    kwargs = client_cls.call_args.kwargs
    assert kwargs["public_key"] == "pk-test"
    assert kwargs["secret_key"] == "sk-test"
    assert kwargs["host"] == "https://cloud.langfuse.com"
    handler_cls.assert_called_once()
    assert lf.get_langfuse_handler() is fake_handler


def test_init_langfuse_idempotent():
    """이미 초기화되면 재사용 (CallbackHandler 1회만 생성)."""
    fake_handler = MagicMock(name="handler")
    with (
        patch.object(lf, "settings", _make_settings(langfuse_enabled=True)),
        patch.object(lf, "Langfuse"),
        patch.object(lf, "CallbackHandler", return_value=fake_handler) as handler_cls,
    ):
        first = lf.init_langfuse()
        second = lf.init_langfuse()

    assert first is second is fake_handler
    handler_cls.assert_called_once()


def test_init_langfuse_fail_open_on_error():
    """클라이언트 생성 실패해도 예외를 삼키고 None 반환 (앱 기동 비차단)."""
    with (
        patch.object(lf, "settings", _make_settings(langfuse_enabled=True)),
        patch.object(lf, "Langfuse", side_effect=RuntimeError("boom")),
    ):
        result = lf.init_langfuse()

    assert result is None
    assert lf.get_langfuse_handler() is None


def test_get_langfuse_handler_default_none():
    """초기화 전 accessor 는 None."""
    lf._HANDLER = None
    assert lf.get_langfuse_handler() is None


def test_shutdown_langfuse_flushes_and_is_safe():
    """shutdown → flush + shutdown 호출. 예외는 무시."""
    fake_client = MagicMock(name="client")
    lf._CLIENT = fake_client
    lf._HANDLER = MagicMock()

    lf.shutdown_langfuse()

    fake_client.flush.assert_called_once()
    fake_client.shutdown.assert_called_once()
    assert lf._CLIENT is None
    assert lf._HANDLER is None


def test_shutdown_langfuse_swallows_errors():
    """flush 예외도 전파하지 않는다."""
    fake_client = MagicMock(name="client")
    fake_client.flush.side_effect = RuntimeError("flush boom")
    lf._CLIENT = fake_client
    lf._HANDLER = MagicMock()

    lf.shutdown_langfuse()  # no raise


def test_shutdown_langfuse_safe_when_not_initialized():
    """초기화 전 shutdown 호출도 예외 없이 통과."""
    lf._CLIENT = None
    lf._HANDLER = None
    lf.shutdown_langfuse()  # no raise
