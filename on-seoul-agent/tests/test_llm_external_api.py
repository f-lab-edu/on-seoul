"""
외부 API 연동 테스트 — 실제 API를 호출한다.

실행:
    uv run pytest tests/test_llm_external_api.py -v

일반 테스트와 분리 실행:
    uv run pytest -m "not external_api"   # 연동 테스트 제외
    uv run pytest -m external_api         # 연동 테스트만
"""

import pytest

from core.config import settings
from llm.client import get_chat_model, get_embeddings
from llm.embedder import Embedder
from llm.generator import Generator

pytestmark = pytest.mark.external_api

_QUOTA_SIGNALS = ("RESOURCE_EXHAUSTED", "429", "quota")
_AUTH_SIGNALS = ("API key", "authentication", "401", "403")


def _skip_on_api_error(e: Exception) -> None:
    err = str(e)
    if any(s in err for s in _AUTH_SIGNALS) or any(
        s.lower() in err.lower() for s in ("authentication",)
    ):
        pytest.skip(f"API 키 인증 실패: {e}")
    if any(s in err for s in _QUOTA_SIGNALS) or "quota" in err.lower():
        pytest.skip(f"쿼터 초과 (무료 티어): {e}")


def _require_google_key() -> None:
    if not settings.google_api_key:
        pytest.skip("GOOGLE_API_KEY가 설정되지 않음")


def _require_openai_key() -> None:
    if not settings.openai_api_key:
        pytest.skip("OPENAI_API_KEY가 설정되지 않음")


# ── Chat ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gemini_generate() -> None:
    """Gemini가 한국어 질문에 정상 응답하는지 확인."""
    _require_google_key()
    generator = Generator()
    try:
        result = await generator.generate(
            "서울시 공공서비스 예약이 뭐야?",
            system="한 문장으로만 답해줘.",
        )
    except Exception as e:
        _skip_on_api_error(e)
        raise
    assert isinstance(result, str) and len(result) > 0


@pytest.mark.asyncio
async def test_gpt_generate_fallback() -> None:
    """GPT 폴백이 정상 동작하는지 확인."""
    _require_openai_key()
    generator = Generator(model=get_chat_model(provider="openai"))
    try:
        result = await generator.generate(
            "안녕하세요.",
            system="한 문장으로만 답해줘.",
        )
    except Exception as e:
        _skip_on_api_error(e)
        raise
    assert isinstance(result, str) and len(result) > 0


# ── Embeddings ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_gemini_embed() -> None:
    """Gemini gemini-embedding-2-preview가 1536차원 벡터를 반환하는지 확인 (output_dimensionality=1536)."""
    _require_google_key()
    embedder = Embedder(embeddings=get_embeddings())
    try:
        result = await embedder.embed("서울 수영장 예약")
    except Exception as e:
        _skip_on_api_error(e)
        raise
    assert isinstance(result, list)
    assert len(result) == 1536  # gemini-embedding-2-preview 차원
    assert all(isinstance(v, float) for v in result)


@pytest.mark.asyncio
async def test_gemini_embed_many() -> None:
    """여러 텍스트를 배치 임베딩하는지 확인."""
    _require_google_key()
    embedder = Embedder(embeddings=get_embeddings())
    texts = ["수영장", "문화센터", "체육관"]
    try:
        results = await embedder.embed_many(texts)
    except Exception as e:
        _skip_on_api_error(e)
        raise
    assert len(results) == len(texts)
    assert all(len(v) == 1536 for v in results)
