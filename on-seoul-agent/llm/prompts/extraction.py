"""구조화 추출 프롬프트 상수."""

from langchain_core.prompts import ChatPromptTemplate

EXTRACTION_PROMPT_FULL = ChatPromptTemplate.from_messages([
    (
        "system",
        """\
당신은 서울시 공공서비스 예약 시설의 메타데이터를 구조화 추출하는 전문가입니다.
주어진 시설 정보와 상세 내용에서 다음 필드를 추출하세요.
추출할 수 없는 필드는 null로 반환하세요.

- fee: 이용 요금 (예: "무료", "성인 5000원/회")
- operating_hours: 운영 시간 (예: "평일 09:00-18:00")
- cancellation: 취소·환불 정책 요약
- facilities: 시설 편의시설 목록 (배열)
- capacity: 정원 또는 수용 인원 (예: "30명")
- restrictions: 이용 제한 사항 목록 (배열)
- summary: 시설 한 줄 요약 (50자 이내, Track B 임베딩 입력용)
""",
    ),
    (
        "human",
        """\
시설명: {service_name}
지역: {area_name}
대분류: {max_class_name}
소분류: {min_class_name}
장소: {place_name}
대상: {target_info}
결제 유형: {payment_type}

상세 내용:
{cleaned_detail}
""",
    ),
])

EXTRACTION_PROMPT_METADATA_ONLY = ChatPromptTemplate.from_messages([
    (
        "system",
        """\
당신은 서울시 공공서비스 예약 시설의 메타데이터를 구조화 추출하는 전문가입니다.
상세 내용이 짧거나 없으므로 시설명과 기타 메타데이터만으로 가능한 필드를 추출하세요.
추출할 수 없는 필드는 null로 반환하세요.

- fee: 이용 요금
- operating_hours: 운영 시간
- cancellation: 취소·환불 정책 요약
- facilities: 시설 편의시설 목록 (배열)
- capacity: 정원 또는 수용 인원
- restrictions: 이용 제한 사항 목록 (배열)
- summary: 시설 한 줄 요약 (50자 이내, Track B 임베딩 입력용)
""",
    ),
    (
        "human",
        """\
시설명: {service_name}
지역: {area_name}
대분류: {max_class_name}
소분류: {min_class_name}
장소: {place_name}
대상: {target_info}
결제 유형: {payment_type}

상세 내용: (없음)
""",
    ),
])
