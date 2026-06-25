"""detail_content 노출 전 정제·발췌 — 운영-상세(P5) 순수 셰이핑.

`prepare_detail_excerpt(raw, query_keywords) -> str | None` 는 DB/LLM/state 와 무관한
순수·결정적 함수다. answer 책임 경계(상위 §2.3): fetch·정제·발췌·키워드매칭은 상류
(여기)가 담당하고 answer 는 준비된 발췌 문자열만 소비한다. 선례: agents/_reference_resolution.

파이프라인 (P5 §4 확정값):
  1. hwpjson 블록 제거(<!--[data-hwpjson]…-->) — 길이 outlier(교육강좌 641KB) 제거
  2. HTML 엔티티 디코드 → 태그 strip — 교육/진료·문화체험에 효과, 평문 카테고리엔 no-op
  3. 공백 정규화 — 연속 줄바꿈/탭/공백 축약
  4. 경계 — "3. 상세내용"부터 끝까지(섹션4 포함). 마커 없으면 선두 보일러플레이트만 제거
     ※ head-truncation 금지: 폭염 답의 64%·우천 41%가 "4. 주의사항"(후반)에 있음(§3.4).
  5. 질의 키워드 span 탐색 — 정제 본문에서 매칭 위치(다중 매칭 모두)
  6. 키워드 중심 발췌 윈도우 — 매칭 위치 ±윈도우, 다중 매칭이면 구간 병합(상한 ~2,000자)
  7. 모바일 PII 마스킹 — 01[016-9]-?\\d{3,4}-?\\d{4}
  8. 답변가능 게이트 — 질의 키워드 본문 부재 → None / 길이<50 → None(거짓 발췌·환각 차단)

DRY(OI-7 연계): hwpjson/엔티티/태그/공백 primitive 는 scripts/cleaning/detail_content
(임베딩 정제)와 개념상 겹치나, 런타임이 scripts/(오프라인)를 import 하지 않도록 여기
자족 구현한다(경계 3→끝·키워드 발췌·PII 는 런타임 전용). 임베딩 정제기 자체의 섹션4
폐기 재검토는 OI-7(별도 PR) 범위.
"""

import html
import re

# 정제 마커 — "3. 상세내용"부터 끝까지(섹션4 포함). 임베딩 정제기와 달리 종료 마커를
# 두지 않는다(§3.4: 운영 답변 상당량이 "4. 주의사항" 뒤에 있어 잘라내면 안 됨).
_DETAIL_START_MARKER = "3. 상세내용"

