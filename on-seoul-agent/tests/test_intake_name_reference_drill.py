"""intake 이름-기반 참조 DRILL 회귀 — 버그(이름 참조가 NEW 로 오분류) 가드.

근본 원인은 intake 프롬프트/few-shot 부족이었다(배선은 정상). 이 테스트는
두 층위를 단언한다:

1) 프롬프트 회귀 가드 — INTAKE_SYSTEM / INTAKE_FEW_SHOT_EXAMPLES 가 이름-기반·
   속성 DRILL 판별 지침과 (a)단일매치 (b)다중매치 (c)NEW 대조 few-shot 을 담는다.
   (fake LLM 은 프롬프트를 따르지 않으므로, 프롬프트 자체가 지침을 담는지 직접 검증.)
2) 배선 가드 — LLM 이 DRILL+ref_indices 를 내면 intake_node 가 그대로 target_service_ids
   로 매핑하고, NEW 면 검색 경로로 빠지는지(분류 결과가 라우팅에 정상 반영되는지).
"""

import json

from agents.nodes.intake import IntakeNodes
from llm.prompts.intake import INTAKE_FEW_SHOT_EXAMPLES, INTAKE_SYSTEM
from schemas.intake import IntakeAction, TurnKind
from schemas.state import ActionType
from tests.helpers import make_agent_state, make_intake

# 버그 재현 시나리오의 직전 결과(영등포 풋살 3변형 + 마루공원 족구장).
_PREV_FUTSAL = [
    {"service_id": "F1", "label": "2026년 7월 영등포공원 풋살경기장(토,일,공휴일 주간)"},
    {"service_id": "F2", "label": "2026년 7월 영등포공원 풋살경기장(평일 야간)"},
    {"service_id": "F3", "label": "2026년 7월 영등포공원 풋살경기장(평일 주간)"},
    {"service_id": "F4", "label": "마루공원 족구장 1면"},
]

_PREV_SINGLE = [
    {"service_id": "S1", "label": "강남 수영장"},
    {"service_id": "S2", "label": "마포 풋살장"},
]


def _nodes(intake) -> IntakeNodes:
    return IntakeNodes(intake=intake)


def _example_outputs() -> list[dict]:
    """few-shot 예시 output(JSON 문자열)을 파싱한 dict 리스트."""
    return [json.loads(ex["output"]) for ex in INTAKE_FEW_SHOT_EXAMPLES]


class TestPromptGuidance:
    """프롬프트가 이름-기반·속성 DRILL 판별 지침을 담는지(분류 실패 근본 원인 가드)."""

    def test_drill_definition_mentions_name_reference(self):
        # DRILL 이 서수뿐 아니라 직전 결과에 등장한 *시설 이름* 참조도 포함.
        assert "시설 이름" in INTAKE_SYSTEM
        assert "여러 항목" in INTAKE_SYSTEM  # 한 이름이 여러 인덱스와 일치 케이스

    def test_drill_definition_mentions_attribute_questions(self):
        # 항목의 속성(요금 등)을 물어도 DRILL.
        assert "속성" in INTAKE_SYSTEM
        assert "요금" in INTAKE_SYSTEM

    def test_discrimination_rule_present(self):
        # DRILL↔NEW 판별 기준("그 이름이 직전 결과에 있나?")이 프롬프트에 존재.
        assert "DRILL ↔ NEW" in INTAKE_SYSTEM
        assert "있으면 DRILL, 없으면 NEW" in INTAKE_SYSTEM


class TestFewShotExamples:
    """few-shot 에 (a)단일매치 (b)다중매치 DRILL + 기존 (c)NEW 대조가 있는지."""

    def test_single_match_attribute_drill_example(self):
        outs = _example_outputs()
        # "마포 풋살장은 무료야?" → DRILL, ref_indices=[2].
        single = [
            o
            for o in outs
            if o["turn_kind"] == "DRILL" and o["ref_indices"] == [2]
        ]
        assert single, "이름 단일매치 속성 DRILL few-shot 누락"

    def test_multi_match_attribute_drill_example(self):
        outs = _example_outputs()
        # "영등포공원 풋살경기장은 무료야?" → DRILL, ref_indices=[1,2,3].
        multi = [
            o
            for o in outs
            if o["turn_kind"] == "DRILL" and o["ref_indices"] == [1, 2, 3]
        ]
        assert multi, "이름 다중매치 속성 DRILL few-shot 누락"
        # 해당 예시 메시지에 버그 재현 발화가 담겼는지(회귀 추적성).
        msgs = [
            ex["message"]
            for ex in INTAKE_FEW_SHOT_EXAMPLES
            if json.loads(ex["output"])["ref_indices"] == [1, 2, 3]
        ]
        assert any("영등포공원 풋살경기장은 무료야?" in m for m in msgs)

    def test_topic_shift_new_example_preserved(self):
        # 회귀 가드: 직전에 없는 새 시설("마포 수영장") 은 여전히 NEW.
        new_examples = [
            ex
            for ex in INTAKE_FEW_SHOT_EXAMPLES
            if "마포 수영장 알려줘" in ex["message"]
        ]
        assert new_examples
        out = json.loads(new_examples[0]["output"])
        assert out["turn_kind"] == "NEW"
        assert out["ref_indices"] == []


