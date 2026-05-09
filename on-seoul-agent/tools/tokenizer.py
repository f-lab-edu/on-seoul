"""Korean query tokenizer — Lindera KoDic 기반 형태소 분석기 래퍼.

lindera-py가 설치되지 않은 환경(CI, 로컬 개발)에서도 임포트 가능하도록
ImportError를 포착해 폴백 토크나이저(공백 분리)로 대체한다.

프로덕션 환경에서는 lindera-py 바이너리가 빌드·설치된 상태에서 실행된다.
"""

import logging

logger = logging.getLogger(__name__)

DOMAIN_TOKENS: frozenset[str] = frozenset({
    "따릉이",
    "한강공원",
    "세빛섬",
    "노들섬",
    "광화문광장",
    "DDP",
    "ddp",
})

try:
    from lindera_py import Tokenizer as _LinderaTokenizer

    _tokenizer = _LinderaTokenizer.from_config({"dictionary": {"kind": "KoDic"}})

    def _lindera_tokenize(text: str) -> list[str]:
        return [t.text for t in _tokenizer.tokenize(text)]

except ImportError:
    import re as _re

    logger.warning("lindera-py not installed, falling back to whitespace tokenizer")

    def _lindera_tokenize(text: str) -> list[str]:  # type: ignore[misc]
        """lindera-py 미설치 환경 폴백: 공백·구두점 분리."""
        return [tok for tok in _re.split(r"[\s,]+", text) if tok]


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
