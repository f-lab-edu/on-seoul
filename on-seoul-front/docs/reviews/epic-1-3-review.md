# Epic 1~3 종합 코드 리뷰

날짜: 2026-05-07
리뷰어: front-code-reviewer
범위: Epic 1 (Foundation) + Epic 2 (Auth) + Epic 3 (SSE Chat)

## 종합 결정: APPROVE (조건부)

MUST-FIX 0건, SHOULD-FIX 4건. 머지 가능. SHOULD-FIX는 후속 PR(Epic 4 진입 전 또는 Epic 5 polish)에서 보강 권장.

## 요약

Epic 1~3은 CLAUDE.md의 절대 규칙(A. 인증/B. SSE/C. 컴포넌트/D. 타입/E. 일반)을 충실히 준수한다. 핵심 강점은 (1) `lib/api-client.ts`의 single-flight refresh와 절대 URL 차단, (2) `hooks/useChatStream.ts`의 discriminated union switch + `assertNever`, (3) Route Handler의 cookie forward + `X-Accel-Buffering: no` 처리, (4) SSE 잔여 청크 처리 및 reader release까지 다룬 파서, (5) 단위 테스트가 single-flight·무한루프 차단·절대 URL 거부 등 보안 임계치를 직접 검증한다는 점이다.

약점은 주로 UX/일관성 영역이다. (a) `useAuth`의 5xx error 메시지가 raw `Error.message`(영문)로 노출될 가능성, (b) `(chat)/layout.tsx`의 서버측 `fetch`가 `api-client`를 우회하지만 이는 서버 컴포넌트 SSR 특성상 불가피한 패턴이며 cookie forward 자체는 안전, (c) `useChatStream`의 `e.name === "AbortError"` 이중 체크가 죽은 코드, (d) `(chat)/page.tsx`의 `useEffect` 의존성에 `state` 객체를 통째로 넣어 매 리렌더마다 effect가 재실행될 수 있음(현재는 `lastCommittedDoneId` 가드로 멱등 보장).

머지 가능 여부: **머지 가능**. 보안 절대규칙은 모두 통과하며 핵심 경계면(api-client 401 인터셉터, SSE 프록시, 인증 가드)이 견고하다. SHOULD-FIX는 Epic 4(채팅 영속화)에서 자연스럽게 정리될 수 있는 항목이다.

## CLAUDE.md 절대 규칙 준수 매트릭스

| 규칙 | 준수 | 비고 |
|---|---|---|
| A.1 토큰 직접 보관 금지 (localStorage/sessionStorage/document.cookie 금지) | OK | 전 코드베이스에서 해당 API 미사용. 토큰은 httpOnly 쿠키만. |
| A.2 모든 API는 api-client + credentials:include | OK | 클라이언트 측은 `api-client.ts` 일원화. `useChatStream`만 `/api/query` 직접 fetch하나, 동일 origin Route Handler 호출이며 `credentials:'include'` 명시. `(chat)/layout.tsx`의 서버측 fetch는 SSR 특성상 cookies()로 명시 forward — 의도된 우회. |
| A.3 Route Handler가 쿠키 forward, 토큰을 본문/URL/응답 헤더로 노출 금지 | OK | `app/api/query/route.ts`가 `cookie` 헤더만 forward. 응답 헤더에 Set-Cookie 등 토큰 노출 없음. |
| A.4 401 → /auth/refresh 1회 후 실패 시 logout, 무한루프 금지 | OK | `_retry` 가드 + `path === "/auth/refresh"` 가드 + single-flight. 테스트로 검증됨. |
| B.1 SSE 타입은 `types/sse-events.ts` 단일 소스 | OK | 백엔드 `events.py` 미러 주석 명시. CLAUDE.md 예시(`agent`)와 실제 파일(`AgentName` 별칭)이 일치. |
| B.2 SSE 처리는 `useChatStream` 안에서만 | OK | 컴포넌트는 `state` 소비만. `EventSource`/`fetch+stream` 직접 사용 없음. |
| B.3 discriminated union switch + `assertNever(default)` | OK | `useChatStream.ts:105-136`. |
| B.4 스트림 단절 시 메시지 확정 + 재시도 버튼 | OK | "스트리밍이 완료되지 않은 상태에서…" 에러 + `(chat)/page.tsx`의 retry 버튼. |
| C.1 shadcn/ui 우선 | OK | `components/ui/`에 button/card/dialog/input/scroll-area/avatar 생성. |
| C.2 shadcn 생성물 보존 | OK (스캔 한정) | `button.tsx` 헤더가 CLI 생성 형태 유지. prop 시그니처 변경 흔적 없음. |
| C.3 `use client`는 필요한 컴포넌트에만 | OK | `(auth)/login`, `(chat)/layout`은 서버 컴포넌트. `useChatStream`/`useAuth` 사용처만 클라이언트. `agent-trace`/`message-bubble`은 `'use client'` 없는 순수 표현. |
| D.1 `any` 금지 | OK | ESLint `@typescript-eslint/no-explicit-any: error`. 코드 내 `any` 미발견. SSE 페이로드는 `unknown` → `as SseEvent` 캐스팅. |
| D.2 백엔드 공유 타입은 `types/`에 + 출처 주석 | OK | `sse-events.ts`(events.py), `auth.ts`(users 테이블), `chat.ts`(query_history) 모두 주석 명시. |
| D.3 DTO는 경계에서만 | OK | 컴포넌트는 `User`, `MessageRole`, `ChatStreamState` 등 도메인 타입만 소비. |
| E.1 console.log 잔존 금지 | OK | 미발견. ESLint warn 설정. |
| E.2 주석은 "왜"만 | OK | 대부분 의도/규칙 근거 설명(예: "절대 URL을 허용하면 외부 오리진에…"). |
| E.3 하드코딩 URL 금지 | OK | `NEXT_PUBLIC_API_BASE_URL` / `API_BASE_URL` / `NEXT_PUBLIC_OAUTH_LOGIN_URL` 모두 환경변수 사용. |
| E.4 새 의존성 사유 PR 명기 | N/A | 본 리뷰 범위 밖(PR 설명 미확인). |

