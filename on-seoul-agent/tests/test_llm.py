import pytest
from unittest.mock import AsyncMock, MagicMock

from llm.embedder import Embedder
from llm.generator import Generator
from core.exceptions import LLMException, RateLimitException


@pytest.mark.asyncio
async def test_embedder_embed():
    mock_embeddings = AsyncMock()
    mock_embeddings.aembed_query = AsyncMock(return_value=[0.1, 0.2, 0.3])
    embedder = Embedder(embeddings=mock_embeddings)
    result = await embedder.embed("test text")
    assert result == [0.1, 0.2, 0.3]
    mock_embeddings.aembed_query.assert_called_once_with("test text")


@pytest.mark.asyncio
async def test_embedder_embed_many():
    mock_embeddings = AsyncMock()
    mock_embeddings.aembed_documents = AsyncMock(return_value=[[0.1], [0.2]])
    embedder = Embedder(embeddings=mock_embeddings)
    result = await embedder.embed_many(["a", "b"])
    assert result == [[0.1], [0.2]]


@pytest.mark.asyncio
async def test_embedder_embed_raises_rate_limit_exception_as_is():
    """embed()이 RateLimitException을 LLMException으로 뭉개지 않고 그대로 전달한다."""
    mock_embeddings = AsyncMock()
    mock_embeddings.aembed_query = AsyncMock(
        side_effect=RateLimitException("rate limit 소진")
    )
    embedder = Embedder(embeddings=mock_embeddings)

    with pytest.raises(RateLimitException):
        await embedder.embed("test")


@pytest.mark.asyncio
async def test_embedder_embed_many_raises_rate_limit_exception_as_is():
    """embed_many()가 RateLimitException을 LLMException으로 뭉개지 않고 그대로 전달한다."""
    mock_embeddings = AsyncMock()
    mock_embeddings.aembed_documents = AsyncMock(
        side_effect=RateLimitException("rate limit 소진")
    )
    embedder = Embedder(embeddings=mock_embeddings)

    with pytest.raises(RateLimitException):
        await embedder.embed_many(["a", "b"])


@pytest.mark.asyncio
async def test_embedder_embed_wraps_generic_exception_in_llm_exception():
    """embed()이 일반 예외를 LLMException으로 감싼다."""
    mock_embeddings = AsyncMock()
    mock_embeddings.aembed_query = AsyncMock(side_effect=ValueError("unexpected"))
    embedder = Embedder(embeddings=mock_embeddings)

    with pytest.raises(LLMException, match="Embedding failed"):
        await embedder.embed("test")


@pytest.mark.asyncio
async def test_embedder_embed_many_wraps_generic_exception_in_llm_exception():
    """embed_many()가 일반 예외를 LLMException으로 감싼다."""
    mock_embeddings = AsyncMock()
    mock_embeddings.aembed_documents = AsyncMock(side_effect=ValueError("unexpected"))
    embedder = Embedder(embeddings=mock_embeddings)

    with pytest.raises(LLMException, match="Batch embedding failed"):
        await embedder.embed_many(["a", "b"])


@pytest.mark.asyncio
async def test_generator_generate_without_system():
    mock_response = MagicMock()
    mock_response.content = "hello"
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)
    generator = Generator(model=mock_model)
    result = await generator.generate("what is up?")
    assert result == "hello"


@pytest.mark.asyncio
async def test_generator_generate_with_system():
    mock_response = MagicMock()
    mock_response.content = "response"
    mock_model = AsyncMock()
    mock_model.ainvoke = AsyncMock(return_value=mock_response)
    generator = Generator(model=mock_model)
    result = await generator.generate("hello", system="You are helpful")
    assert result == "response"
    call_args = mock_model.ainvoke.call_args[0][0]
    assert len(call_args) == 2  # SystemMessage + HumanMessage
