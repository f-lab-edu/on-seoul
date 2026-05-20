"""detail_content 사전 정제 모듈.

공공서비스 예약 데이터의 detail_content 필드는 HTML 템플릿 구조를 가지며
"3. 상세내용" ~ "4. 주의사항" 구간에 변별 정보가 집중된다.
해당 구간만 추출하여 임베딩 품질을 높인다.
"""

START_MARKER = "3. 상세내용"
END_MARKER = "4. 주의사항"


def clean_detail_content(raw: str | None) -> str:
    """detail_content에서 boilerplate를 제거하고 변별 정보 구간만 반환.

    - 시작 마커 없으면 원문 전체 반환 (fallback)
    - 종료 마커 없으면 시작 마커 이후 끝까지 반환
    - None 또는 빈 문자열이면 빈 문자열 반환
    """
    if not raw:
        return ""

    start_idx = raw.find(START_MARKER)
    if start_idx == -1:
        return raw

    content_start = start_idx + len(START_MARKER)

    end_idx = raw.find(END_MARKER, content_start)
    if end_idx == -1:
        return raw[content_start:].strip()

    return raw[content_start:end_idx].strip()
