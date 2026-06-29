"""agents.answer 패키지 — Answer Agent 의 정식 위치.

분해 전 단일 모듈(agents/answer_agent.py)이 노출하던 import 계약을 무손상으로
보존하기 위해, 외부가 `from agents.answer_agent import <X>` 로 가져오던 모든 심볼
(underscore 포함)을 여기서 재export 하고 __all__ 에 명시한다.

agents/answer_agent.py 는 `from agents.answer import *` 셰임으로 이 패키지를 가리킨다.
"""

from agents.answer.agent import AnswerAgent
from agents.answer.cards import (
    _DISPLAY_LIMIT,
    _FALLBACK_URL,
    _curate_display,
    _curate_score,
    _focal_first,
    _group_by_place_name,
    _guarded_use_time,
    _normalize_card_row,
    _parse_time,
)
from agents.answer.prompting import (
    _build_card_system,
    _compose,
    _has_district_in_message,
    _more_notice,
    _relaxed_notice,
)
from llm.prompts.answer import (
    _CLARIFY_FALLBACK,
    _CLAUSE_REFINE_HINT,
    _CLAUSE_RESERVATION_GUIDE,
    _CLAUSE_SKEW_OFFER,
    _CLAUSE_THIN_CAVEAT,
    _FALLBACK_GUARDRAILS,
    _OUTPUT_RULES,
    _ROLE,
    _STRUCT_ANALYTICS,
    _STRUCT_ATTRIBUTE_GAP,
    _STRUCT_CARD_LIST,
    _STRUCT_CLARIFY,
    _STRUCT_DESCRIBE,
    _STRUCT_DESCRIBE_EMPTY,
    _STRUCT_DETAIL,
    _STRUCT_EXPLAIN,
    _STRUCT_FALLBACK,
    _STRUCT_MAP,
    _STRUCT_OPERATIONAL_DETAIL,
    _STRUCT_RELEVANCE,
    _VOICE_GUIDE,
)

__all__ = [
    "AnswerAgent",
    "_CLARIFY_FALLBACK",
    "_CLAUSE_REFINE_HINT",
    "_CLAUSE_RESERVATION_GUIDE",
    "_CLAUSE_SKEW_OFFER",
    "_CLAUSE_THIN_CAVEAT",
    "_DISPLAY_LIMIT",
    "_FALLBACK_GUARDRAILS",
    "_FALLBACK_URL",
    "_OUTPUT_RULES",
    "_ROLE",
    "_STRUCT_ANALYTICS",
    "_STRUCT_ATTRIBUTE_GAP",
    "_STRUCT_CARD_LIST",
    "_STRUCT_CLARIFY",
    "_STRUCT_DESCRIBE",
    "_STRUCT_DESCRIBE_EMPTY",
    "_STRUCT_DETAIL",
    "_STRUCT_EXPLAIN",
    "_STRUCT_FALLBACK",
    "_STRUCT_MAP",
    "_STRUCT_OPERATIONAL_DETAIL",
    "_STRUCT_RELEVANCE",
    "_VOICE_GUIDE",
    "_build_card_system",
    "_compose",
    "_curate_display",
    "_curate_score",
    "_focal_first",
    "_group_by_place_name",
    "_guarded_use_time",
    "_has_district_in_message",
    "_more_notice",
    "_normalize_card_row",
    "_parse_time",
    "_relaxed_notice",
]
