"""구조화 추출 프롬프트 상수."""

from langchain_core.prompts import ChatPromptTemplate

_FIELD_GUIDE = """\
다음 필드를 추출하세요. 상세 내용에 명시되지 않은 정보는 추측하지 말고 반드시 null로 반환하세요.

- fee: 이용 요금. 요금이 여러 구간(성인/어린이, 평일/주말, 시간대)으로 나뉘면
  "성인 3,000원·어린이 1,500원/회" 처럼 슬래시·가운뎃점으로 구분해 한 문장으로 압축.
  무료이면 "무료".
- operating_hours: 운영 시간. 시즌·요일별 차이가 있으면
  "하절기 06:00-22:00 / 동절기 07:00-21:00" 형식으로 슬래시 구분.
- cancellation: 취소·환불 정책만 요약. 이용 일반 제한은 restrictions에 기입.
  예: "이용 3일 전까지 전액 환불, 이후 취소 불가"
- facilities: 시설 편의시설 목록 (배열). 예: ["샤워실", "주차장", "탈의실"]
- capacity: 정원 또는 수용 인원. 예: "30명", "팀당 최대 11명"
- restrictions: cancellation 외 이용 제한 사항 목록 (배열).
  예: ["음주 금지", "반려동물 출입 금지", "개인 예약 불가 (팀 단위만)"]
- summary: 시설 한 줄 설명 (150자 이내). 검색 임베딩 입력용이므로
  지역·시설명·종목·이용 대상·요금·핵심 운영 조건을 자연어로 압축할 것.
  예: "성동구 응봉공원 테니스 코트 (평일). 일반 시민 대상, 성인 2,000원/1시간.
  사전 온라인 예약 필수, 당일 취소 불가."
"""

EXTRACTION_PROMPT_FULL = ChatPromptTemplate.from_messages([
    (
        "system",
        f"""\
당신은 서울시 공공서비스 예약 시설의 메타데이터를 구조화 추출하는 전문가입니다.
주어진 시설 정보와 상세 내용에서 아래 필드를 추출하세요.

{_FIELD_GUIDE}""",
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
        f"""\
당신은 서울시 공공서비스 예약 시설의 메타데이터를 구조화 추출하는 전문가입니다.
상세 내용이 없으므로 시설명·메타데이터만으로 확실히 알 수 있는 필드만 채우세요.
불확실한 필드(fee, operating_hours, cancellation 등)는 추측하지 말고 null로 반환하세요.

{_FIELD_GUIDE}""",
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
