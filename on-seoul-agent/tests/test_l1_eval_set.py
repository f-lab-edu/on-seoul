"""평가셋 ②(회귀/품질 고정 데이터셋) 스캐폴딩 단위 테스트."""

from unittest.mock import MagicMock

from scripts.l1_eval.eval_set import (
    CURATED_CASES,
    EvalCase,
    push_dataset,
)


class TestCuratedCases:
    def test_covers_required_failure_families(self):
        # 계획서 §6: 단순 대표(critic 미발동 가드), thin/skew, 0건, intent오선택, drift.
        expected = {
            "simple_no_critic",
            "thin",
            "skew",
            "zero_hit",
            "intent_mispick",
            "drift",
        }
        families = {c.family for c in CURATED_CASES}
        assert expected <= families

    def test_every_case_has_expected_behavior_labels(self):
        for c in CURATED_CASES:
            assert c.query
            assert c.expected_intent is not None
            # critic 발동 기대는 명시적 bool 이어야 한다(미발동 회귀 가드 포함).
            assert isinstance(c.expected_critic_fires, bool)
            assert c.min_results >= 0

    def test_simple_case_is_critic_no_fire_guard(self):
        simple = [c for c in CURATED_CASES if c.family == "simple_no_critic"]
        assert simple
        assert all(c.expected_critic_fires is False for c in simple)

    def test_failure_cases_expect_critic_fire(self):
        for fam in ("thin", "skew", "zero_hit", "drift"):
            cases = [c for c in CURATED_CASES if c.family == fam]
            assert cases
            assert all(c.expected_critic_fires for c in cases)


class TestDatasetPush:
    def test_push_dry_run_returns_items_without_client(self):
        # 드라이런: Langfuse 없이 등록될 아이템 페이로드를 그대로 산출.
        items = push_dataset(CURATED_CASES, client=None, dataset_name="l1-eval", dry_run=True)
        assert len(items) == len(CURATED_CASES)
        first = items[0]
        assert "input" in first
        assert "expected_output" in first
        assert "metadata" in first
        assert first["metadata"]["family"]

    def test_push_live_calls_client(self):
        client = MagicMock()
        cases = CURATED_CASES[:2]
        push_dataset(cases, client=client, dataset_name="l1-eval", dry_run=False)
        client.create_dataset.assert_called_once()
        assert client.create_dataset_item.call_count == 2

    def test_eval_case_serializes_expected_output(self):
        c = EvalCase(
            family="thin",
            query="q",
            expected_intent="SQL_SEARCH",
            expected_critic_fires=True,
            min_results=3,
            required_services=["svc-1"],
        )
        items = push_dataset([c], client=None, dataset_name="x", dry_run=True)
        eo = items[0]["expected_output"]
        assert eo["expected_intent"] == "SQL_SEARCH"
        assert eo["expected_critic_fires"] is True
        assert eo["min_results"] == 3
        assert eo["required_services"] == ["svc-1"]
