"""core/telemetry.py 단위 테스트.

핵심 보증:
- otel_enabled=False (기본/로컬) → 완전 no-op. provider 등록도, instrument 호출도 없어야 한다.
- otel_enabled=True → providers 등록 + 각 Instrumentor.instrument 호출.
- exporter 실 전송은 전혀 일어나지 않도록 모든 SDK 진입점을 모킹한다.
- exporter/instrument 예외는 fail-open (앱 기동을 막지 않음).
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

import core.telemetry as telemetry


@pytest.fixture(autouse=True)
def _isolate_telemetry_state():
    """각 테스트 후 모듈 전역 상태 + 루트 로거 핸들러를 원복한다.

    enabled 경로 테스트가 mock LoggingHandler 를 루트 로거에 부착하므로
    다른 테스트로 누수되지 않도록 격리한다.
    """
    root_handlers = list(logging.getLogger().handlers)
    yield
    telemetry.shutdown_telemetry()
    logging.getLogger().handlers = root_handlers


def _make_settings(**overrides):
    base = {
        "otel_enabled": False,
        "otel_service_name": "on-seoul-agent",
        "otel_exporter_otlp_endpoint": "http://on-seoul-signoz:4317",
        "app_version": "0.1.0",
        "otel_environment": "test",
    }
    base.update(overrides)
    s = MagicMock()
    for k, v in base.items():
        setattr(s, k, v)
    return s


def test_setup_telemetry_disabled_is_noop():
    """otel_enabled=False → SDK/Instrumentor를 일절 건드리지 않는다."""
    app = MagicMock()
    with (
        patch.object(telemetry, "settings", _make_settings(otel_enabled=False)),
        patch.object(telemetry, "TracerProvider") as tp,
        patch.object(telemetry, "MeterProvider") as mp,
        patch.object(telemetry, "LoggerProvider") as lp,
        patch.object(telemetry, "FastAPIInstrumentor") as fi,
    ):
        result = telemetry.setup_telemetry(app)

    assert result is False
    tp.assert_not_called()
    mp.assert_not_called()
    lp.assert_not_called()
    fi.instrument_app.assert_not_called()


def test_setup_telemetry_disabled_when_endpoint_blank():
    """endpoint 미설정 시 enabled=True여도 no-op (fail-open)."""
    app = MagicMock()
    with (
        patch.object(
            telemetry,
            "settings",
            _make_settings(otel_enabled=True, otel_exporter_otlp_endpoint=""),
        ),
        patch.object(telemetry, "TracerProvider") as tp,
    ):
        result = telemetry.setup_telemetry(app)

    assert result is False
    tp.assert_not_called()


def test_setup_telemetry_enabled_registers_providers_and_instruments():
    """otel_enabled=True → providers 등록 + instrument 호출."""
    app = MagicMock()
    with (
        patch.object(telemetry, "settings", _make_settings(otel_enabled=True)),
        patch.object(telemetry, "set_tracer_provider") as set_tp,
        patch.object(telemetry, "set_meter_provider") as set_mp,
        patch.object(telemetry, "set_logger_provider") as set_lp,
        patch.object(telemetry, "TracerProvider"),
        patch.object(telemetry, "MeterProvider"),
        patch.object(telemetry, "LoggerProvider"),
        patch.object(telemetry, "OTLPSpanExporter"),
        patch.object(telemetry, "OTLPMetricExporter"),
        patch.object(telemetry, "OTLPLogExporter"),
        patch.object(telemetry, "BatchSpanProcessor"),
        patch.object(telemetry, "BatchLogRecordProcessor"),
        patch.object(telemetry, "PeriodicExportingMetricReader"),
        patch.object(telemetry, "LoggingHandler"),
        patch.object(telemetry, "FastAPIInstrumentor") as fi,
        patch.object(telemetry, "HTTPXClientInstrumentor") as hi,
        patch.object(telemetry, "RedisInstrumentor") as ri,
        patch.object(telemetry, "SQLAlchemyInstrumentor") as si,
    ):
        result = telemetry.setup_telemetry(app)

    assert result is True
    set_tp.assert_called_once()
    set_mp.assert_called_once()
    set_lp.assert_called_once()
    fi.instrument_app.assert_called_once()
    hi.return_value.instrument.assert_called_once()
    ri.return_value.instrument.assert_called_once()
    # SQLAlchemy는 두 엔진(on_ai / on_data) 각각 instrument.
    assert si.return_value.instrument.call_count == 2


def test_setup_telemetry_fail_open_on_exporter_error():
    """exporter 생성이 실패해도 예외를 삼키고 False 반환 (앱 기동 비차단)."""
    app = MagicMock()
    with (
        patch.object(telemetry, "settings", _make_settings(otel_enabled=True)),
        patch.object(
            telemetry, "TracerProvider", side_effect=RuntimeError("boom")
        ),
    ):
        result = telemetry.setup_telemetry(app)

    assert result is False


def test_shutdown_telemetry_is_safe_when_not_initialized():
    """초기화 전 shutdown 호출도 예외 없이 통과해야 한다."""
    telemetry._PROVIDERS.clear()
    telemetry.shutdown_telemetry()  # no raise


def test_setup_telemetry_idempotent_skips_reinit_when_already_active():
    """이미 활성(_PROVIDERS 비어있지 않음)이면 재초기화/중복 instrument 를 건너뛴다.

    모듈 레벨 setup_telemetry 호출 + 테스트들의 반복 main import 로 인한 중복
    provider/handler 등록·재계측을 방지하는 idempotency 가드를 검증한다.
    """
    app = MagicMock()
    telemetry._PROVIDERS.append(object())  # 이미 활성 상태로 위장.
    with (
        patch.object(telemetry, "settings", _make_settings(otel_enabled=True)),
        patch.object(telemetry, "TracerProvider") as tp,
        patch.object(telemetry, "FastAPIInstrumentor") as fi,
    ):
        result = telemetry.setup_telemetry(app)

    assert result is True
    # 가드에 막혀 provider 재생성·instrument 재부착이 일어나지 않아야 한다.
    tp.assert_not_called()
    fi.instrument_app.assert_not_called()


# ---------------------------------------------------------------------------
# main.py 와이어링: setup_telemetry 가 모듈 import(서빙 전) 시점에 no-op/활성으로
# 호출되어야 한다 — 서버 span 단절 회귀 방지. import 시점 호출이 "실제로 서버 span 을
# 만드는지"의 권위 있는 가드는 tests/test_telemetry_server_span.py 의 e2e 테스트다.
# ---------------------------------------------------------------------------


def test_setup_telemetry_noop_on_disabled_main_import():
    """otel 비활성(기본) 상태로 main 을 import 해도 instrument_app 미호출(no-op).

    import 시점에 SigNoz 연결 시도나 instrument 부착이 일어나지 않음을 보증한다.
    """
    import importlib
    import sys

    saved = sys.modules.pop("main", None)
    try:
        with (
            patch.object(telemetry, "settings", _make_settings(otel_enabled=False)),
            patch.object(telemetry, "FastAPIInstrumentor") as fi,
            patch.object(telemetry, "TracerProvider") as tp,
        ):
            importlib.import_module("main")
            fi.instrument_app.assert_not_called()
            tp.assert_not_called()
    finally:
        if saved is not None:
            sys.modules["main"] = saved
        else:
            sys.modules.pop("main", None)