## Findings (심각도별 정렬)

### MUST-FIX (머지 차단)

없음.

### SHOULD-FIX (머지 가능, 후속 보강)

- **`hooks/useAuth.ts:46` — 5xx/네트워크 에러 메시지에 `Error.message` raw 노출**
  - `setError(message)` 시 `message`가 `"Failed to fetch"` 같은 영어 원문이 그대로 사용자에게 도달할 수 있음. `(chat)/page.tsx:75`에서는 한글 고정 문구로 덮어쓰므로 현재는 가시화되지 않으나, 향후 `error` 상태를 다른 곳에서 직접 표시하면 UX 비일관 발생.
  - 권장: `useAuth` 안에서 한글 사용자 문구로 매핑하거나, `error`를 `boolean`/`enum`으로 노출하고 메시지는 호출 측이 결정.

- **`app/(chat)/layout.tsx:10` — 서버측 fetch가 `api-client` 경유하지 않음 (의도됨이나 패턴 문서화 필요)**
  - CLAUDE.md A.2는 클라이언트 fetch 금지를 노린 규칙이고, 서버 컴포넌트는 `cookies()`로 명시 forward해야 하므로 본 우회는 정당하다. 그러나 동일 패턴이 향후 `chat/rooms` 등 SSR 데이터 페칭에 반복될 가능성이 있다.
  - 권장: `lib/server-api.ts`(가칭)로 서버측 fetch 헬퍼를 추출해 (a) baseUrl 결정, (b) cookie forward, (c) 401 → redirect 일원화. Epic 4 진입 시 자연스러운 리팩터링 포인트.

- **`hooks/useChatStream.ts:147-148` — AbortError 이중 체크 (죽은 코드)**
  - `if (e instanceof DOMException && e.name === "AbortError") return;` 다음 줄 `if ((e as Error)?.name === "AbortError") return;`은 첫 분기에 잡히지 않은 비-DOMException AbortError 대비로 보이나, 표준 환경에서는 fetch가 던지는 AbortError는 항상 `DOMException`이라 둘 중 하나면 충분. `(e as Error)` 캐스팅은 `e`가 `unknown`이라면 `D.1` 정신과 약간 어긋난다(`any`는 아니나 unsafe narrow).
  - 권장: `if (e instanceof Error && e.name === "AbortError") return;` 한 줄로 통합.

- **`app/(chat)/page.tsx:27-39` — `useEffect` 의존성에 `state` 전체**
  - 매 토큰 수신마다 `state`가 변하며 effect가 재실행된다. 현재는 `lastCommittedDoneId` 가드로 멱등성이 보장돼 버그 없음. 그러나 effect 내부 분기(`state.phase !== "done"` early return)가 매 토큰마다 실행되는 것은 비효율이며, 향후 effect 본문이 늘어나면 잠재 버그 통로.
  - 권장: 의존성을 `state.phase, state.messageId, state.content`로 좁히거나, `useChatStream`이 `onDone(callback)` 콜백을 노출.

### NIT (선택적)

- **`lib/sse.ts:50` — `reader.releaseLock()` try/catch는 좋으나, 호출 측이 `for await`을 break하면 Generator의 `finally`가 즉시 실행되지 않을 수 있다.** 현재 호출 측(`useChatStream`)은 명시 break 없이 `return`/소진하므로 문제 없음. 다만 generator를 다른 곳에서 재사용할 때를 대비해 `cancel()` 시 `reader.cancel()`도 시도하면 더 견고.

- **`app/api/query/route.ts:31-33` — 401 응답 본문을 `"Unauthorized"` 텍스트로 보냄.** 클라이언트는 status 코드만 보고 분기하므로 기능적으로는 문제 없으나, JSON `{ "error": "Unauthorized" }` 형식이 `api-client`의 `parseBody`와 일관됨.

- **`app/(auth)/login/page.tsx:53-60` — `<a>` 태그에 `target="_blank"`가 없어 `rel="noopener noreferrer"`는 사실상 무의미.** 동일 탭 OAuth 진입이면 rel 제거해도 됨(반대로 `target="_self"`로 명시).