# hwpjson 블록 — 한글 문서 JSON 이 본문에 박혀있는 주석 블록(86건, 길이 outlier 주범).
_HWPJSON_RE = re.compile(r"<!--\[data-hwpjson\].*?-->", re.DOTALL)
# 태그 strip — 비백트래킹 보강(보안): inner 클래스에서 `<` 도 제외하고 길이를 상한
# (실 HTML 태그는 짧다). `>` 없는 bare `<` 무리에서 직전 `<[^>]+>` 가 매 `<` 마다
# 문자열 끝까지 스캔(O(n²))하던 것을, `[^<>]` 가 다음 `<` 에서 즉시 실패시켜 막는다.
# 길이 캡(_CLEAN_INPUT_CAP)이 1차 결정적 방어이고 이 보강은 defense-in-depth.
_TAG_RE = re.compile(r"<[^<>]{0,2048}>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_MULTI_NL_RE = re.compile(r"\n{2,}")

# 입력 길이 캡(ReDoS 방어) — detail_content 는 서울 API 외부 원본(최대 641KB)이고 상류
# 길이 캡이 없다. `<` 가 많고 `>` 가 없는 입력이면 _TAG_RE 가 O(n²) 백트래킹해 동기
# 호출(retrieval._prepare_operational_detail)이 이벤트 루프를 9.5~45초 블록(DoS).
# 결정적 방어로 hwpjson 제거 *후* 남은 텍스트를 64KB 로 절단한 뒤 태그 strip 한다.
# 근거(OI-6 Q2): hwpjson 제거 후 p99≈45KB 라 64KB 캡은 섹션4 실내용을 보존한다(무손실).
_CLEAN_INPUT_CAP = 64 * 1024

# 경계 마커 breakout(프롬프트 인젝션) 중화 — detail_content 에 리터럴
# `---EXCERPT_START/END---`(또는 일반 `---...---` 경계 런)가 있으면 answer system
# 프롬프트의 데이터 구역(---EXCERPT_START---~---EXCERPT_END---)을 조기 종료하고
# post-boundary 지시를 주입할 수 있다. 반환 전에 (1) fence 라벨 마커(선례:
# agents/_intake_indexing._FENCE)와 (2) 일반 3+ 하이픈 경계 런을 평탄화한다. 본문의
# 범위 대시(09-18시 등)는 하이픈 2개 이하라 보존된다.
#
# 이중 안전장치(SHOULD-FIX 하드닝): 실 펜스는 바이트-정확이라 변형은 진짜 breakout
# 으로 기능하진 않지만(저위험), 외부입력→LLM 프롬프트 보안 경로라 변형까지 막는다.
#   (a) 라벨 토큰을 대소문자 무시(re.I)로 잡아 라벨 자체(EXCERPT_END 등)를 제거한다.
#   (b) 라벨을 둘러싼 대시·공백 섞인 런(`- - - EXCERPT_END - - -`, `--- excerpt_end ---`)
#       까지 한 매치로 흡수해 펜스 모양 잔여물을 남기지 않는다.
# 과도중화 방지: `\w+_(?:START|END)` 라벨 패턴에 한정한다. 막연한 `start`/`end` 영어
# 단어는 `_(START|END)` 형태가 아니라 매칭되지 않고, 가격 `1,000-2,000원`·시간 `09-18시`
# 의 하이픈 2개 이하 런은 _DASH_RUN_RE(3+) 와 라벨 인접 런 어디에도 걸리지 않는다.
_DASH_SPACE_RUN = r"(?:[-\s]*-[-\s]*)?"  # 대시 1개 이상 + 공백 섞임. 없어도 됨.
_FENCE_LABEL_RE = re.compile(
    rf"{_DASH_SPACE_RUN}\b\w*_(?:START|END)\b{_DASH_SPACE_RUN}",
    re.IGNORECASE,
)
_DASH_RUN_RE = re.compile(r"-{3,}")

# 진짜 개인 휴대폰 번호(강사·담당자 연락처). rrn형은 오탐·이메일은 원천 마스킹이라 제외.
_MOBILE_RE = re.compile(r"01[016-9][-\s.]?\d{3,4}[-\s.]?\d{4}")
_MOBILE_MASK = "[연락처 비공개]"

# 발췌 윈도우 — 키워드 매칭 위치 기준 앞/뒤. 다중 매칭 병합 시 상한.
_WINDOW_BEFORE = 200
_WINDOW_AFTER = 1300
_MERGE_CAP = 2000

# 운영 주제 동의어 그룹 — 질의에 한 동의어가 있으면 그룹 전체를 본문 검색어로 쓴다
# (예: "폭염" 질의가 본문의 "무더위"도 잡도록). §3.1 OI-6 실신호 기반(폭염·우천·
# 휴무·주차가 핵심). caution(주의사항)은 전건 섹션 마커라 의미 신호가 아니므로 제외.
_OPERATIONAL_SYNONYMS: tuple[tuple[str, ...], ...] = (
    ("폭염", "혹서", "무더위"),
    ("우천", "우산", "강우", "비"),
    ("한파", "적설", "결빙", "폭설"),
    ("휴무", "휴장", "휴관"),
    ("주차",),
    ("안전",),
    ("준비물", "지참"),
    ("이용안내", "이용 안내"),
)


def extract_operational_keywords(message: str) -> list[str]:
    """사용자 발화에서 운영 주제 키워드(+동의어 그룹)를 추출한다.

    질의에 한 동의어라도 있으면 그 그룹 전체를 검색어로 펼친다(본문이 다른 동의어로
    적혀 있어도 발췌 매칭). 매칭 그룹이 없으면 빈 리스트(→ 발췌 게이트가 None 처리).
    """
    if not message:
        return []
    out: list[str] = []
    for group in _OPERATIONAL_SYNONYMS:
        if any(syn in message for syn in group):
            out.extend(group)
    return out


# 답변가능 게이트 — boilerplate 제거 후 길이가 이 미만이면 (a) 무력.
# §4 는 명목상 50 자를 제시하나, 1차 게이트는 "질의 키워드 존재"이고 길이는 trivial
# 발췌(키워드만 단독 등장)를 거르는 2차 안전망이다. 실 데이터(body p50≈2,000)에선
# 50/20 차이가 무의미하고, 운영지침 한 문장(~20-40자)도 유효 답변일 수 있어 20 자로
# 둔다(키워드 부재 게이트가 거짓 발췌의 1차 방어).
_MIN_LEN = 20


def _clean(raw: str) -> str:
    """1~3단계: hwpjson 제거 → 길이 캡 → 태그 strip → 엔티티 디코드 → 공백 정규화.

    ※ 태그 strip 을 엔티티 디코드보다 *먼저* 한다(§4 의 명목 순서와 반대). 디코드를
    먼저 하면 escaped 컨텐츠(&lt;실내&gt;)가 태그 모양(<실내>)으로 변해 strip 에 잘려
    본문이 손실된다. 진짜 태그(<br>/<p>)는 literal 이라 먼저 제거되고, escaped 엔티티는
    디코드 후 보존된다.

    ※ ReDoS 방어(보안): hwpjson 제거 *직후* 남은 텍스트를 _CLEAN_INPUT_CAP 으로 절단한
    뒤 _TAG_RE 를 적용한다. detail_content 는 상류 길이 캡 없는 외부 원본(최대 641KB)이고
    `>` 없는 `<` 무리에서 _TAG_RE 가 O(n²) 백트래킹하므로, 캡이 결정적 방어다. hwpjson
    제거를 먼저 둬야 거대 hwpjson 블록이 캡 예산을 먹지 않고 섹션4 실내용이 보존된다.
    """
    text = _HWPJSON_RE.sub(" ", raw)
    if len(text) > _CLEAN_INPUT_CAP:
        text = text[:_CLEAN_INPUT_CAP]
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    # 줄 단위 공백 축약 후 다중 줄바꿈을 단일 줄바꿈으로.
    text = _WS_RE.sub(" ", text)
    text = _MULTI_NL_RE.sub("\n", text)
    return text.strip()


def _apply_boundary(text: str) -> str:
    """4단계: "3. 상세내용" 마커부터 끝까지(섹션4 포함). 마커 없으면 전체 사용."""
    start = text.find(_DETAIL_START_MARKER)
    if start != -1:
        return text[start + len(_DETAIL_START_MARKER) :].strip()
    # 마커 없음(0.9%) — 시작 마커가 없는 평문 폴백이라 전체 본문을 그대로 사용한다
    # (선두 보일러플레이트 제거는 시작 마커 경유로만 일어나며, 마커 없는 입력엔
    # 신뢰할 섹션 경계가 없어 head-cut 으로 본문을 잃는 것보다 전체 사용이 안전).
    return text.strip()


def _neutralize_boundaries(text: str) -> str:
    """경계 마커 breakout 중화: fence 라벨 마커·3+ 하이픈 런을 공백으로 평탄화한다.

    본문에 박힌 ---EXCERPT_START/END--- (또는 일반 ---...---)가 answer system 의 데이터
    구역을 조기 종료해 post-boundary 지시를 주입하는 인젝션을 차단한다. 본문의 정상
    범위 대시(09-18시)는 하이픈이 2개 이하라 영향받지 않는다.
    """
    text = _FENCE_LABEL_RE.sub(" ", text)
    return _DASH_RUN_RE.sub(" ", text)


def _find_spans(text: str, keywords: list[str]) -> list[int]:
    """5단계: 질의 키워드의 매칭 시작 위치(다중 매칭 모두)를 오름차순으로 반환."""
    positions: list[int] = []
    for kw in keywords:
        if not kw:
            continue
        idx = text.find(kw)
        while idx != -1:
            positions.append(idx)
            idx = text.find(kw, idx + 1)
    return sorted(set(positions))


def _excerpt_windows(text: str, positions: list[int]) -> str:
    """6단계: 키워드 중심 발췌 윈도우. 겹치는 구간 병합, 상한 ~2,000자.

    각 매칭 위치 ±윈도우로 [start, end] 구간을 만들고, 겹치거나 인접하면 병합한다.
    병합 결과를 누적하되 총 길이가 _MERGE_CAP 을 넘으면 중단(다중 매칭 보존 우선).
    """
    intervals: list[tuple[int, int]] = []
    for pos in positions:
        start = max(0, pos - _WINDOW_BEFORE)
        end = min(len(text), pos + _WINDOW_AFTER)
        if intervals and start <= intervals[-1][1]:
            # 겹침/인접 → 직전 구간과 병합.
            intervals[-1] = (intervals[-1][0], max(intervals[-1][1], end))
        else:
            intervals.append((start, end))

    parts: list[str] = []
    total = 0
    for start, end in intervals:
        segment = text[start:end].strip()
        if total + len(segment) > _MERGE_CAP:
            segment = segment[: _MERGE_CAP - total]
            parts.append(segment)
            break
        parts.append(segment)
        total += len(segment)
    return " … ".join(p for p in parts if p)


def _mask_pii(text: str) -> str:
    """7단계: 모바일 휴대폰 번호 마스킹."""
    return _MOBILE_RE.sub(_MOBILE_MASK, text)


def prepare_detail_excerpt(
    raw: str | None,
    query_keywords: list[str],
) -> str | None:
    """detail_content 원문에서 질의 키워드 중심 발췌본을 준비한다(노출 전 정제 완료).

    Parameters
    ----------
    raw:
        focal 시설 detail_content 원문(tools/fetch_detail_content 가 조회). None/빈 →
        답변 불가.
    query_keywords:
        사용자 질의에서 추출한 운영 키워드(폭염·우천·휴무·주차…). 비면 답변 불가.

    Returns
    -------
    str | None
        키워드 중심 발췌 + PII 마스킹 완료 문자열(≤ ~2,000자). 키워드 본문 부재·
        길이<50자 → None(답변가능 게이트: 거짓 발췌·환각 차단, P4 interim 폴백 신호).
    """
    if not raw or not query_keywords:
        return None

    cleaned = _clean(raw)
    body = _apply_boundary(cleaned)
    if len(body) < _MIN_LEN:
        return None

    positions = _find_spans(body, query_keywords)
    if not positions:
        # 8단계 게이트: 질의 키워드가 본문에 없음 → (a) 무력(거짓 발췌 방지).
        return None

    excerpt = _excerpt_windows(body, positions)
    excerpt = _mask_pii(excerpt)
    # 인젝션 방어 — answer 마커 조립(answer_agent ---EXCERPT_START/END---)에 넘기기 전
    # 본문에 박힌 경계 런을 중화해 데이터 구역 조기 종료(프롬프트 breakout)를 막는다.
    excerpt = _neutralize_boundaries(excerpt)
    # 중화로 생긴 연속 공백을 다시 단일 스페이스로 축약한 뒤 strip.
    excerpt = _WS_RE.sub(" ", excerpt).strip()
    if len(excerpt) < _MIN_LEN:
        return None
    return excerpt
