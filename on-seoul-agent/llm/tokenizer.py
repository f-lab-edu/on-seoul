"""하위 호환 re-export — tools/tokenizer로 이전됨.

tokenizer는 LLM API를 호출하지 않으므로 tools/ 디렉토리로 이동했다.
이 모듈은 기존 import 경로(llm.tokenizer)를 유지하기 위해 남겨 둔다.
"""

from tools.tokenizer import DOMAIN_TOKENS, tokenize_query

__all__ = ["DOMAIN_TOKENS", "tokenize_query"]
