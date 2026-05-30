"""scripts/cleaning/detail_content.py 단위 테스트."""

from scripts.cleaning.detail_content import clean_detail_content


class TestCleanDetailContent:
    def test_extracts_section_between_markers(self):
        raw = "1. 개요\n2. 방법\n3. 상세내용\n핵심 내용 여기\n4. 주의사항\n주의 내용"
        result = clean_detail_content(raw)
        assert "핵심 내용 여기" in result
        assert "1. 개요" not in result
        assert "4. 주의사항" not in result

    def test_missing_start_marker_returns_raw(self):
        raw = "전체 내용만 있고 마커 없음"
        result = clean_detail_content(raw)
        assert result == raw

    def test_missing_end_marker_takes_until_end(self):
        raw = "앞부분\n3. 상세내용\n마지막까지 내용"
        result = clean_detail_content(raw)
        assert result == "마지막까지 내용"
        assert "앞부분" not in result

    def test_none_returns_empty_string(self):
        result = clean_detail_content(None)
        assert result == ""

    def test_empty_string_returns_empty(self):
        result = clean_detail_content("")
        assert result == ""
