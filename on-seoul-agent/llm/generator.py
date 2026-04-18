from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from llm.client import get_chat_model
from core.exceptions import LLMException


class Generator:
    def __init__(self, model: BaseChatModel | None = None) -> None:
        self._model = model or get_chat_model()

    async def generate(self, prompt: str, system: str | None = None) -> str:
        """Send a prompt to the LLM and return the text response."""
        messages = []
        if system:
            messages.append(SystemMessage(content=system))
        messages.append(HumanMessage(content=prompt))

        try:
            response = await self._model.ainvoke(messages)
            return str(response.content)
        except Exception as e:
            raise LLMException(f"LLM generation failed: {str(e)}", detail=e)
