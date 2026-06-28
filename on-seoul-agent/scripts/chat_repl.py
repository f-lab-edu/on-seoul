"""맥락유지형(multi-turn) 대화 관찰 스크립트.

목적
----
agentic 대화 개선(워킹셋/적응형 답변 등) 작업 전후로, 멀티턴 대화의
**과정(SSE 이벤트)** 과 **사용자 경험(final content)** 을 눈으로 관찰한다.

AI 서비스는 요청당 stateless 다(routers/chat.py). 턴 간 맥락은 Spring 릴레이가
ChatRequest 필드(history / prev_entities / prev_intent / prev_reasoning)로 실어
넘긴다. 이 스크립트가 그 릴레이를 흉내낸다 — 매 턴 final/decision 이벤트에서
carryover 를 추출·누적해 다음 요청에 조립해 넣는다(CarryoverState).

사용법
------
# 내장 시나리오 목록
uv run python scripts/chat_repl.py --list

# 내장 시나리오 실행 (서버 필요)
uv run python scripts/chat_repl.py --scenario room64_relevance

# 대화형 REPL (한 줄씩 입력; 빈 줄/`/quit` 종료, `/reset` carryover 초기화)
uv run python scripts/chat_repl.py --interactive

# 서버 주소·방·이력 캡·위치 옵션
uv run python scripts/chat_repl.py --scenario room64_skew \
    --base-url http://localhost:8000 --room-id 64 --max-history-turns 6 \
    --lat 37.5 --lng 127.0

서버가 떠 있어야 실제 대화가 가능하다(연결 실패 시 안내 메시지 출력).
서버 없이도 `--list`, `--help`, import/argparse 경로는 동작한다.

SSE 파싱 근거
-------------
SSE 프레임은 `id:`/`event:`/`data:` 라인과 빈 줄(프레임 경계)로 구성된다
(routers/chat.py:sse_frame). 한 프레임에서 `data:` 가 여러 줄이면 개행으로
이어붙인다(W3C SSE 규약). _SSEParser.feed() 는 청크 경계와 무관하게 바이트
스트림을 라인 단위로 누적·파싱하므로 멀티라인 data·프레임 경계를 정확히 처리한다
(하단 _selfcheck_sse_parser 단위 확인 참조).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 프로젝트 루트를 sys.path에 추가 (scripts/ 에서 실행 시 패키지 임포트를 위해)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

# ---------------------------------------------------------------------------
# 색상 (비-TTY면 자동 끔)
# ---------------------------------------------------------------------------
_USE_COLOR = sys.stdout.isatty()


def _c(code: str, text: str) -> str:
    if not _USE_COLOR:
        return text
    return f"\033[{code}m{text}\033[0m"


def _bold(t: str) -> str:
    return _c("1", t)


def _dim(t: str) -> str:
    return _c("2", t)


def _cyan(t: str) -> str:
    return _c("36", t)


def _green(t: str) -> str:
    return _c("32", t)


def _yellow(t: str) -> str:
    return _c("33", t)


def _red(t: str) -> str:
    return _c("31", t)


def _magenta(t: str) -> str:
    return _c("35", t)


# ---------------------------------------------------------------------------
# SSE 파서 — 청크 경계 무관 라인 누적 + 멀티라인 data 결합
# ---------------------------------------------------------------------------
@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]


class _SSEParser:
    """바이트 청크를 받아 완성된 SSE 프레임을 이벤트로 산출한다.

    W3C SSE 규약 부분 구현(이 서비스가 쓰는 형태만):
      - `event:` 라인은 이벤트 타입을 지정.
      - `data:` 라인이 여러 개면 개행으로 이어붙임.
      - 빈 줄이 프레임 경계 → 누적된 event/data 로 SSEEvent 방출.
      - `id:` 라인은 무시(이 스크립트는 사용 안 함).
    data 는 JSON 으로 파싱하되 실패 시 {"_raw": <원문>} 로 폴백한다.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._event = "message"
        self._data_lines: list[str] = []

    def feed(self, chunk: str) -> list[SSEEvent]:
        self._buf += chunk
        out: list[SSEEvent] = []
        # 완성된 라인만 처리(개행 없는 꼬리는 버퍼에 남겨 다음 청크와 합침).
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            line = line.rstrip("\r")
            evt = self._consume_line(line)
            if evt is not None:
                out.append(evt)
        return out

    def _consume_line(self, line: str) -> SSEEvent | None:
        if line == "":
            return self._flush()
        if line.startswith(":"):
            return None  # 주석 라인
        field_name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field_name == "event":
            self._event = value
        elif field_name == "data":
            self._data_lines.append(value)
        # id/기타 필드는 무시
        return None

    def _flush(self) -> SSEEvent | None:
        if not self._data_lines and self._event == "message":
            return None  # 빈 프레임
        raw = "\n".join(self._data_lines)
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"_raw": raw}
        evt = SSEEvent(event=self._event, data=data)
        self._event = "message"
        self._data_lines = []
        return evt


