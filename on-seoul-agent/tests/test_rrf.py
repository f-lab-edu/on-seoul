"""core/rrf.py 단위 테스트.

reciprocal_rank_fusion 함수의 기본 동작, 가중치, 중복 제거, 빈 채널 처리를 검증한다.
"""

from core.rrf import reciprocal_rank_fusion


class TestReciprocalRankFusion:
    def test_unweighted_basic_merge(self):
        """S2가 두 채널 모두 1위/2위로 S1보다 상위에 있으면 RRF 결과 1위가 S2여야 한다."""
        channels = {
            "a": ["S2", "S1", "S3"],
            "b": ["S2", "S3", "S1"],
        }
        result = reciprocal_rank_fusion(channels)
        ids = [sid for sid, _ in result]
        assert ids[0] == "S2"

    def test_weighted_emphasis(self):
        """weights={"a": 1.0, "b": 0.1} 일 때 a 채널 1위 S1이 최종 1위여야 한다."""
        channels = {
            "a": ["S1", "S2"],
            "b": ["S2", "S1"],
        }
        result = reciprocal_rank_fusion(channels, weights={"a": 1.0, "b": 0.1})
        ids = [sid for sid, _ in result]
        assert ids[0] == "S1"

    def test_dedup_within_channel(self):
        """동일 service_id가 채널 내에 중복 등장하면 최고 rank(첫 등장)만 사용한다."""
        channels = {
            "a": ["S1", "S1", "S2"],
        }
        result = reciprocal_rank_fusion(channels)
        ids = [sid for sid, _ in result]
        # S1 중복이므로 결과에 S1이 1번만 있어야 한다
        assert ids.count("S1") == 1
        assert ids[0] == "S1"

    def test_empty_channel_ignored(self):
        """빈 채널이 있어도 나머지 채널로 결과를 정상 반환한다."""
        channels = {
            "a": ["S1", "S2"],
            "b": [],
        }
        result = reciprocal_rank_fusion(channels)
        ids = [sid for sid, _ in result]
        assert "S1" in ids
        assert "S2" in ids

    def test_all_empty_returns_empty(self):
        """모든 채널이 비어 있으면 빈 리스트를 반환한다."""
        channels = {
            "a": [],
            "b": [],
        }
        result = reciprocal_rank_fusion(channels)
        assert result == []

    def test_single_channel(self):
        """채널 1개도 정상 동작한다."""
        channels = {"a": ["S3", "S1", "S2"]}
        result = reciprocal_rank_fusion(channels)
        ids = [sid for sid, _ in result]
        assert ids == ["S3", "S1", "S2"]

    def test_rrf_score_formula(self):
        """1/(k+rank) 공식대로 점수가 계산된다."""
        k = 60
        channels = {"a": ["S1"]}
        result = reciprocal_rank_fusion(channels, k_constant=k)
        assert len(result) == 1
        sid, score = result[0]
        assert sid == "S1"
        expected = 1.0 / (k + 1)
        assert abs(score - expected) < 1e-9

    def test_result_sorted_by_score_descending(self):
        """결과는 rrf_score 내림차순으로 정렬된다."""
        channels = {
            "a": ["S1", "S2", "S3"],
            "b": ["S3", "S2", "S1"],
        }
        result = reciprocal_rank_fusion(channels)
        scores = [score for _, score in result]
        assert scores == sorted(scores, reverse=True)

    def test_weights_none_uses_uniform(self):
        """weights=None 이면 모든 채널 가중치 1.0으로 처리된다."""
        channels = {
            "a": ["S1"],
            "b": ["S1"],
        }
        result_none = reciprocal_rank_fusion(channels, weights=None)
        result_uniform = reciprocal_rank_fusion(channels, weights={"a": 1.0, "b": 1.0})
        assert abs(result_none[0][1] - result_uniform[0][1]) < 1e-9
