"""Reciprocal Rank Fusion (RRF) 유틸리티.

가중 RRF로 복수의 service_id 랭킹 리스트를 결합한다.
"""


def reciprocal_rank_fusion(
    channels: dict[str, list[str]],
    *,
    weights: dict[str, float] | None = None,
    k_constant: int = 60,
) -> list[tuple[str, float]]:
    """가중 RRF로 service_id 리스트를 결합한다.

    Parameters
    ----------
    channels:
        채널명 → service_id 순위 리스트. 높은 순위(앞쪽)일수록 점수가 높다.
    weights:
        채널별 가중치. None이면 모든 채널 가중치 1.0.
    k_constant:
        RRF 공식 상수. 표준값 60.

    Returns
    -------
    list[tuple[str, float]]
        (service_id, rrf_score) 내림차순 정렬 리스트.

    Notes
    -----
    - 한 채널에서 같은 service_id가 여러 rank에 등장하면 최고 rank(첫 등장)만 사용한다.
    - 빈 채널은 무시한다.
    - rrf_score(service_id) = Σ over channels: weight[c] / (k_constant + rank[c, service_id])
    """
    scores: dict[str, float] = {}

    for channel, service_ids in channels.items():
        if not service_ids:
            continue

        weight = 1.0 if weights is None else weights.get(channel, 1.0)
        seen_in_channel: set[str] = set()

        for rank, sid in enumerate(service_ids, start=1):
            if sid in seen_in_channel:
                continue
            seen_in_channel.add(sid)
            scores[sid] = scores.get(sid, 0.0) + weight / (k_constant + rank)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
