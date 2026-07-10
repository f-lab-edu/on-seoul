"""L1 측정 도구 + 평가셋 스캐폴딩.

Agentic L1(retrieval-critic) 의 산출물을 만드는 *측정/평가 인프라*다.
코드 구현(critic 루프)이 아니라, 실 트래픽 실패 버킷 분포를 측정하고 회귀/품질용 고정
평가셋을 만들어 **사람의 결정 게이트**(L1 계속 vs L2 우선)에 수치를 입력한다.

구성:
  - signals.py       : 질의당 추출 신호 + 버킷 라벨 스키마 (단일 계약 출처)
  - extract.py       : Langfuse 트레이스 추출(라이브 / 드라이런 픽스처)
  - rule_labeler.py  : state 신호만으로 결정적 자동 라벨 (LLM 불필요)
  - llm_classifier.py: 판단 라벨(intent오선택/drift/복합-표현불가) LLM 분류기
  - aggregate.py     : 사람 검증 하네스 + 자동↔사람 일치율 + 버킷 분포 리포트
  - eval_set.py      : 평가셋 ②(회귀/품질) 큐레이션 + Langfuse Dataset 등록

결정 자체는 하지 않는다 — 스크립트는 분포/수치까지만 산출한다.
"""
