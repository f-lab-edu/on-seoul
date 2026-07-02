"""규칙 자동 라벨러 — state 신호만으로 결정적 버킷(LLM 불필요, 공짜).

우선순위(강한 실패 우선):
  ZERO_HIT > THIN > SKEW > RETRIED > NORMAL

근거: 0건은 가장 강한 실패라 재시도/thin 신호를 압도한다. thin/skew 는 결과가 있으나
품질이 약한 경우로 RETRIED(재시도 발생했으나 결과 자체 품질은 미상)보다 정보량이 크다.
건수 신호가 전혀 없는 구 트레이스는 실패로 단정하지 않고 NORMAL(하위호환).
"""

from __future__ import annotations

from scripts.l1_eval.signals import QuerySignals, RuleBucket


def label_rule(signals: QuerySignals) -> RuleBucket:
    """단일 질의 신호를 결정적 규칙 버킷으로 라벨링한다."""
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
