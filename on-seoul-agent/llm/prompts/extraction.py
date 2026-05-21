"""구조화 추출 프롬프트 상수."""

import json

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

# ---------------------------------------------------------------------------
# Few-shot 예시 — EXTRACTION_PROMPT_FULL 전용
#
# 예시 1: 체육시설·야간 풋살장 — 복잡한 요금 구조, restrictions/cancellation 구분
# 예시 2: 교육 프로그램·무료 — 대상 제한, 기간제, facilities 빈 배열
# ---------------------------------------------------------------------------
_FS1_INPUT = """\
시설명: 마포 난지천 풋살장-평일(야간)
지역: 마포구
대분류: 체육시설
소분류: 풋살장
장소: 난지천공원
대상: 일반 시민
결제 유형: 유료

상세 내용:
○ 이용요금: 야간(20:00~22:00) 6,000원/2시간 (조명료 포함)
○ 예약: 서울시공공서비스예약 온라인 선착순. 5명 이상 팀 단위만 신청 가능.
○ 취소: 이용 2일 전까지 취소 가능, 이후 불이익 발생.
○ 유의사항: 음주 후 이용 금지. 예약자 본인 직접 참석 필수. 용도 외 사용 금지.
○ 주차: 난지천공원 주차장 이용 가능 (유료).
"""
_FS1_OUTPUT = json.dumps({
    "fee": "6,000원/2시간 (야간 조명 포함)",
    "operating_hours": "평일 20:00-22:00",
    "cancellation": "이용 2일 전까지 취소 가능, 이후 취소 불이익 발생",
    "facilities": ["야간 조명", "주차장(유료)"],
    "capacity": None,
    "restrictions": [
        "5명 이상 팀 단위 예약만 가능",
        "음주 후 이용 금지",
        "예약자 본인 직접 참석 필수",
    ],
    "summary": (
        "마포구 난지천공원 인조잔디 풋살장 (평일 야간). "
        "야간 조명 포함 6,000원/2시간. "
        "5명 이상 팀 단위 온라인 예약 필수, 취소는 이용 2일 전까지 가능."
    ),
}, ensure_ascii=False)

_FS2_INPUT = """\
시설명: 2026 봄 어르신 스마트폰 활용 교육
지역: 노원구
대분류: 교육
소분류: 디지털 교육
장소: 노원구청 강당
대상: 만 60세 이상 노원구 거주 어르신
결제 유형: 무료

상세 내용:
○ 교육 기간: 2026.04.07~04.25 (매주 화·목, 총 6회)
○ 교육 시간: 오전 10:00~12:00
○ 교육 내용: 카카오톡 사용법, 유튜브 시청, 키오스크 이용 방법 등
○ 접수: 노원구 거주자만 신청 가능. 선착순 20명.
○ 취소: 교육 시작 3일 전까지 취소 가능.
"""
_FS2_OUTPUT = json.dumps({
    "fee": "무료",
    "operating_hours": "화·목 10:00-12:00 (2026.04.07-04.25, 총 6회)",
    "cancellation": "교육 시작 3일 전까지 취소 가능",
    "facilities": [],
    "capacity": "20명",
    "restrictions": ["만 60세 이상 노원구 거주자만 신청 가능"],
    "summary": (
        "노원구청 강당에서 진행하는 어르신 스마트폰 활용 교육 (무료). "
        "만 60세 이상 노원구 거주자 대상, 선착순 20명. "
        "카카오톡·유튜브·키오스크 사용법 등 화·목 총 6회 과정."
    ),
}, ensure_ascii=False)

# ---------------------------------------------------------------------------
# Few-shot 예시 — EXTRACTION_PROMPT_METADATA_ONLY 전용
#
# 상세 내용 없을 때: 추측 금지, 확실한 필드만 채우고 나머지 null 반환
# ---------------------------------------------------------------------------
_FS_META_INPUT = """\
시설명: 잠실실내보조체육관(농구장)
지역: 송파구
대분류: 체육시설
소분류: 농구장
장소: 잠실종합운동장
대상: 일반 시민
결제 유형: 유료

상세 내용: (없음)
"""
_FS_META_OUTPUT = json.dumps({
    "fee": None,
    "operating_hours": None,
    "cancellation": None,
    "facilities": [],
    "capacity": None,
    "restrictions": [],
    "summary": (
        "송파구 잠실종합운동장 내 실내 농구장. "
        "일반 시민 대상 유료 체육시설."
    ),
}, ensure_ascii=False)


EXTRACTION_PROMPT_FULL = ChatPromptTemplate.from_messages([
    (
        "system",
        f"""\
당신은 서울시 공공서비스 예약 시설의 메타데이터를 구조화 추출하는 전문가입니다.
주어진 시설 정보와 상세 내용에서 아래 필드를 추출하세요.

{_FIELD_GUIDE}""",
    ),
    ("human", _FS1_INPUT),
    ("ai", _FS1_OUTPUT),
    ("human", _FS2_INPUT),
    ("ai", _FS2_OUTPUT),
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
    ("human", _FS_META_INPUT),
    ("ai", _FS_META_OUTPUT),
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