# ---------------------------------------------------------------------------
# Carryover 조립/추출 — Spring 릴레이 흉내 (확장 지점 한 곳에 집중)
# ---------------------------------------------------------------------------
@dataclass
class CarryoverState:
    """턴 간 맥락을 누적해 다음 ChatRequest 필드로 조립한다.

    Spring 릴레이가 chat_messages / 영속 service_cards 에서 조립하는 것을
    클라이언트 측에서 흉내낸다. 슬롯 추가(예: 향후 P1 의 prev_working_set)는
    이 클래스 한 곳만 확장하면 되도록 추출/조립을 모은다.

    계약(schemas/chat.py):
      history       : [{role, content}] — user 발화 + assistant answer 누적.
      prev_entities : [{service_id, label}] — 직전 final service_cards 에서.
      prev_intent   : 직전 final intent.
      prev_reasoning: 직전 decision 이벤트의 user_rationale.
    """

    max_history_turns: int = 6  # USER+ASSISTANT 쌍 단위 캡(최신 N쌍)

    history: list[dict[str, str]] = field(default_factory=list)
    prev_entities: list[dict[str, str]] = field(default_factory=list)
    prev_intent: str | None = None
    prev_reasoning: str | None = None

    def reset(self) -> None:
        self.history.clear()
        self.prev_entities.clear()
        self.prev_intent = None
        self.prev_reasoning = None

    def build_request_fields(self) -> dict[str, Any]:
        """현재 누적 상태를 ChatRequest 필드 dict 로 조립한다."""
        fields: dict[str, Any] = {"history": list(self.history)}
        if self.prev_entities:
            fields["prev_entities"] = list(self.prev_entities)
        if self.prev_intent is not None:
            fields["prev_intent"] = self.prev_intent
        if self.prev_reasoning is not None:
            fields["prev_reasoning"] = self.prev_reasoning
        return fields

    def record_turn(
        self,
        user_message: str,
        decision: SSEEvent | None,
        final: SSEEvent | None,
    ) -> None:
        """한 턴이 끝난 뒤 다음 턴용 carryover 를 갱신한다.

        - history: 이번 user 발화 + 이번 assistant answer 를 순서대로 추가
          (각 content 최대 1000자 트렁케이트), 최신 N쌍으로 캡.
        - prev_entities: 이번 final.service_cards 에서 service_id+label 조립(최대 10).
        - prev_intent: 이번 final.intent.
        - prev_reasoning: 이번 decision.user_rationale(없으면 None).
        """
        self.history.append({"role": "user", "content": _truncate(user_message, 1000)})
        answer = ""
        if final is not None:
            answer = str(final.data.get("answer") or "")
        self.history.append({"role": "assistant", "content": _truncate(answer, 1000)})
        # 최신 N쌍(=2N 발화)으로 캡.
        cap = self.max_history_turns * 2
        if len(self.history) > cap:
            del self.history[: len(self.history) - cap]

        self.prev_entities = _entities_from_final(final)
        self.prev_intent = (
            (final.data.get("intent") if final is not None else None) or None
        )
        self.prev_reasoning = (
            _reasoning_from_decision(decision) if decision is not None else None
        )


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


def _entities_from_final(final: SSEEvent | None) -> list[dict[str, str]]:
    """final.service_cards → [{service_id, label}] (최대 10, label=service_name)."""
    if final is None:
        return []
    cards = final.data.get("service_cards") or []
    out: list[dict[str, str]] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        sid = card.get("service_id")
        if not sid:
            continue
        label = card.get("service_name") or ""
        out.append({"service_id": str(sid), "label": str(label)[:200]})
        if len(out) >= 10:
            break
    return out


def _reasoning_from_decision(decision: SSEEvent | None) -> str | None:
    """decision 이벤트의 user_rationale 을 추출(최대 500자)."""
    if decision is None:
        return None
    rationale = decision.data.get("user_rationale")
    if not rationale:
        return None
    return _truncate(str(rationale), 500)


# ---------------------------------------------------------------------------
# 출력 헬퍼
# ---------------------------------------------------------------------------
def _print_turn_header(turn_no: int, message_id: int, user_message: str) -> None:
    print()
    print(_bold(_cyan(f"─── Turn {turn_no} · msg_id={message_id} ───")))
    print(f"{_bold('나')}: {user_message}")


