"""HyQE 예상질문 생성 프롬프트."""

from langchain_core.prompts import ChatPromptTemplate

HYQE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        """\
당신은 서울시 공공서비스 예약 시설에 대해 시민이 물어볼 법한 예상 질문을 생성하는 전문가입니다.
주어진 시설 정보를 바탕으로 {n}개의 예상 질문을 JSON 배열로 생성하세요.

각 질문은 다음 형식의 JSON 객체여야 합니다:
{{"question_text": "질문 내용", "intent_label": "semantic|detail|keyword"}}

intent_label 분류 기준:
- semantic: 시설의 성격·용도·특징에 관한 의미 기반 질문 (예: "아이와 함께 체험할 수 있는 시설인가요?")
- detail: 구체적인 운영 정보에 관한 질문 (예: "이용 요금은 얼마인가요?", "몇 시에 문을 여나요?")
- keyword: 시설명·지역·분류 키워드 기반 직접 검색형 질문 (예: "강남구 헬스장", "체육시설 예약")

분포 목표: semantic 50%, detail 30%, keyword 20% (±10% 허용)

반드시 유효한 JSON 배열만 출력하고 다른 텍스트는 포함하지 마세요.
""",
    ),
    (
        "human",
        """\
시설명: {service_name}
지역: {area_name}
대분류: {max_class_name}
소분류: {min_class_name}
한 줄 요약: {extracted_summary}

상세 내용:
{cleaned_detail}
""",
    ),
])
