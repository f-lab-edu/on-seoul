"""llm/tokenizer.py 단위 테스트.

lindera-py 바이너리 없이도 동작하도록 _lindera_tokenize 내부 함수를 patch한다.
실제 형태소 분석기 호출 없이 토크나이징 결과 변환 로직과 도메인 용어 보존을 검증한다.
"""

from unittest.mock import patch


from tools.tokenizer import DOMAIN_TOKENS, tokenize_query


class TestTokenizeQueryBasic:
    def test_returns_list_of_strings(self):
        """반환 타입이 list[str]이다."""
        with patch("tools.tokenizer._lindera_tokenize", return_value=["서울", "체험"]):
            result = tokenize_query("서울 체험")
        assert isinstance(result, list)
        assert all(isinstance(t, str) for t in result)

    def test_single_token(self):
        """단일 토큰이 리스트로 반환된다."""
        with patch("tools.tokenizer._lindera_tokenize", return_value=["수영"]):
            result = tokenize_query("수영")
        assert result == ["수영"]

    def test_multiple_tokens(self):
        """복수 토큰이 순서대로 반환된다. 도메인 용어 미포함 문장 기준."""
        with patch("tools.tokenizer._lindera_tokenize", return_value=["수영", "강습"]):
            result = tokenize_query("수영 강습")
        assert result == ["수영", "강습"]

    def test_empty_string_returns_empty_list(self):
        """빈 문자열은 빈 리스트를 반환한다."""
        with patch("tools.tokenizer._lindera_tokenize", return_value=[]):
            result = tokenize_query("")
        assert result == []


class TestDomainTokenPreservation:
    def test_domain_token_exact_match_prepended(self):
        """DOMAIN_TOKENS에 정확히 일치하는 텍스트는 원문이 토큰 앞에 추가된다."""
        domain_term = next(iter(DOMAIN_TOKENS))  # 첫 번째 도메인 토큰
        with patch("tools.tokenizer._lindera_tokenize", return_value=["분리", "토큰"]):
            result = tokenize_query(domain_term)
        assert result[0] == domain_term

    def test_domain_token_not_duplicated_if_already_preserved(self):
        """lindera가 도메인 용어를 그대로 반환하면 중복 추가하지 않는다."""
        with patch("tools.tokenizer._lindera_tokenize", return_value=["따릉이"]):
            result = tokenize_query("따릉이")
        # "따릉이"가 DOMAIN_TOKENS에 있으므로 원문을 앞에 추가하지만,
        # lindera 결과에도 이미 있으므로 중복 제거된다.
        assert result.count("따릉이") == 1

    def test_non_domain_token_not_prepended(self):
        """DOMAIN_TOKENS에 없는 일반 텍스트는 원문을 앞에 추가하지 않는다."""
        assert "수영장" not in DOMAIN_TOKENS
        with patch("tools.tokenizer._lindera_tokenize", return_value=["수영", "장"]):
            result = tokenize_query("수영장")
        assert result == ["수영", "장"]

    def test_partial_domain_token_in_sentence_preserved(self):
        """문장 내 도메인 용어가 포함된 경우 해당 토큰이 결과에 보존된다.

        예: "따릉이 대여소" → lindera가 ["따릉이", "대여소"]로 분리했을 때
        "따릉이"가 결과에 포함되어야 한다.
        """
        with patch("tools.tokenizer._lindera_tokenize", return_value=["따릉이", "대여소"]):
            result = tokenize_query("따릉이 대여소")
        assert "따릉이" in result

    def test_domain_token_split_case_restored(self):
        """lindera가 도메인 용어를 분리한 경우 원문 도메인 토큰을 앞에 추가한다.

        예: "따릉이" → lindera가 ["따릉", "이"]로 분리 → 결과는 ["따릉이", "따릉", "이"]
        """
        with patch("tools.tokenizer._lindera_tokenize", return_value=["따릉", "이"]):
            result = tokenize_query("따릉이")
        assert result[0] == "따릉이"
        assert "따릉" in result
        assert "이" in result

    def test_sentence_contains_domain_token_but_lindera_splits_it(self):
        """문장 내 도메인 용어를 lindera가 분리한 경우 원문 도메인 토큰이 결과 앞에 추가된다.

        예: "한강공원 근처 시설" → lindera가 ["한강", "공원", "근처", "시설"]로 분리 시
        "한강공원"이 결과 앞에 보존되어야 한다 (line 72 커버).
        """
        with patch("tools.tokenizer._lindera_tokenize", return_value=["한강", "공원", "근처", "시설"]):
            result = tokenize_query("한강공원 근처 시설")
        assert result[0] == "한강공원"
        assert "한강" in result
        assert "시설" in result

    def test_sentence_multiple_domain_tokens_split_all_preserved(self):
        """문장에 두 도메인 용어가 있고 둘 다 lindera가 분리한 경우 모두 앞에 추가된다.

        예: "따릉이 타고 한강공원 가기" → 두 도메인 토큰 모두 보존 (line 75 커버).
        """
        with patch(
            "tools.tokenizer._lindera_tokenize",
            return_value=["따릉", "이", "타고", "한강", "공원", "가기"],
        ):
            result = tokenize_query("따릉이 타고 한강공원 가기")
        assert "따릉이" in result
        assert "한강공원" in result

    def test_all_domain_tokens_defined(self):
        """DOMAIN_TOKENS가 비어 있지 않다."""
        assert len(DOMAIN_TOKENS) > 0


class TestDomainTokens:
    def test_domain_tokens_is_set_of_strings(self):
        """DOMAIN_TOKENS는 문자열 집합이다."""
        assert isinstance(DOMAIN_TOKENS, frozenset | set)
        assert all(isinstance(t, str) for t in DOMAIN_TOKENS)

    def test_known_domain_tokens_present(self):
        """주요 도메인 용어가 DOMAIN_TOKENS에 등록되어 있다."""
        assert "따릉이" in DOMAIN_TOKENS
        assert "한강공원" in DOMAIN_TOKENS