def _print_carryover(carry: CarryoverState) -> None:
    print(_dim("┌─ 전송 carryover (맥락 유지 검증) ─────────────"))
    n_turns = len(carry.history) // 2
    print(_dim(f"│ history: {len(carry.history)} 발화 ({n_turns} 턴)"))
    if carry.prev_entities:
        print(_dim(f"│ prev_entities ({len(carry.prev_entities)}):"))
        for ent in carry.prev_entities:
            print(_dim(f"│   - {ent['service_id']}  {ent['label']}"))
    else:
        print(_dim("│ prev_entities: (없음)"))
    print(_dim(f"│ prev_intent: {carry.prev_intent}"))
    rationale = carry.prev_reasoning
    if rationale:
        print(_dim(f"│ prev_reasoning: {rationale}"))
    else:
        print(_dim("│ prev_reasoning: (없음)"))
    print(_dim("└──────────────────────────────────────────────"))


def _print_event(evt: SSEEvent) -> None:
    """SSE 이벤트를 도착 순서대로 핵심 필드만 읽기 좋게 출력한다."""
    d = evt.data
    if evt.event == "progress":
        print(_dim(f"  · progress  [{d.get('step')}] {d.get('message', '')}"))
    elif evt.event == "decision":
        routes = d.get("routes") or []
        print(
            _magenta(
                f"  · decision  action={d.get('action')} "
                f"routes={routes}"
            )
        )
        rationale = d.get("user_rationale")
        if rationale:
            print(_magenta(f"              근거: {rationale}"))
    elif evt.event == "title":
        print(_magenta(f"  · title  {d.get('title')!r}"))
    elif evt.event == "sources_update":
        srcs = d.get("sources") or []
        summary = ", ".join(
            f"{s.get('channel')}={s.get('hits')}" for s in srcs
        )
        print(_green(f"  · sources_update  {summary}"))
    elif evt.event == "final":
        print(_green("  · final  (수신)"))
    elif evt.event == "workflow_error":
        print(_red(f"  · workflow_error  {d.get('error')}"))
    elif evt.event == "error":
        print(_red(f"  · error  {d.get('message')}"))
    else:
        print(_dim(f"  · {evt.event}  {d}"))


def _print_final_ux(final: SSEEvent | None) -> None:
    """최종 사용자 경험(content) 블록."""
    if final is None:
        print(_red("  (final 이벤트 없음 — 오류 또는 중단)"))
        return
    d = final.data
    print()
    print(_bold(_green("─ 최종 답변 ─")))
    print(d.get("answer") or "(빈 답변)")

    cards = d.get("service_cards") or []
    if cards:
        print()
        print(_bold(f"─ 시설 카드 ({len(cards)}) ─"))
        for i, card in enumerate(cards, start=1):
            if not isinstance(card, dict):
                continue
            _print_card(i, card)

    intent = d.get("intent")
    cache_hit = d.get("cache_hit")
    # title 은 별도 title 이벤트로 도착하므로 final meta 에서 표기하지 않는다.
    meta = f"intent={intent}  cache_hit={cache_hit}"
    print(_dim(f"\n[{meta}]"))


def _print_card(i: int, card: dict[str, Any]) -> None:
    name = card.get("service_name") or "(이름 없음)"
    area = card.get("area_name") or "-"
    status = card.get("service_status") or "-"
    pay = card.get("payment_type") or "-"
    print(f"  {i}. {_bold(name)}  [{area} · {status} · {pay}]")
    # 새로 노출되는 정형 컬럼이 있으면 함께 표기.
    use_start = card.get("use_time_start")
    use_end = card.get("use_time_end")
    if use_start or use_end:
        print(_dim(f"     이용시간: {use_start or '?'} ~ {use_end or '?'}"))
    cstd_type = card.get("cancel_std_type")
    cstd_days = card.get("cancel_std_days")
    if cstd_type and cstd_days is not None:
        print(_dim(f"     취소기준: {cstd_type} 기준 {cstd_days}일 전"))
    tel = card.get("tel_no")
    if tel:
        print(_dim(f"     문의처: {tel}"))


