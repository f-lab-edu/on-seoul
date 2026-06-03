"""POST /notification/template 라우터 테스트(요약/하이라이트 생성).

이 엔드포인트는 "완성 본문(body)"이 아니라 짧은 "요약/하이라이트(summary)"를
생성한다. 사실(서비스명·상태·접수기간·링크)은 Knock 이메일 Liquid 템플릿이
결정적으로 렌더링하므로, AI는 행동 유도 하이라이트만 만든다.

응답 계약은 {title, summary}이며, title은 코드에서 결정적으로 생성하고
summary만 LLM이 생성한다. 실제 LLM/외부 API 호출 없이 AsyncMock으로 LLM을
모킹한다. 망 분리/Nginx 레벨 보호 가정이므로 엔드포인트에 별도 인증은 없다.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from routers.notification import _HighlightResponse

# ---------------------------------------------------------------------------
# 픽스처
# ---------------------------------------------------------------------------


@pytest.fixture()
def app() -> FastAPI:
    from main import app as _app

    return _app


@pytest.fixture()
async def client(app: FastAPI) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _make_llm_mock(
    summary: str = "접수가 다시 시작됐어요. 마감 전에 확인해 보세요.",
):
    """정상 summary를 반환하는 LLM chain mock을 생성한다(title은 코드 생성)."""
    chain_mock = AsyncMock()
    chain_mock.ainvoke = AsyncMock(
        return_value=_HighlightResponse(summary=summary)
    )

    llm_mock = AsyncMock()
    llm_mock.with_structured_output = lambda _: chain_mock

    return llm_mock, chain_mock


def _single_group(service_id: str = "SVC001", **overrides) -> dict:
    """서비스 그룹 1개를 담은 요청 본문을 생성한다."""
    group = {
        "service_id": service_id,
        "service_name": "OO수영장 자유수영",
        "area_name": "강남구",
        "service_url": "https://yeyak.seoul.go.kr/aaa",
        "service_status": "접수중",
        "changes": [
            {
                "change_type": "UPDATED",
                "field_name": "serviceStatus",
                "old_value": "예약마감",
                "new_value": "접수중",
            }
        ],
    }
    group.update(overrides)
    return {"services": [group]}


# ---------------------------------------------------------------------------
# 정상 응답 케이스
# ---------------------------------------------------------------------------


class TestCreateTemplateSuccess:
    async def test_single_group_returns_200(self, client: AsyncClient):
        """서비스 그룹 1개 → 200, title은 코드 생성·summary는 LLM 반환."""
        llm_mock, _ = _make_llm_mock(
            summary="강남구 자유수영 접수가 재개됐어요. 마감 전 신청하세요.",
        )

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post("/notification/template", json=_single_group())

        assert response.status_code == 200
        data = response.json()
        assert "온서울 맞춤" in data["title"]  # 코드 결정적 생성 포맷
        assert "1개" in data["title"]  # service_count=1
        assert "공공서비스 정보" in data["title"]
        assert (
            data["summary"] == "강남구 자유수영 접수가 재개됐어요. 마감 전 신청하세요."
        )
        assert "body" not in data

    async def test_multiple_groups_new_and_updated_returns_200(
        self, client: AsyncClient
    ):
        """여러 서비스 그룹(NEW+UPDATED 혼합) → 200."""
        llm_mock, _ = _make_llm_mock(
            summary="2건의 변경이 있어요. 자유수영 접수 재개에 주목하세요.",
        )

        body = {
            "services": [
                {
                    "service_id": "SVC001",
                    "service_name": "OO수영장 자유수영",
                    "area_name": "강남구",
                    "changes": [
                        {
                            "change_type": "UPDATED",
                            "field_name": "serviceStatus",
                            "old_value": "예약마감",
                            "new_value": "접수중",
                        }
                    ],
                },
                {
                    "service_id": "SVC002",
                    "service_name": "강남구립도서관 글쓰기교실",
                    "area_name": "강남구",
                    "changes": [{"change_type": "NEW"}],
                },
            ]
        }

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post("/notification/template", json=body)

        assert response.status_code == 200
        data = response.json()
        assert "온서울 맞춤" in data["title"]
        assert "2개" in data["title"]  # service_count=2
        assert data["summary"]

    async def test_invokes_llm_with_single_retry(self, client: AsyncClient):
        """알림 경로는 지연 민감 → max_retries=1로 호출한다.
        Gemini API 최솟값(10s) 제약으로 timeout은 기본값(30s) 사용.
        asyncio.wait_for(8s)가 consumer 계약(10s)을 보장한다."""
        llm_mock, _ = _make_llm_mock()

        with patch(
            "routers.notification.get_chat_model", return_value=llm_mock
        ) as factory:
            response = await client.post("/notification/template", json=_single_group())

        assert response.status_code == 200
        factory.assert_called_once()
        kwargs = factory.call_args.kwargs
        assert "timeout" not in kwargs  # Gemini 최솟값 10s 제약, 기본값(30s) 사용
        assert kwargs["max_retries"] == 1
        assert kwargs["temperature"] == 0.2


# ---------------------------------------------------------------------------
# 입력 검증 케이스 (422)
# ---------------------------------------------------------------------------


class TestCreateTemplateValidation:
    async def test_empty_services_returns_422(self, client: AsyncClient):
        """services 빈 리스트 → 422."""
        response = await client.post(
            "/notification/template", json={"services": []}
        )
        assert response.status_code == 422

    async def test_empty_changes_in_group_returns_422(self, client: AsyncClient):
        """그룹 내 changes 빈 배열 → 422."""
        response = await client.post(
            "/notification/template",
            json={"services": [{"service_id": "SVC001", "changes": []}]},
        )
        assert response.status_code == 422

    async def test_invalid_change_type_returns_422(self, client: AsyncClient):
        """change_type 범위 위반 → 422."""
        response = await client.post(
            "/notification/template",
            json={
                "services": [
                    {"service_id": "SVC001", "changes": [{"change_type": "MODIFIED"}]}
                ]
            },
        )
        assert response.status_code == 422

    async def test_whitespace_service_id_returns_422(self, client: AsyncClient):
        """service_id 공백 → 422."""
        response = await client.post(
            "/notification/template",
            json={
                "services": [
                    {"service_id": "   ", "changes": [{"change_type": "NEW"}]}
                ]
            },
        )
        assert response.status_code == 422

    async def test_too_many_services_returns_422(self, client: AsyncClient):
        """services 51개(MAX_SERVICES=50 초과) → 422."""
        services = [
            {"service_id": f"SVC{i:03d}", "changes": [{"change_type": "NEW"}]}
            for i in range(51)
        ]
        response = await client.post(
            "/notification/template", json={"services": services}
        )
        assert response.status_code == 422

    async def test_exactly_max_services_is_accepted(self, client: AsyncClient):
        """services 50개(MAX_SERVICES 경계값) → 200까지 도달."""
        llm_mock, _ = _make_llm_mock()
        services = [
            {"service_id": f"SVC{i:03d}", "changes": [{"change_type": "NEW"}]}
            for i in range(50)
        ]
        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template", json={"services": services}
            )
        assert response.status_code == 200

    async def test_too_many_changes_in_group_returns_422(self, client: AsyncClient):
        """그룹 내 changes 51건(MAX_CHANGES_PER_SERVICE=50 초과) → 422."""
        changes = [{"change_type": "UPDATED"} for _ in range(51)]
        response = await client.post(
            "/notification/template",
            json={"services": [{"service_id": "SVC001", "changes": changes}]},
        )
        assert response.status_code == 422

    async def test_exactly_max_changes_in_group_is_accepted(
        self, client: AsyncClient
    ):
        """그룹 내 changes 50건(경계값) → 200까지 도달."""
        llm_mock, _ = _make_llm_mock()
        changes = [{"change_type": "UPDATED"} for _ in range(50)]
        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template",
                json={"services": [{"service_id": "SVC001", "changes": changes}]},
            )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# LLM 실패 degrade 케이스 (503)
# ---------------------------------------------------------------------------


class TestCreateTemplateDegrade:
    async def test_llm_timeout_returns_503(self, client: AsyncClient):
        """LLM TimeoutError → 503."""

        async def _timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        chain_mock = AsyncMock()
        chain_mock.ainvoke = _timeout
        llm_mock = AsyncMock()
        llm_mock.with_structured_output = lambda _: chain_mock

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post("/notification/template", json=_single_group())

        assert response.status_code == 503
        assert response.json()["detail"] == "알림 템플릿 생성에 실패했습니다."

    async def test_llm_general_exception_returns_503(self, client: AsyncClient):
        """LLM 일반 예외 → 503."""

        async def _fail(*args, **kwargs):
            raise RuntimeError("LLM 연결 오류")

        chain_mock = AsyncMock()
        chain_mock.ainvoke = _fail
        llm_mock = AsyncMock()
        llm_mock.with_structured_output = lambda _: chain_mock

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post("/notification/template", json=_single_group())

        assert response.status_code == 503

    async def test_llm_returns_whitespace_summary_yields_503(
        self, client: AsyncClient
    ):
        """LLM이 공백만 있는 summary 반환 → 503."""
        llm_mock, _ = _make_llm_mock(summary="   ")

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post("/notification/template", json=_single_group())

        assert response.status_code == 503

    async def test_self_timeout_triggers_503(self, client: AsyncClient):
        """asyncio.wait_for self-timeout 경로 → 503."""
        with patch(
            "routers.notification._invoke_llm",
            side_effect=asyncio.TimeoutError(),
        ):
            response = await client.post("/notification/template", json=_single_group())

        assert response.status_code == 503


# ---------------------------------------------------------------------------
# 입력 직렬화(프롬프트로 메타/변경이 실제 전달되는지) 검증
# ---------------------------------------------------------------------------


class TestServiceSerialization:
    """_format_services / _build_messages가 LLM 입력에 메타·변경을 담는지 확인.

    happy-path echo 테스트로는 'service_url이 프롬프트에 전달됐는지'를 검증할 수
    없어, 직렬화 결과를 직접 단언한다(추측 금지·노출 금지 규칙의 회귀 가드).
    """

    def test_format_includes_meta_and_change_detail(self):
        from routers.notification import _format_services
        from schemas.notification import NotificationTemplateRequest

        req = NotificationTemplateRequest(
            services=[
                {
                    "service_id": "S1",
                    "service_name": "OO수영장",
                    "area_name": "강남구",
                    "service_url": "https://x/aaa",
                    "service_status": "접수중",
                    "changes": [
                        {
                            "change_type": "UPDATED",
                            "field_name": "serviceStatus",
                            "old_value": "예약마감",
                            "new_value": "접수중",
                        }
                    ],
                }
            ]
        )
        text = _format_services(req)
        assert "[서비스 1]" in text  # 헤더는 인덱스만
        assert "S1" not in text  # service_id는 입력에 등장하지 않음
        assert "OO수영장" in text  # service_name은 메타 라인으로
        assert "https://x/aaa" in text  # service_url이 프롬프트에 포함
        assert "강남구" in text
        assert "접수중" in text
        assert "UPDATED serviceStatus: 예약마감 -> 접수중" in text

    def test_format_header_omits_service_id_when_no_name(self):
        """service_name이 없어도 service_id는 직렬화 결과 어디에도 등장하지 않는다."""
        from routers.notification import _format_services
        from schemas.notification import NotificationTemplateRequest

        req = NotificationTemplateRequest(
            services=[{"service_id": "S2", "changes": [{"change_type": "NEW"}]}]
        )
        text = _format_services(req)
        assert "[서비스 1]" in text
        assert "S2" not in text  # raw service_id 노출 불가
        # NEW는 old/new 값이 없으므로 화살표 표기가 붙지 않아야 한다.
        assert "->" not in text

    def test_format_service_name_appears_as_meta_line(self):
        """service_name이 있으면 헤더가 아닌 메타 라인으로 출력된다."""
        from routers.notification import _format_services
        from schemas.notification import NotificationTemplateRequest

        req = NotificationTemplateRequest(
            services=[
                {
                    "service_id": "S9",
                    "service_name": "강남구립도서관",
                    "changes": [{"change_type": "NEW"}],
                }
            ]
        )
        text = _format_services(req)
        assert "- service_name: 강남구립도서관" in text
        assert "S9" not in text

    def test_format_omits_absent_optional_meta(self):
        """없는 필드는 프롬프트에 라벨조차 등장하지 않아야 한다(추측 금지)."""
        from routers.notification import _format_services
        from schemas.notification import NotificationTemplateRequest

        req = NotificationTemplateRequest(
            services=[
                {
                    "service_id": "S3",
                    "service_name": "이름만 있는 서비스",
                    "changes": [{"change_type": "DELETED"}],
                }
            ]
        )
        text = _format_services(req)
        assert "service_url" not in text
        assert "area_name" not in text
        assert "place_name" not in text

    def test_format_numbers_multiple_groups(self):
        from routers.notification import _format_services
        from schemas.notification import NotificationTemplateRequest

        req = NotificationTemplateRequest(
            services=[
                {"service_id": "SVC-AAA", "changes": [{"change_type": "NEW"}]},
                {"service_id": "SVC-BBB", "changes": [{"change_type": "NEW"}]},
            ]
        )
        text = _format_services(req)
        assert "[서비스 1]" in text
        assert "[서비스 2]" in text
        assert "SVC-AAA" not in text
        assert "SVC-BBB" not in text

    async def test_scheduled_trigger_omits_change_block(self):
        """시점 트리거(빈 changes)는 "- 변경:" 블록 없이 메타만 직렬화한다."""
        from routers.notification import _format_group, _format_services
        from schemas.notification import (
            NotificationTemplateRequest,
            ServiceChangeGroup,
        )

        group = ServiceChangeGroup(
            service_id="S1",
            service_name="마포 봄꽃 문화행사",
            area_name="마포구",
            service_status="접수중",
            changes=[],
        )
        text = _format_group(1, group)
        assert "- 변경:" not in text
        assert "마포 봄꽃 문화행사" in text
        assert "마포구" in text
        assert "접수중" in text
        assert "S1" not in text

        req = NotificationTemplateRequest(
            trigger_type="DEADLINE_DDAY",
            services=[group],
        )
        services_text = _format_services(req)
        assert "- 변경:" not in services_text
        assert "마포 봄꽃 문화행사" in services_text

    async def test_meta_reaches_llm_input_not_just_echoed(self, client: AsyncClient):
        """직렬화된 서비스 메타가 LLM에 전달된 메시지 안에 실제로 들어가는지 확인.

        happy-path echo 테스트는 mock 반환값을 그대로 검증하므로 입력 전달을
        보장하지 못한다. ainvoke 인자를 캡처해 직렬화된 입력을 직접 단언한다.
        """
        captured: dict = {}

        async def _capture(messages, *args, **kwargs):
            captured["messages"] = messages
            return _HighlightResponse(summary="s")

        chain_mock = AsyncMock()
        chain_mock.ainvoke = _capture
        llm_mock = AsyncMock()
        llm_mock.with_structured_output = lambda _: chain_mock

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template", json=_single_group()
            )

        assert response.status_code == 200
        # 마지막 HumanMessage가 직렬화된 서비스 입력이며 메타를 포함해야 한다.
        last_human = captured["messages"][-1]
        assert "OO수영장 자유수영" in last_human.content
        assert "SVC001" not in last_human.content  # service_id 미노출


# ---------------------------------------------------------------------------
# trigger_type (시점 트리거) 케이스
# ---------------------------------------------------------------------------


def _scheduled_group(service_id: str = "SVC001", **overrides) -> dict:
    """changes 빈 배열을 가진 시점 트리거용 서비스 그룹을 생성한다."""
    group = {
        "service_id": service_id,
        "service_name": "마포 봄꽃 문화행사",
        "area_name": "마포구",
        "service_status": "접수중",
        "changes": [],
    }
    group.update(overrides)
    return group


class TestTriggerType:
    @pytest.mark.parametrize(
        "trigger_type",
        ["CHANGE", "OPEN_DAY", "BEFORE_RECEIPT_D1", "DEADLINE_DDAY"],
    )
    async def test_each_trigger_type_returns_200(
        self, client: AsyncClient, trigger_type: str
    ):
        """4종 trigger_type 모두 정상 200."""
        llm_mock, _ = _make_llm_mock(summary="요약입니다.")
        if trigger_type == "CHANGE":
            body = {"trigger_type": trigger_type, **_single_group()}
        else:
            body = {"trigger_type": trigger_type, "services": [_scheduled_group()]}

        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post("/notification/template", json=body)

        assert response.status_code == 200
        assert response.json()["summary"] == "요약입니다."

    async def test_invalid_trigger_type_returns_422(self, client: AsyncClient):
        """enum 외 trigger_type → 422."""
        body = {"trigger_type": "WEEKLY", **_single_group()}
        response = await client.post("/notification/template", json=body)
        assert response.status_code == 422

    async def test_scheduled_trigger_with_empty_changes_returns_200(
        self, client: AsyncClient
    ):
        """시점 트리거 + changes:[] → 200 (빈 changes 허용)."""
        llm_mock, _ = _make_llm_mock()
        body = {"trigger_type": "OPEN_DAY", "services": [_scheduled_group()]}
        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post("/notification/template", json=body)
        assert response.status_code == 200

    async def test_change_trigger_with_empty_changes_returns_422(
        self, client: AsyncClient
    ):
        """CHANGE + changes:[] → 422 (CHANGE는 changes 필수)."""
        body = {"trigger_type": "CHANGE", "services": [_scheduled_group()]}
        response = await client.post("/notification/template", json=body)
        assert response.status_code == 422

    async def test_missing_trigger_type_defaults_to_change(
        self, client: AsyncClient
    ):
        """trigger_type 누락 → CHANGE 기본 동작(하위호환)."""
        llm_mock, _ = _make_llm_mock()
        # trigger_type 없이 changes 있는 기존 본문 → 정상
        with patch("routers.notification.get_chat_model", return_value=llm_mock):
            response = await client.post(
                "/notification/template", json=_single_group()
            )
        assert response.status_code == 200

    async def test_missing_trigger_type_enforces_change_rules(
        self, client: AsyncClient
    ):
        """trigger_type 누락 + changes:[] → CHANGE 규칙 적용으로 422."""
        body = {"services": [_scheduled_group()]}
        response = await client.post("/notification/template", json=body)
        assert response.status_code == 422


class TestTriggerHintInjection:
    """_build_messages가 trigger_type별 _TRIGGER_HINTS를 주입하는지 단위 검증."""

    @pytest.mark.parametrize(
        "trigger_type",
        ["OPEN_DAY", "BEFORE_RECEIPT_D1", "DEADLINE_DDAY"],
    )
    def test_scheduled_trigger_injects_hint(self, trigger_type: str):
        from llm.prompts.notification import _TRIGGER_HINTS
        from routers.notification import _build_messages
        from schemas.notification import NotificationTemplateRequest

        req = NotificationTemplateRequest(
            trigger_type=trigger_type,
            services=[
                {
                    "service_id": "S1",
                    "service_name": "테스트 서비스",
                    "changes": [],
                }
            ],
        )
        messages = _build_messages(req)
        system = messages[0].content
        assert _TRIGGER_HINTS[trigger_type] in system

    def test_change_trigger_uses_base_system_unchanged(self):
        """CHANGE는 빈 힌트이므로 SystemMessage가 기본 프롬프트 그대로다."""
        from llm.prompts.notification import NOTIFICATION_SYSTEM
        from routers.notification import _build_messages
        from schemas.notification import NotificationTemplateRequest

        req = NotificationTemplateRequest(
            trigger_type="CHANGE",
            services=[
                {
                    "service_id": "S1",
                    "changes": [{"change_type": "NEW"}],
                }
            ],
        )
        messages = _build_messages(req)
        assert messages[0].content == NOTIFICATION_SYSTEM

    def test_scheduled_hint_not_present_for_change(self):
        """CHANGE의 SystemMessage에는 시점 힌트 문구가 없어야 한다."""
        from llm.prompts.notification import _TRIGGER_HINTS
        from routers.notification import _build_messages
        from schemas.notification import NotificationTemplateRequest

        req = NotificationTemplateRequest(
            trigger_type="CHANGE",
            services=[{"service_id": "S1", "changes": [{"change_type": "NEW"}]}],
        )
        system = _build_messages(req)[0].content
        assert _TRIGGER_HINTS["DEADLINE_DDAY"] not in system