class TestNodeWiringForNameReference:
    """LLM 분류 결과가 라우팅에 정상 반영되는지(배선 가드)."""

    async def test_single_match_drill_binds_index(self):
        # (a) 이름 단일매치 DRILL → target_service_ids 단일.
        node = _nodes(make_intake(turn_kind=TurnKind.DRILL, ref_indices=[2]))
        state = make_agent_state(
            message="마포 풋살장은 무료야?", prev_entities=_PREV_SINGLE
        )
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "DRILL"
        assert update["target_service_ids"] == ["S2"]

    async def test_multi_match_drill_binds_all_indices(self):
        # (b) 다중매치 — 버그 재현 시나리오. ref_indices=[1,2,3] → 3변형 전부 바인딩.
        node = _nodes(make_intake(turn_kind=TurnKind.DRILL, ref_indices=[1, 2, 3]))
        state = make_agent_state(
            message="영등포공원 풋살경기장은 무료야?", prev_entities=_PREV_FUTSAL
        )
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "DRILL"
        assert update["target_service_ids"] == ["F1", "F2", "F3"]

    async def test_topic_shift_stays_new_no_binding(self):
        # (c) 회귀 가드 — 직전에 없는 새 시설은 NEW → 검색 경로(바인딩 없음).
        node = _nodes(make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE))
        state = make_agent_state(
            message="마포 수영장 알려줘", prev_entities=_PREV_SINGLE
        )
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["target_service_ids"] is None

    async def test_drill_routes_to_rehydrate_not_search(self):
        # DRILL 은 rehydrate(검색 스킵)로, NEW+RETRIEVE 는 router(신규 검색)로.
        node = _nodes(make_intake(turn_kind=TurnKind.DRILL, ref_indices=[1, 2, 3]))
        state = make_agent_state(
            message="영등포공원 풋살경기장은 무료야?", prev_entities=_PREV_FUTSAL
        )
        update = await node.intake_node(state)
        merged = {**state, **update}
        assert node.route_intake(merged) == "rehydrate_node"

    async def test_new_routes_to_router_for_fresh_search(self):
        # (c) 대칭 가드 — 직전에 없는 새 시설은 NEW → router_node(신규 검색)로 라우팅.
        # DRILL→rehydrate 와 짝지어, NEW 가 검색 경로를 *타는지*까지 단언(과교정 시
        # rehydrate 로 새어 엉뚱한 직전 항목을 재서술하는 회귀를 잡는다).
        node = _nodes(make_intake(turn_kind=TurnKind.NEW, action=IntakeAction.RETRIEVE))
        state = make_agent_state(
            message="마포 수영장 알려줘", prev_entities=_PREV_SINGLE
        )
        update = await node.intake_node(state)
        merged = {**state, **update}
        assert node.route_intake(merged) == "router_node"

    async def test_overcorrected_drill_to_absent_name_degrades_to_new(self):
        # 과교정 방지 안전판 — 버그 시나리오의 시설로 직접 단언.
        # LLM 이 직전에 *없는* 이름("마포 수영장")을 잘못 DRILL 로 분류하고 범위 밖
        # 인덱스(9)를 내도, 인덱스 계약이 바인딩 0 → NEW+RETRIEVE 로 정직하게 강등.
        # (환각 ID 바인딩 0 + breadcrumb 로 trace 추적 가능.)
        node = _nodes(make_intake(turn_kind=TurnKind.DRILL, ref_indices=[9]))
        state = make_agent_state(
            message="마포 수영장 알려줘", prev_entities=_PREV_SINGLE
        )
        update = await node.intake_node(state)
        assert update["triage"]["turn_kind"] == "NEW"
        assert update["triage"]["action"] == ActionType.RETRIEVE
        assert update["target_service_ids"] is None
        assert "intake_route_fallback" in update["node_path"]
        # 라우팅도 검색 경로로 수렴(rehydrate 로 새지 않음).
        assert node.route_intake({**state, **update}) == "router_node"
