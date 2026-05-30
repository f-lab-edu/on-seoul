"""Korean query tokenizer — Kiwi(kiwipiepy) 기반 형태소 분석기 래퍼.

kiwipiepy가 설치되지 않은 환경에서도 임포트 가능하도록
ImportError를 포착해 폴백 토크나이저(공백 분리)로 대체한다.

BM25 검색 품질을 위해 의미 있는 품사(체언·용언 어간)만 추출한다.
입자·어미·특수문자 등은 제거하여 불필요한 BM25 노이즈를 줄인다.
"""

import logging

logger = logging.getLogger(__name__)

DOMAIN_TOKENS: frozenset[str] = frozenset(
    {
        "따릉이",
        "한강공원",
        "세빛섬",
        "노들섬",
        "광화문광장",
        "DDP",
        "ddp",
    }
)

# BM25 검색에 의미 있는 품사 태그 (kiwipiepy 기준).
# 체언(N*): 일반명사/고유명사/의존명사/수사, 용언 어간(V*, XR): 동사/형용사/보조용언/어근
# SL: 외국어, SH: 한자, SN: 숫자
_CONTENT_POS: frozenset[str] = frozenset(
    {
        "NNG",  # 일반명사
        "NNP",  # 고유명사
        "NNB",  # 의존명사
        "NR",   # 수사
        "VV",   # 동사
        "VA",   # 형용사
        "VX",   # 보조용언
        "XR",   # 어근
        "SL",   # 외국어
        "SH",   # 한자
        "SN",   # 숫자
    }
)

try:
    from kiwipiepy import Kiwi as _Kiwi

    _kiwi = _Kiwi()

    def _kiwi_tokenize(text: str) -> list[str]:
        """Kiwi 형태소 분석 — 의미 품사만 추출."""
        return [
            token.form
            for token in _kiwi.tokenize(text)
            if token.tag in _CONTENT_POS
        ]

    logger.debug("kiwipiepy 토크나이저 초기화 완료")

except ImportError:
    import re as _re

    logger.warning("kiwipiepy not installed, falling back to whitespace tokenizer")

    def _kiwi_tokenize(text: str) -> list[str]:  # type: ignore[misc]
        """kiwipiepy 미설치 환경 폴백: 공백·구두점 분리."""
        return [tok for tok in _re.split(r"[\s,]+", text) if tok]


# 내부 토크나이즈 함수 (하위 호환 별칭 유지)
_lindera_tokenize = _kiwi_tokenize


def tokenize_query(text: str) -> list[str]:
    """사용자 쿼리를 형태소 단위로 분리한다.

    1. Lindera KoDic으로 형태소 분석을 수행한다.
    2. 입력 텍스트 전체가 DOMAIN_TOKENS에 등록된 경우, lindera가 해당 용어를
       분리했더라도 원문을 결과 앞에 추가한다.
    3. 입력 텍스트 내에 DOMAIN_TOKENS 항목이 부분 포함된 경우, 해당 도메인
       토큰이 분석 결과에 포함되어 있지 않으면 앞에 추가한다. 이미 포함되어
       있으면 추가하지 않는다.

    Parameters
    ----------
    text:
        원본 사용자 쿼리 문자열.

    Returns
    -------
    list[str]
        형태소 토큰 리스트. 도메인 용어는 원형이 보존된다.
    """
    if not text:
        return []

    tokens = _lindera_tokenize(text)

    preserved: list[str] = []

    # 입력 전체가 도메인 용어인 경우 — 원문을 앞에 추가 (중복 제거)
    if text in DOMAIN_TOKENS:
        if text not in tokens:
            preserved.append(text)
        # 이미 토큰에 포함된 경우 중복 추가하지 않는다
        return preserved + tokens

    # 입력 문장 내 도메인 용어 부분 포함 처리
    for domain_token in DOMAIN_TOKENS:
        if domain_token in text and domain_token not in tokens:
            preserved.append(domain_token)

    if preserved:
        return preserved + tokens

    return tokens