- **`components/chat/message-list.tsx:31` — `scrollIntoView`를 `streamState` 객체 의존성으로 트리거.** 매 토큰마다 smooth scroll이 호출되어 모바일에서 버벅임 가능. `streamState.content.length`로 좁히고 `behavior: "auto"`로 변경 권장.

- **`components/chat/message-bubble.tsx` — XSS 검토.** `whitespace-pre-wrap`만 사용하고 React가 텍스트로 렌더하므로 XSS 위험 없음. post-MVP에 `react-markdown` 도입 시 `disallowedElements`/`urlTransform`/`rehype-sanitize` 필수.

- **`app/(auth)/oauth/callback/page.tsx` — `useEffect` 안에서 `router.replace` 사용 시 사용자가 브라우저 뒤로가기로 콜백 URL에 다시 진입할 수 없어 안전하나, 알 수 없는 케이스(`status`/`error` 모두 없음)에서 조용히 `/login`으로 가는 동안 사용자에게 사유가 전달되지 않는다.** `?error=unknown` 등으로 명시 가능.

- **`hooks/useChatStream.ts:115` — `event.delta`가 `undefined`거나 비문자열이면 `content += undefined`로 `"undefined"` 문자열이 누적될 수 있다.** 백엔드 신뢰 가정이지만, `unknown` → 좁힘이 단순 캐스팅이므로 zod 등 도입 전에는 한 줄 가드(`if (typeof event.delta === "string")`) 권장.

- **`vitest.config.ts:13` — `globals: false`인데 테스트는 import로 명시 사용 중이라 일관됨.** 다만 `setupFiles`가 없어 `process.env` 초기화가 각 테스트의 `beforeEach`에 산재. `setupFiles`로 통합 권장.

## Cross-Epic 일관성 분석

- **에러 처리 패턴**: `ApiError`(api-client), `phase: "error"`(useChatStream), `error: string | null`(useAuth) 세 가지 패턴이 공존한다. Epic 별 책임 분리 측면에서는 합리적이나, 사용자 노출 메시지 한글화는 `useChatStream`만 일부 처리하고 `useAuth`는 raw 영문이 셀 수 있다(SHOULD-FIX 1번). `(chat)/page.tsx`에서 한글 고정문으로 덮어쓰는 방식이 일관성 보강책.

- **타입 사용 패턴**: 백엔드 응답은 모두 `unknown` → 명시 캐스팅(`as User`, `as SseEvent`) 패턴으로 통일. zod 미도입 상태에서 일관됨. D.1 위배 없음. 다만 SSE는 백엔드 신뢰 경계라 zod로 좁히는 것이 더 안전(SHOULD-FIX는 아니고 Epic 5 polish 후보).

- **환경변수 처리 패턴**: 클라이언트(`api-client.ts`, login page)는 `NEXT_PUBLIC_*`만 참조. 서버(`(chat)/layout.tsx`, `api/query/route.ts`)는 `API_BASE_URL ?? NEXT_PUBLIC_API_BASE_URL` fallback 패턴으로 일관. 미설정 시 throw/500 응답으로 가시화하는 정책도 동일. **일관성 우수**.

- **테스트 커버리지**: api-client는 단위 테스트 7건(정상/401/동시401/refresh-401/무한루프/외부URL/204), useChatStream은 4건(token누적/error/cancel/HTTP500). Route Handler(`api/query/route.ts`)와 SSE 파서(`lib/sse.ts`)는 직접 테스트 없음 — useChatStream 통합 테스트가 SSE 파서를 간접 커버하나, 잔여 청크/keepalive 코멘트/멀티라인 data 처리 단위 테스트 없음(SHOULD-FIX 후보).

## 후속 Epic 권장 사항

**Epic 4 진입 전 정리**:
- SHOULD-FIX 1(useAuth 한글 메시지) 또는 호출 측 일원화 정책 결정.
- SHOULD-FIX 2(server-api 헬퍼)를 Epic 4 채팅 이력 SSR 페치와 함께 추출.
- SHOULD-FIX 3(AbortError 이중 체크) — 작은 변경이라 같은 PR로 정리.

**Epic 5 polish에서 다룰 항목**:
- `lib/sse.ts` 단위 테스트(잔여 청크, 멀티라인 data, keepalive 코멘트).
- SSE 페이로드 zod 좁힘.
- `react-markdown` 도입 시 sanitize 정책.
- `message-list.tsx` 스크롤 성능 튜닝.
- shadcn 컴포넌트 wrapper 패턴 가이드(`components/ui/` 보존 규칙 강화).

## 총평

Epic 1~3은 보안/타입/SSE/인증의 모든 절대 규칙을 통과하며, 단위 테스트가 핵심 보안 경계(401 single-flight, 절대 URL 차단, 무한루프 방지)를 직접 검증한다는 점에서 매우 견고하다. SHOULD-FIX는 사용자 경험 일관성과 향후 확장성을 위한 것으로, 머지 차단 사유는 아니다.

**APPROVE**. Epic 4 진입을 권장한다.
