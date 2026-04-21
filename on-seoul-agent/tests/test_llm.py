import pytest
from unittest.mock import AsyncMock, MagicMock

from llm.embedder import Embedder
from llm.generator import Generator


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
