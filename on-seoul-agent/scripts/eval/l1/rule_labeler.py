"""규칙 자동 라벨러 — state 신호만으로 결정적 버킷(LLM 불필요, 공짜).

우선순위(스코핑 먼저, 그다음 강한 실패 우선):
  NON_RETRIEVE > ZERO_HIT > THIN > SKEW > RETRIED > NORMAL

근거: NON_RETRIEVE(action≠RETRIEVE 또는 turn_kind=META)는 애초에 검색을 시도하지
않은 턴이라 hit/quality 신호로 실패를 판정하면 안 된다 — 최우선으로 분리해 L1/L2
분모에서 뺀다. 그 아래로는 0건이 가장 강한 실패라 재시도/thin 신호를 압도한다.
thin/skew 는 결과가 있으나 품질이 약한 경우로 RETRIED(재시도 발생했으나 결과 자체
품질은 미상)보다 정보량이 크다. 건수 신호가 전혀 없는 구 트레이스는 실패로 단정하지
않고 NORMAL(하위호환).
"""

from __future__ import annotations

from scripts.eval.l1.signals import QuerySignals, RuleBucket


def label_rule(signals: QuerySignals) -> RuleBucket:
    """단일 질의 신호를 결정적 규칙 버킷으로 라벨링한다."""
    if signals.is_non_retrieve():
        return RuleBucket.NON_RETRIEVE
    if signals.is_zero_hit():
        return RuleBucket.ZERO_HIT
    if signals.is_thin():
        return RuleBucket.THIN
    if signals.is_skew():
        return RuleBucket.SKEW
    if signals.was_retried():
        return RuleBucket.RETRIED
    return RuleBucket.NORMAL


def label_all(signals_list: list[QuerySignals]) -> list[RuleBucket]:
    """다건 일괄 라벨링."""
    return [label_rule(s) for s in signals_list]
