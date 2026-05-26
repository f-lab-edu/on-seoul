"""schemas/search.py 단위 테스트.

SearchKind / SearchChannel 상수, _CHANNEL_TO_KIND 매핑 완전성,
kind_of() 헬퍼 동작, ChannelData TypedDict 구조를 검증한다.
"""

import pytest

from schemas.search import (
    ChannelData,
    ChannelHit,
    ChannelQuery,
    SearchChannel,
    SearchKind,
    _CHANNEL_TO_KIND,
    kind_of,
)


class TestKindOf:
    def test_sql_channel(self):
        assert kind_of(SearchChannel.SQL) == SearchKind.SQL

    def test_vector_channel_phase1(self):
        assert kind_of(SearchChannel.VECTOR) == SearchKind.VECTOR

    def test_vector_a_returns_vector_kind(self):
        assert kind_of(SearchChannel.VECTOR_A) == SearchKind.VECTOR

    def test_vector_b_returns_vector_kind(self):
        assert kind_of(SearchChannel.VECTOR_B) == SearchKind.VECTOR

    def test_vector_c_returns_vector_kind(self):
        assert kind_of(SearchChannel.VECTOR_C) == SearchKind.VECTOR

    def test_hyde_vector_returns_vector_kind(self):
        assert kind_of(SearchChannel.HYDE_VECTOR) == SearchKind.VECTOR

    def test_bm25_channel(self):
        assert kind_of(SearchChannel.BM25) == SearchKind.BM25

    def test_rrf_channel(self):
        assert kind_of(SearchChannel.RRF) == SearchKind.RRF

    def test_map_channel(self):
        assert kind_of(SearchChannel.MAP) == SearchKind.MAP

    def test_final_channel(self):
        assert kind_of(SearchChannel.FINAL) == SearchKind.FINAL

    def test_unknown_channel_raises_value_error(self):
        with pytest.raises(ValueError, match="unknown channel"):
            kind_of("xyz")

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            kind_of("")

    def test_partial_match_raises_value_error(self):
        """'vector_' 처럼 접두어가 같아도 미등록이면 ValueError."""
        with pytest.raises(ValueError):
            kind_of("vector_")

    def test_error_message_contains_channel_name(self):
        """오류 메시지에 잘못된 채널명이 포함된다."""
        with pytest.raises(ValueError, match="future_channel"):
            kind_of("future_channel")


class TestAllKnownChannelsMapped:
    """SearchChannel 클래스에 정의된 모든 상수가 _CHANNEL_TO_KIND 에 등록되어야 한다."""

    def _get_channel_constants(self) -> dict[str, str]:
        return {
            name: value
            for name, value in vars(SearchChannel).items()
            if not name.startswith("_") and isinstance(value, str)
        }

    def test_all_channel_constants_in_mapping(self):
        constants = self._get_channel_constants()
        unmapped = [ch for ch in constants.values() if ch not in _CHANNEL_TO_KIND]
        assert unmapped == [], f"_CHANNEL_TO_KIND 에 누락된 채널: {unmapped}"

    def test_no_extra_channels_in_mapping(self):
        """매핑에는 있지만 SearchChannel 상수에 없는 채널이 있으면 안 된다."""
        constants = self._get_channel_constants()
        channel_values = set(constants.values())
        extras = [ch for ch in _CHANNEL_TO_KIND if ch not in channel_values]
        assert extras == [], f"SearchChannel 에 없는 매핑 키: {extras}"


class TestSearchKindValues:
    """SearchKind 상수 값이 DB CHECK 화이트리스트와 일치해야 한다."""

    DB_WHITELIST = {"sql", "vector", "bm25", "rrf", "map", "final"}

    def _get_kind_constants(self) -> set[str]:
        return {
            value
            for name, value in vars(SearchKind).items()
            if not name.startswith("_") and isinstance(value, str)
        }

    def test_kind_constants_match_db_whitelist(self):
        assert self._get_kind_constants() == self.DB_WHITELIST

    def test_mapping_values_are_valid_kinds(self):
        """모든 매핑 값이 SearchKind 상수 중 하나여야 한다."""
        valid_kinds = self._get_kind_constants()
        invalid = [
            kind for kind in _CHANNEL_TO_KIND.values() if kind not in valid_kinds
        ]
        assert invalid == [], f"유효하지 않은 kind 값: {invalid}"


class TestChannelDataStructure:
    """ChannelData / ChannelHit / ChannelQuery TypedDict 구조 기본 검증."""

    def test_channel_data_creation(self):
        data: ChannelData = {
            "kind": SearchKind.SQL,
            "query": {"query_text": "수영장", "parameters": {"top_k": 5}},
            "hits": [
                {"rank": 1, "service_id": "SVC001", "score": None, "meta": {}},
            ],
        }
        assert data["kind"] == "sql"
        assert data["query"]["query_text"] == "수영장"
        assert data["hits"][0]["rank"] == 1

    def test_channel_query_nullable_query_text(self):
        """rrf / final 채널은 query_text 가 None 이어야 한다."""
        q: ChannelQuery = {
            "query_text": None,
            "parameters": {"source_channels": ["vector", "bm25"]},
        }
        assert q["query_text"] is None

    def test_channel_hit_nullable_score(self):
        """SQL 채널처럼 score 개념 없는 경우 None."""
        hit: ChannelHit = {
            "rank": 1,
            "service_id": "SVC001",
            "score": None,
            "meta": {},
        }
        assert hit["score"] is None

    def test_channel_hit_with_meta(self):
        hit: ChannelHit = {
            "rank": 1,
            "service_id": "SVC001",
            "score": 0.92,
            "meta": {"intent_label": "체육시설"},
        }
        assert hit["meta"]["intent_label"] == "체육시설"
