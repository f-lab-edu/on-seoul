"""generate_title_node — 독립 병렬 제목 생성 노드.

START 에서 intake_node 와 병렬 분기(fan-out)해 자기 일만 하고 END 로 간다.
공유 state(output/title)에 쓰지 않는다 → fire-and-emit only(reducer/조인 불필요).

게이트: state["title_needed"](첫 턴, message_id==1) True 일 때만 동작.
입력: state["message"] 만 사용(intake/검색/refined_query 무의존).

별도 SSE 이벤트 payload:
    {"type": "title", "room_id", "title", "message_id", "query"}
Spring 릴레이가 event name 을 벗길 수 있어 payload 의 type:"title" 로 식별한다.

자체 캐시(Redis, 전역):
    키 = title:{_TITLE_PROMPT_VERSION}:{hash(정규화(message))}
    정규화 = strip + 공백 collapse + NFC. 룸/유저 무관 전역 공유.
    제목은 message 만 의존하는 결정적 산출이라 stale 위험이 없어 TTL 을 길게 둔다.
    프롬프트 변경 시 _TITLE_PROMPT_VERSION 을 올려 무효화한다.

fail-open: LLM/캐시 예외 시 경고 로그 + emit 생략(스트림 중단 금지), return {}.
빈/공백 title 도 emit 생략. 에러 턴과 무관하게 독립 emit 된다.
"""

import hashlib
import logging
import re
import unicodedata
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.prompts import ChatPromptTemplate
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from llm.client import get_chat_model
from schemas.state import AgentState

logger = logging.getLogger(__name__)

# 프롬프트 변경 시 이 상수를 올려 기존 캐시 엔트리를 무효화한다.
_TITLE_PROMPT_VERSION = 1

# 제목 캐시 TTL(초). 결정적 산출이라 stale 위험은 없으나, 무기한 누적을 막기 위해
# 넉넉한 상한(30일)을 둔다.
_TITLE_CACHE_TTL = 60 * 60 * 24 * 30

_TITLE_SYSTEM = """\
사용자 질문을 보고 대화 제목을 10자 이내로 만드세요.
특수문자나 이모지 없이 명사형으로 끝내세요.
"""

_TITLE_HUMAN = "사용자 질문: {message}"

_WS_RE = re.compile(r"\s+")


class _TitleOutput(BaseModel):
    title: str


def _normalize_message(message: str) -> str:
    """캐시 키용 메시지 정규화 — NFC + strip + 공백 collapse.

    룸/유저 무관 전역 캐시라 표면 차이(앞뒤 공백, 중복 공백, 유니코드 분해형)를
    제거해 동일 의미 질의가 같은 키를 갖도록 한다.
    """
    return _WS_RE.sub(" ", unicodedata.normalize("NFC", message).strip())


def _title_cache_key(message: str) -> str:
    """title:{version}:{hash(정규화(message))} — 전역 공유 캐시 키."""
    digest = hashlib.sha256(_normalize_message(message).encode("utf-8")).hexdigest()
    return f"title:{_TITLE_PROMPT_VERSION}:{digest}"


def build_title_chain(model: BaseChatModel | None = None) -> Any:
    """title 생성 체인을 조립한다(answer 경로와 독립)."""
    llm = model or get_chat_model()
    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", _TITLE_SYSTEM),
            ("human", _TITLE_HUMAN),
        ]
    )
    return prompt | llm.with_structured_output(_TitleOutput)


class TitleNodes:
    """제목 생성 페이즈 — generate_title_node 단일 노드.

    의존:
      - title_chain: message → _TitleOutput LLM 체인(미주입 시 기본 생성).
      - redis: 전역 title 캐시(AgentGraph 가 보유한 redis 재사용). None 이면 캐시 우회.
    """

    def __init__(self, title_chain: Any = None, redis: Any = None) -> None:
        self._title_chain = title_chain or build_title_chain()
        self._redis = redis

    async def generate_title_node(self, state: AgentState) -> dict[str, Any]:
        """첫 턴이면 message 로 제목을 생성·캐시하고 title 이벤트를 emit 한다.

        공유 state 에 쓰지 않으므로 항상 {} 를 반환한다(fire-and-emit only).
        """
        if not state.get("title_needed"):
            return {}

        message = state["message"]
        try:
            title = await self._resolve_title(message)
        except Exception:
            # fail-open: LLM/캐시 예외는 스트림을 중단시키지 않는다.
            logger.warning("title.generate 실패 — emit 생략", exc_info=True)
            return {}

        if not (title or "").strip():
            # 빈/공백 title 은 emit 생략.
            return {}

        self._emit_title(state, title)
        return {}

    async def _resolve_title(self, message: str) -> str:
        """캐시 조회 → 미스면 LLM 호출 + 캐시 set. 전역 캐시(룸/유저 무관)."""
        key = _title_cache_key(message)
        if self._redis is not None:
            cached = await self._redis.get(key)
            if cached is not None:
                cached_str = (
                    cached.decode("utf-8") if isinstance(cached, bytes) else cached
                )
                if cached_str.strip():
                    return cached_str

        out: _TitleOutput = await self._title_chain.ainvoke({"message": message})
        title = (out.title or "").strip()

        if title and self._redis is not None:
            await self._redis.set(key, title, ex=_TITLE_CACHE_TTL)
        return title

    @staticmethod
    def _emit_title(state: AgentState, title: str) -> None:
        """title 이벤트를 custom stream 으로 흘려보낸다(컨텍스트 밖이면 no-op).

        payload 의 type:"title" 은 Spring 릴레이가 event name 을 벗겨도 식별
        가능하도록 둔 필수 식별자다.
        """
        try:
            writer = get_stream_writer()
        except (RuntimeError, LookupError):
            return
        if writer is None:
            return
        writer(
            {
                "_evt": "title",
                "type": "title",
                "room_id": state["room_id"],
                "title": title,
                "message_id": state["message_id"],
                "query": state["message"],
            }
        )
