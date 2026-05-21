"""core/rrf.py 엣지케이스 단위 테스트.

검증 절차 §2 에서 식별된 누락 경로:
  - weights dict에 일부 채널명이 없을 때 기본 1.0 처리 여부
  - 모든 채널이 동일 service_id만 포함할 때
"""

from core.rrf import reciprocal_rank_fusion


class TestRrfMissingWeightKey:
    def test_missing_channel_key_in_weights_defaults_to_1_0(self):
        """weights dict에 채널명이 없으면 weight=1.0으로 처리된다.

        weights={"a": 2.0} 만 있고 "b" 키가 없을 때:
          - "a" 채널 1위 S1 score = 2.0 / (60 + 1)
          - "b" 채널 1위 S2 score = 1.0 / (60 + 1)  (기본값 1.0)
        S1 > S2 여야 한다.
        """
        channels = {
            "a": ["S1"],
            "b": ["S2"],
        }
        result = reciprocal_rank_fusion(channels, weights={"a": 2.0})
        ids = [sid for sid, _ in result]
        assert ids[0] == "S1", "weights에 없는 채널은 weight=1.0이 사용되어야 한다"

    def test_no_weights_key_for_any_channel_is_equivalent_to_none(self):
        """weights에 어떤 채널 키도 없으면 (빈 dict) weights=None과 동일하게 동작한다."""
        channels = {
            "a": ["S1", "S2"],
            "b": ["S2", "S1"],
        }
        result_empty_weights = reciprocal_rank_fusion(channels, weights={})
        result_none_weights = reciprocal_rank_fusion(channels, weights=None)

        ids_empty = [sid for sid, _ in result_empty_weights]
        ids_none = [sid for sid, _ in result_none_weights]
        assert ids_empty == ids_none, "빈 weights dict는 weights=None과 같은 순서를 내야 한다"

    def test_scores_differ_when_weight_applied_to_one_channel(self):
        """한 채널에만 가중치가 적용되면 해당 채널 기여도가 달라진다."""
        channels = {
            "track_a": ["S1"],
        }
        score_weighted = reciprocal_rank_fusion(channels, weights={"track_a": 2.0})[0][1]
        score_default = reciprocal_rank_fusion(channels, weights=None)[0][1]
        assert abs(score_weighted - score_default * 2) < 1e-9


class TestRrfAllSameServiceId:
    def test_all_channels_same_service_id_appears_once(self):
        """모든 채널이 동일한 service_id만 포함할 때 결과에 1건만 있어야 한다."""
        channels = {
            "a": ["S1", "S1"],
            "b": ["S1"],
            "c": ["S1", "S1", "S1"],
        }
        result = reciprocal_rank_fusion(channels)
        assert len(result) == 1
        assert result[0][0] == "S1"

    def test_all_channels_same_single_id_score_accumulates_across_channels(self):
        """동일 service_id가 여러 채널에 1위로 등장하면 점수가 채널 수만큼 누적된다."""
        k = 60
        channels = {
            "a": ["S1"],
            "b": ["S1"],
            "c": ["S1"],
        }
        result = reciprocal_rank_fusion(channels, k_constant=k)
        _, score = result[0]
        # 3채널 각 1위 → 3 * (1 / (60 + 1))
        expected = 3 * (1.0 / (k + 1))
        assert abs(score - expected) < 1e-9

    def test_channel_dedup_before_cross_channel_accumulation(self):
        """채널 내부 dedup은 첫 등장만 사용하므로 같은 채널에서 중복 등장해도 1회만 누적한다."""
        k = 60
        channels = {
            "a": ["S1", "S1"],  # 두 번 등장하지만 첫 번째만 사용
        }
        result = reciprocal_rank_fusion(channels, k_constant=k)
        _, score = result[0]
        expected = 1.0 / (k + 1)  # 1번만
        assert abs(score - expected) < 1e-9
