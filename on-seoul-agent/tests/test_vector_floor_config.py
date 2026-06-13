"""트랙별 차등 유사도 하한(config 분리) 갭 테스트 — QA 보강.

검증 범위:
1. resolve_min_similarity가 row_kind별 config 값을 반환한다 (question 포함).
2. tools가 settings를 import 시점이 아닌 호출 시점에 읽는다
   (env/monkeypatch 오버라이드가 다음 호출에 즉시 반영 — run_recall.py의
   env 오버라이드 BEFORE/AFTER 비교가 유효하려면 필수 전제).
3. question_search의 명시 파라미터 우선: min_similarity=0.0 전달 시
   config(0.65)를 무시한다 (scripts/eval/score_distribution.py 의존 경로).
4. vector_agent의 search_channels parameters가 실제 실행에 쓰인 값과
   일치한다 — 도구 호출은 센티널(None) 기본값으로 config를 해석하고,
   로깅도 같은 config를 읽으므로 둘이 같은 값이어야 한다.
   BM25 채널 top_k 로깅은 실제 한도 BM25_LIMIT(50)이다.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.config import settings
from schemas.search import SearchChannel
from tools.bm25_search import BM25_LIMIT
from tools.question_search import question_search
from tools.vector_search import resolve_min_similarity, vector_search

from tests.test_vector_agent_hybrid import (
    _make_agent,
    _make_state,
    _patch_all_searches,
)

_SAMPLE_VECTOR = [0.1, 0.2, 0.3]
_QUESTION_KEYS = ["service_id", "embedding_text", "intent_label", "similarity"]
_RRF_KEYS = ["service_id", "embedding_text", "metadata", "similarity"]


def _capture_session(keys: list[str]) -> tuple[MagicMock, list[dict]]:
    """bind 파라미터를 캡처하는 fake AsyncSession."""
    binds: list[dict] = []

    async def _capture_execute(stmt, params=None):
        binds.append(params or {})
        mock_result = MagicMock()
        mock_result.keys.return_value = keys
        mock_result.fetchall.return_value = []
        return mock_result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_capture_execute)
    return session, binds


class TestResolveMinSimilarity:
    def test_per_track_values(self):
        """row_kind별로 대응하는 config 필드를 반환한다."""
        assert (
            resolve_min_similarity("identity")
            == settings.vector_min_similarity_identity
        )
        assert (
            resolve_min_similarity("summary") == settings.vector_min_similarity_summary
        )
        assert (
            resolve_min_similarity("question")
            == settings.vector_min_similarity_question
        )

    def test_default_floor_values(self):
        """운영 기본 하한: 3트랙 공통 0.65 (floor 스윕 정점, 2026-06 측정)."""
        assert settings.vector_min_similarity_identity == 0.65
        assert settings.vector_min_similarity_summary == 0.65
        assert settings.vector_min_similarity_question == 0.65

    def test_unknown_row_kind_raises(self):
        """허용되지 않은 row_kind는 KeyError."""
        with pytest.raises(KeyError):
            resolve_min_similarity("bogus")


class TestCallTimeConfigResolution:
    """tools가 settings를 호출 시점에 읽는지 — env 오버라이드 평가의 전제."""

    async def test_vector_search_reads_settings_at_call_time(self, monkeypatch):
        monkeypatch.setattr(settings, "vector_min_similarity_identity", 0.91)
        monkeypatch.setattr(settings, "vector_track_top_k", 7)
        session, binds = _capture_session(_RRF_KEYS)
        await vector_search(session, _SAMPLE_VECTOR, row_kind="identity")
        assert binds[0]["min_similarity"] == 0.91
        assert binds[0]["top_k"] == 7

    async def test_question_search_reads_settings_at_call_time(self, monkeypatch):
        monkeypatch.setattr(settings, "vector_min_similarity_question", 0.93)
        monkeypatch.setattr(settings, "vector_track_top_k", 9)
        session, binds = _capture_session(_QUESTION_KEYS)
        await question_search(session, _SAMPLE_VECTOR)
        assert binds[0]["min_similarity"] == 0.93
        assert binds[0]["top_k"] == 9


class TestQuestionSearchExplicitOverride:
    """명시 파라미터 우선 — score_distribution.py가 min_similarity=0.0으로 의존."""

    async def test_min_similarity_zero_overrides_config(self):
        session, binds = _capture_session(_QUESTION_KEYS)
        await question_search(session, _SAMPLE_VECTOR, min_similarity=0.0)
        assert binds[0]["min_similarity"] == 0.0

    async def test_explicit_min_similarity_overrides_config(self):
        session, binds = _capture_session(_QUESTION_KEYS)
        await question_search(session, _SAMPLE_VECTOR, min_similarity=0.8)
        assert binds[0]["min_similarity"] == 0.8

    async def test_top_k_default_is_track_top_k(self):
        session, binds = _capture_session(_QUESTION_KEYS)
        await question_search(session, _SAMPLE_VECTOR)
        assert binds[0]["top_k"] == settings.vector_track_top_k


class TestSearchChannelsLoggingConsistency:
    """search_channels parameters == 실행에 실제 적용된 config 값."""

    async def test_channel_params_match_config(self, monkeypatch):
        """config를 비기본값으로 바꿔도 로깅이 같은 값을 따라간다."""
        monkeypatch.setattr(settings, "vector_min_similarity_identity", 0.41)
        monkeypatch.setattr(settings, "vector_min_similarity_summary", 0.42)
        monkeypatch.setattr(settings, "vector_min_similarity_question", 0.43)
        monkeypatch.setattr(settings, "vector_track_top_k", 17)

        agent = _make_agent()
        with _patch_all_searches() as ctx:
            result = await agent.search(_make_state())

        channels = result["search_channels"]
        a = channels[SearchChannel.VECTOR_A]["query"]["parameters"]
        b = channels[SearchChannel.VECTOR_B]["query"]["parameters"]
        c = channels[SearchChannel.VECTOR_C]["query"]["parameters"]
        assert (a["min_similarity"], a["top_k"]) == (0.41, 17)
        assert (b["min_similarity"], b["top_k"]) == (0.42, 17)
        assert (c["min_similarity"], c["top_k"]) == (0.43, 17)

        # 도구 호출이 명시 top_k/min_similarity 없이(None 센티널) 이뤄져야
        # 위 로깅 값과 실행 값이 같은 config로 해석된다.
        for call in ctx.mock_vs.call_args_list:
            assert "top_k" not in call.kwargs
            assert "min_similarity" not in call.kwargs
        for call in ctx.mock_qs.call_args_list:
            assert "top_k" not in call.kwargs
            assert "min_similarity" not in call.kwargs

    async def test_bm25_channel_logs_actual_limit(self):
        """BM25 채널 top_k 로깅은 실제 한도 BM25_LIMIT(50)."""
        agent = _make_agent()
        with _patch_all_searches() as ctx:
            result = await agent.search(_make_state())

        bm25_params = result["search_channels"][SearchChannel.BM25]["query"][
            "parameters"
        ]
        assert bm25_params["top_k"] == BM25_LIMIT == 50
        # 실행도 기본 limit(BM25_LIMIT)으로 호출돼야 로깅과 일치한다.
        for call in ctx.mock_bm25.call_args_list:
            assert "limit" not in call.kwargs
