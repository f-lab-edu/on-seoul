"""agents.answer 패키지 분해의 import 계약 회귀 고정.

answer_agent god-class 를 agents/answer/ 패키지(+ llm/prompts/answer.py)로 분해한
뒤에도 `from agents.answer_agent import <X>` 셰임이 외부가 의존하던 모든 심볼
(underscore 포함)을 무손상으로 재export 하는지 회귀로 고정한다.

검증 항목:
1. 외부(agents/routers/tests/scripts)가 실제 import 하던 전 심볼이 셰임에 노출.
2. 셰임 경유 객체와 패키지 직접 경유 객체가 동일(`is`).
3. agents.answer 가 `from agents.answer import *` 로 underscore 까지 노출하려면
   __all__ 에 명시돼야 하므로, __all__ 이 외부 의존 심볼을 전부 포함.
4. AnswerAgent._normalize 가 모듈 레벨 _normalize_card_row 와 동치(위임 보존).
5. cards 모듈은 leaf — agent/prompting 을 import 하지 않는다.
"""

import ast
import importlib
import os

import pytest
from langchain_core.runnables import RunnableLambda

import agents.answer as pkg
import agents.answer_agent as shim

# 외부가 실제 `from agents.answer_agent import` 하는 심볼 화이트리스트.
# 분해 전 단일 모듈이 노출하던 계약을 회귀로 고정한다. 신규 외부 의존이 생기면
# (해당 심볼이 셰임에 없으면) 이 테스트가 깨져 계약 누락을 즉시 드러낸다.
_EXTERNAL_SYMBOLS = [
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


@pytest.mark.parametrize("name", _EXTERNAL_SYMBOLS)
def test_shim_exposes_external_symbol(name):
    """외부 의존 심볼이 셰임(agents.answer_agent)에 런타임 노출된다."""
    assert hasattr(shim, name), f"{name} 가 셰임에서 사라짐 (재export 계약 위반)"


@pytest.mark.parametrize("name", _EXTERNAL_SYMBOLS)
def test_shim_and_package_are_same_object(name):
    """셰임 경유와 패키지 직접 경유가 동일 객체(`is`)다 — 이중 정의 방지."""
    assert getattr(shim, name) is getattr(pkg, name)


def test_external_symbols_in_package_all():
    """외부 의존 심볼이 전부 __all__ 에 명시 — `import *` 가 underscore 까지 노출.

    bare `from agents.answer import *` 는 __all__ 가 없으면 underscore 를 건너뛴다.
    __all__ 에서 underscore 심볼이 누락되면 셰임이 조용히 깨지므로 이를 고정한다.
    """
    missing = [s for s in _EXTERNAL_SYMBOLS if s not in pkg.__all__]
    assert not missing, f"__all__ 누락 — 셰임 import * 가 조용히 깨짐: {missing}"


def test_normalize_delegates_to_module_function():
    """AnswerAgent()._normalize 가 _normalize_card_row 와 동치(위임 보존)."""
    from agents.answer.cards import _normalize_card_row

    agent = shim.AnswerAgent(model=RunnableLambda(lambda x: x))
    row = {
        "service_id": "X1",
        "service_name": "테니스장",
        "area_name": "강남구",
        "service_url": "notaurl",  # 스킴 가드 → fallback URL
        "tel_no": "02-123-4567",
        "use_time_start": "09:00:00",
        "use_time_end": "18:00:00",
    }
    assert agent._normalize(dict(row)) == _normalize_card_row(dict(row))


def test_cards_module_is_leaf():
    """cards.py 는 agent/prompting 를 import 하지 않는 leaf 다(사이클 없음)."""
    cards_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "agents", "answer", "cards.py"
    )
    tree = ast.parse(open(cards_path).read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            # `from agents.answer import prompting` 관용구 검출 — 서브모듈을 alias 로
            # 가져오면 node.module 은 패키지명("agents.answer")이라 위 단독 추가로는
            # 역의존이 걸러지지 않는다. 각 alias 의 full path 도 후보에 넣는다.
            imported.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
    forbidden = {"agents.answer.agent", "agents.answer.prompting"}
    assert not (imported & forbidden), f"cards 가 leaf 가 아님: {imported & forbidden}"


def test_shim_import_star_resolves():
    """셰임이 import * 로 패키지를 가리키며 ImportError 없이 로드된다."""
    reloaded = importlib.reload(shim)
    assert reloaded.AnswerAgent is pkg.AnswerAgent