# ---------------------------------------------------------------------------
# HTTP SSE 호출
# ---------------------------------------------------------------------------
async def _stream_turn(
    client: httpx.AsyncClient,
    base_url: str,
    payload: dict[str, Any],
) -> tuple[list[SSEEvent], SSEEvent | None, SSEEvent | None]:
    """한 턴을 POST /chat/stream 으로 보내고 이벤트를 실시간 출력하며 수집한다.

    Returns:
        (모든 이벤트, 마지막 decision 이벤트, final/error 이벤트)
    """
    parser = _SSEParser()
    events: list[SSEEvent] = []
    decision: SSEEvent | None = None
    final: SSEEvent | None = None

    url = base_url.rstrip("/") + "/chat/stream"
    async with client.stream("POST", url, json=payload) as resp:
        resp.raise_for_status()
        async for chunk in resp.aiter_text():
            for evt in parser.feed(chunk):
                _print_event(evt)
                events.append(evt)
                if evt.event == "decision":
                    decision = evt
                elif evt.event in ("final", "workflow_error", "error"):
                    final = evt
    return events, decision, final


async def _run_conversation(
    messages: list[dict[str, Any]],
    base_url: str,
    room_id: int,
    max_history_turns: int,
    message_iter: AsyncIterator[dict[str, Any]] | None = None,
) -> None:
    """주어진 발화 목록(또는 대화형 iterator)으로 멀티턴 대화를 진행한다.

    messages 각 항목: {"message": str, "lat": float|None, "lng": float|None}
    """
    carry = CarryoverState(max_history_turns=max_history_turns)
    turn_no = 0

    timeout = httpx.Timeout(connect=5.0, read=120.0, write=10.0, pool=5.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        source = _aiter_list(messages) if message_iter is None else message_iter
        async for spec in source:
            msg = spec["message"]
            if msg == "__RESET__":
                carry.reset()
                print(_yellow("\n[carryover 초기화됨]"))
                continue
            turn_no += 1
            message_id = turn_no
            _print_turn_header(turn_no, message_id, msg)
            _print_carryover(carry)

            payload: dict[str, Any] = {
                "room_id": room_id,
                "message_id": message_id,
                "message": msg,
                **carry.build_request_fields(),
            }
            if spec.get("lat") is not None:
                payload["lat"] = spec["lat"]
            if spec.get("lng") is not None:
                payload["lng"] = spec["lng"]

            print(_dim("┌─ SSE 이벤트 스트림 ─────────────"))
            try:
                _, decision, final = await _stream_turn(client, base_url, payload)
            except httpx.ConnectError:
                _print_server_down(base_url)
                return
            except httpx.HTTPStatusError as exc:
                print(_red(f"  HTTP {exc.response.status_code}: {exc.response.text[:300]}"))
                return
            print(_dim("└──────────────────────────────────"))

            _print_final_ux(final)
            carry.record_turn(msg, decision, final)


async def _aiter_list(items: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for it in items:
        yield it


def _print_server_down(base_url: str) -> None:
    print(_red(f"\n서버에 연결할 수 없습니다: {base_url}"))
    print(_yellow("AI 서비스가 떠 있는지 확인하세요:"))
    print("  uv run uvicorn main:app --reload")
    print(
        _dim(
            "DB/LLM 연결이 필요합니다 — ON_AI_DATABASE_URL / GOOGLE_API_KEY 등 "
            "환경변수(.env)가 설정되어 있어야 합니다."
        )
    )


# ---------------------------------------------------------------------------
# 대화형 REPL 입력 iterator
# ---------------------------------------------------------------------------
async def _interactive_messages(
    lat: float | None, lng: float | None
) -> AsyncIterator[dict[str, Any]]:
    print(_dim("대화형 모드. 빈 줄 또는 /quit 종료, /reset carryover 초기화."))
    loop = asyncio.get_event_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, lambda: input("\n나> "))
        except (EOFError, KeyboardInterrupt):
            print()
            return
        line = line.strip()
        if line == "" or line == "/quit":
            return
        if line == "/reset":
            yield {"message": "__RESET__", "lat": None, "lng": None}
            continue
        yield {"message": line, "lat": lat, "lng": lng}


# ---------------------------------------------------------------------------
# 내장 시나리오
# ---------------------------------------------------------------------------
_SCENARIOS: dict[str, dict[str, Any]] = {
    "room64_relevance": {
        "desc": "적합성 후속 (156/157 재현) — '왜 이게 자연속 활동이야?'",
        "messages": [
            "자연 속에서 할 수 있는 활동 찾아줘",
            "왜 이 항목들이 자연속에서 할 수 있는 활동이야?",
        ],
    },
    "room64_skew": {
        "desc": "지역 쏠림 + 보일러플레이트 (161 재현)",
        "messages": ["지금 접수 중인 체육시설 관련 서비스 알려줘"],
    },
    "attribute_gap": {
        "desc": "attribute_gap 데이터-성격 프레이밍 확인",
        "messages": [
            "지금 접수 중인 체육시설 관련 서비스 알려줘",
            "마루공원 테니스장 폭염철 이용안내에 대해 알려줘",
        ],
    },
    "followup_filter": {
        "desc": "워킹셋 후속 (P1 전엔 재검색될 것) — '그 중 무료인 것만'",
        "messages": ["강남구 체육시설 알려줘", "그 중 무료인 것만"],
    },
}


def _print_scenario_list() -> None:
    print("내장 시나리오:")
    for name, sc in _SCENARIOS.items():
        print(f"  {_bold(name)}")
        print(f"    {sc['desc']}")
        for i, m in enumerate(sc["messages"], start=1):
            print(_dim(f"    {i}. {m}"))


def _scenario_messages(
    name: str, lat: float | None, lng: float | None
) -> list[dict[str, Any]]:
    sc = _SCENARIOS[name]
    return [{"message": m, "lat": lat, "lng": lng} for m in sc["messages"]]


# ---------------------------------------------------------------------------
# SSE 파서 self-check (서버 없이 import 시 호출 가능; 정확성 근거)
# ---------------------------------------------------------------------------
def _selfcheck_sse_parser() -> None:
    """SSE 파서가 청크 경계·멀티라인 data·프레임 경계를 정확히 처리하는지 확인.

    `--selfcheck` 또는 단위테스트에서 호출. 실패 시 AssertionError.
    """
    parser = _SSEParser()
    # 프레임을 비정상적인 위치(라인 중간/data 사이)에서 쪼개 청크 경계 무관성 검증.
    raw = (
        "id: abc\nevent: progress\ndata: {\"step\": \"routing\"}\n\n"
        "event: decision\ndata: {\"action\": \"RETRIEVE\",\n"
        "data: \"routes\": [\"SQL_SEARCH\"]}\n\n"
    )
    chunks = [raw[i : i + 7] for i in range(0, len(raw), 7)]
    events: list[SSEEvent] = []
    for ch in chunks:
        events.extend(parser.feed(ch))
    assert len(events) == 2, f"기대 2 이벤트, 실제 {len(events)}"
    assert events[0].event == "progress"
    assert events[0].data == {"step": "routing"}
    # 멀티라인 data 는 개행으로 결합되어 단일 JSON 으로 파싱되어야 한다.
    assert events[1].event == "decision"
    assert events[1].data == {"action": "RETRIEVE", "routes": ["SQL_SEARCH"]}
    print("SSE 파서 self-check 통과")


# ---------------------------------------------------------------------------
# argparse / main
# ---------------------------------------------------------------------------
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="맥락유지형 멀티턴 대화 관찰 스크립트 (SSE 과정 + UX content)"
    )
    parser.add_argument(
        "--scenario", type=str, default=None, help="내장 시나리오 이름 실행"
    )
    parser.add_argument(
        "--list", action="store_true", help="내장 시나리오 목록 출력 후 종료"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="REPL 대화형 모드 (인자 없이 실행해도 동일)",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://localhost:8000",
        dest="base_url",
        help="AI 서비스 주소 (기본: http://localhost:8000)",
    )
    parser.add_argument(
        "--room-id", type=int, default=64, dest="room_id", help="대화방 ID (기본: 64)"
    )
    parser.add_argument(
        "--max-history-turns",
        type=int,
        default=6,
        dest="max_history_turns",
        help="history 에 실어 보낼 최신 턴(USER+ASSISTANT 쌍) 수 캡 (기본: 6)",
    )
    parser.add_argument("--lat", type=float, default=None, help="사용자 위도 (MAP 테스트)")
    parser.add_argument("--lng", type=float, default=None, help="사용자 경도 (MAP 테스트)")
    parser.add_argument(
        "--selfcheck",
        action="store_true",
        help="SSE 파서 self-check 만 실행하고 종료 (서버 불필요)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    if args.selfcheck:
        _selfcheck_sse_parser()
        return

    if args.list:
        _print_scenario_list()
        return

    if args.scenario is not None:
        if args.scenario not in _SCENARIOS:
            sys.exit(
                f"알 수 없는 시나리오: {args.scenario}\n"
                "--list 로 목록을 확인하세요."
            )
        messages = _scenario_messages(args.scenario, args.lat, args.lng)
        asyncio.run(
            _run_conversation(
                messages, args.base_url, args.room_id, args.max_history_turns
            )
        )
        return

    # 기본/인자 없음 → 대화형 REPL.
    asyncio.run(
        _run_conversation(
            [],
            args.base_url,
            args.room_id,
            args.max_history_turns,
            message_iter=_interactive_messages(args.lat, args.lng),
        )
    )


if __name__ == "__main__":
    main()
