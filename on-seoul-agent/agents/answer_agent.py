"""하위호환 셰임 — 정식 위치는 agents/answer/ 패키지. import 계약 보존용."""

from agents.answer import *  # noqa: F401,F403  (__all__ 가 underscore 심볼까지 재export)
