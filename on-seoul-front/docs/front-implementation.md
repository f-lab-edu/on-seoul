# 프론트엔드 구현 목록

Next.js 15 App Router 기반 웹 프론트엔드 구현 순서.
각 Phase는 독립적으로 동작 확인 후 다음 단계로 진행한다.

> **MVP 방침**: 챗봇(SSE 스트리밍) + Google OAuth 인증 두 축에 집중한다.
> 인증은 백엔드 발급 **httpOnly 쿠키** 기반(Access/Refresh, SameSite=Strict)이며 프론트는 토큰을 보관하지 않는다.
> 지도·알림·반응형 PC 대응은 POST-MVP.

브랜치는 아래 Epic 단위로 생성한다. Phase는 각 Epic 안의 작업 체크리스트다.

상위 규약은 [`CLAUDE.md`](../CLAUDE.md), 백엔드 인증 시퀀스는 [`docs/schemas/auth-design.md`](../../docs/schemas/auth-design.md) 참조.

---

## Epic 1 — `feat/foundation`
> Phase 1–3 | 앱 기동, shadcn 셋업, 타입 단일 소스 정의

### Phase 1. 프로젝트 초기 구성

- [x] Next.js 15 App Router 스캐폴딩 (`pnpm create next-app`, TypeScript strict, Tailwind, ESLint)
- [x] 디렉토리 구조 생성 (`app/(auth)/`, `app/(chat)/`, `app/api/`, `components/`, `hooks/`, `lib/`, `types/`)
- [x] `tsconfig.json` — `strict: true`, `@/*` alias 설정
- [x] `.env.example` — `NEXT_PUBLIC_API_BASE_URL`, `API_BASE_URL`, `NEXT_PUBLIC_OAUTH_LOGIN_URL` 정의
- [x] ESLint 규칙 — `console.log` warn, `any` error
- [x] `package.json` scripts — `dev`, `build`, `lint`, `typecheck`

검증: `pnpm dev` 기동 후 `http://localhost:3000` 기본 페이지 렌더, `pnpm typecheck && pnpm lint` 통과 ✅

### Phase 2. shadcn/ui 셋업

- [x] `pnpm dlx shadcn@latest init` — `components.json`, `app/globals.css` 생성
- [x] Tailwind 색상 토큰 / 다크모드 변수 연결
- [x] 기본 컴포넌트 추가 — `button`, `input`, `card`, `dialog`, `scroll-area`, `avatar`
- [x] `components/ui/` 보존 규칙 문서화 (CLAUDE.md C-2 참조)

검증: `pnpm typecheck && pnpm lint` 통과 ✅

### Phase 3. 타입 단일 소스 정의

- [x] `types/sse-events.ts` — 백엔드 `ai-service/schemas/events.py` 미러 (출처 주석 필수). `agent_start` / `tool_call` / `tool_result` / `token` / `done` / `error` discriminated union
- [x] `types/auth.ts` — `User`, OAuth 콜백 결과(`status=success | error=forbidden`)
- [x] `types/chat.ts` — `ChatRoom`, `ChatMessage` (`USER` / `ASSISTANT` 역할)
- [x] `assertNever` 유틸 추가 (`lib/assert-never.ts`)

검증: `pnpm typecheck` 통과 ✅

---

## Epic 2 — `feat/auth`
> Phase 4–7 | Google OAuth 2.0 + httpOnly 쿠키 인증, 401 single-flight refresh

> **Vitest 도입 시점**: Phase 4 시작 시 설치. 이후 `lib/api-client.ts`와 `hooks/useChatStream.ts` 두 파일만 테스트 대상으로 고정하고 컴포넌트·페이지는 제외한다.

> 백엔드 시퀀스: `oauth-jwt-flow` 스킬 정본. 콜백 성공 시 `302 → /oauth/callback?status=success` + Set-Cookie(access_token / refresh_token, SameSite=Strict).

### Phase 4. API 클라이언트 (`lib/api-client.ts`)

- [x] `fetch` 래퍼 — `credentials: 'include'` 기본 적용
- [x] 401 인터셉터 — `/auth/refresh` **single-flight** 1회 재시도 (동시 401 5건 → refresh 1번)
- [x] Refresh 자체가 401 → `auth:logout` window event 발행 → 즉시 실패
- [x] `/auth/refresh` 호출 시 `_retry` 플래그로 무한루프 차단
- [x] `get` / `post` / `delete` 헬퍼, 204 응답은 `undefined` 반환
- [x] Vitest 설치 (`pnpm add -D vitest @vitest/coverage-v8`) — 이 Phase에 최초 도입
- [x] 단위 테스트 6건 — fetch 모킹으로 single-flight / 무한루프 방지 / refresh 401 시 logout 이벤트 검증

### Phase 5. 로그인·콜백 페이지

- [x] `app/(auth)/login/page.tsx` — `NEXT_PUBLIC_OAUTH_LOGIN_URL` 진입 링크 (서버 컴포넌트)
- [x] `app/(auth)/oauth/callback/page.tsx` — `'use client'`. `status === 'success'` → `/`, `error=forbidden` → `/login?error=forbidden`. 쿠키 처리는 백엔드 책임이므로 본 페이지는 라우팅만
- [x] `?error=forbidden` 메시지 노출 영역 (`/login`)
- [x] 로그인 페이지에서 `localStorage` / `document.cookie` 직접 접근 없음 (QA 확인)

검증: `/login` → 백엔드 OAuth → Google 동의 → `/oauth/callback?status=success` → `/` 도착 시 토큰이 URL/스토리지에 노출되지 않음 확인 (백엔드 연동 시 수동 검증 필요)

### Phase 6. `useAuth` 훅

- [x] `apiClient.get<User>('/auth/me')`로 현재 사용자 조회 → `user` / `loading` 상태 노출
- [x] `auth:logout` window event 구독 → `setUser(null)` + `/login` 라우팅
- [x] `logout()` — `POST /auth/logout` 호출 후 라우팅, 실패해도 로컬 상태 정리
- [x] 채팅 layout / 보호 페이지에서 사용 (`(chat)/page.tsx` placeholder에서 사용 중)

### Phase 7. 인증 가드

- [x] `app/(chat)/layout.tsx` — 서버에서 `/auth/me` 호출 (cookies() forward, 화이트리스트 적용) → 401/403 시 `/login` redirect
- [ ] CORS / SameSite=Strict 동작 확인 — same-site 또는 reverse proxy 구성 검증 (백엔드 연동 시 수동 검증 필요)
- [ ] 수동 시나리오 테스트 (백엔드 연동 후)
  - 비로그인 보호 페이지 접근 → `/login` 라우팅
  - Access 만료 → API 401 → refresh → 재시도 성공 (UX 끊김 없음)
  - Refresh 만료 → refresh 401 → 자동 로그아웃
  - 동시 5개 요청 401 → refresh 정확히 1회 (네트워크 탭)

---

## Epic 3 — `feat/chat-stream`
> Phase 8–10 | SSE 프록시, `useChatStream` 단일 진입점, 챗봇 UI

> 백엔드 SSE 엔드포인트: `POST {API}/api/query`, 인증: httpOnly 쿠키 `access_token`, 요청 본문: `{question, roomId?}`.
> 구현 패턴은 `sse-chat-stream` 스킬 정본.

### Phase 8. SSE Route Handler 프록시 (`app/api/query/route.ts`)

- [ ] `POST` 핸들러 — 요청 본문 검증(zod) → 백엔드 `${API_BASE_URL}/api/query`로 forward
- [ ] 요청 쿠키(`access_token`)를 그대로 전달, 토큰을 본문/URL/응답 헤더로 노출 금지
- [ ] 응답을 `text/event-stream`으로 그대로 스트리밍 (ReadableStream pipethrough)
- [ ] AbortController 연결 — 클라이언트 disconnect 시 백엔드 요청도 취소
- [ ] 백엔드 5xx / 타임아웃 시 `event: error` 1건 emit 후 종료

### Phase 9. `hooks/useChatStream.ts`

- [ ] `fetch + ReadableStream` 기반 SSE 파서 (`lib/sse.ts` 분리). `EventSource` 사용 금지 (POST + 쿠키 forward 필요)
- [ ] 이벤트 디스패치 — discriminated union switch, `default: assertNever(event)`
  - `agent_start` / `tool_call` / `tool_result` → 진행 상태 누적 (`agent-trace`)
  - `token` → 본문 누적
  - `done` → 메시지 확정 + `messageId` 보존
  - `error` → 에러 메시지 + 재시도 버튼 노출
- [ ] AbortController로 도중 취소 지원
- [ ] TanStack Query 사용 금지 (SSE는 별도 단일 진입점)
- [ ] 단위 테스트 — Mock ReadableStream으로 토큰 누적 / 에러 분기 / 취소 동작 검증 (Vitest)

### Phase 10. 챗봇 UI

- [ ] `components/chat/chat-input.tsx` — 메시지 입력 + 전송 (Enter / Shift+Enter)
- [ ] `components/chat/message-list.tsx` — 가상 스크롤(필요 시), 자동 하단 고정
- [ ] `components/chat/message-bubble.tsx` — USER / ASSISTANT 렌더, 마크다운(필요 시)
- [ ] `components/chat/agent-trace.tsx` — `agent_start` / `tool_call` / `tool_result` 진행 표시
- [ ] 스트림 도중 단절 → 메시지 확정 + 재시도 버튼 (조용한 실패 금지)

검증: `/` 진입 → 질의 입력 → SSE 토큰 실시간 누적 → `done` 이후 카드/링크 정상 렌더

---

## Epic 4 — `feat/chat-history`
> Phase 11–12 | 대화 목록·이력 캐싱, [roomId] 라우팅

### Phase 11. 대화 이력 (TanStack Query)

- [ ] `GET /chat/rooms` 쿼리 — 사이드바 목록
- [ ] `GET /chat/rooms/{id}/messages` 쿼리 — 기존 대화 이어가기
- [ ] 새 대화 mutation — 첫 응답 `done` 이후 백엔드가 채워준 제목으로 invalidate
- [ ] 캐시 키 / staleTime 정책 결정 (목록 30s, 이력 영구)

### Phase 12. 라우팅·레이아웃

- [ ] `app/(chat)/layout.tsx` — 사이드바(대화 목록) + 본문 이중 컬럼
- [ ] `app/(chat)/page.tsx` — 새 대화 시작
- [ ] `app/(chat)/[roomId]/page.tsx` — 기존 대화 로드 후 `useChatStream` 연결
- [ ] 첫 응답 전까지 제목 "새 대화"로 표기, `done` 이후 백엔드 제목으로 교체
- [ ] 로그아웃 시 TanStack Query 캐시 전체 invalidate

검증: 새 대화 → 응답 완료 → 사이드바 제목 자동 갱신 → 새로고침 후 동일 이력 복원

---

## Epic 5 — `feat/front-polish`
> Phase 13–15 | 테스트, 접근성, 배포

### Phase 13. 테스트

> **범위 고정**: Vitest는 코어 2개 파일(`api-client`, `useChatStream`)만 관리한다. 컴포넌트·페이지 스냅샷/인터랙션 테스트는 도입하지 않는다.

- [ ] `lib/api-client.ts` 테스트 보완 (Phase 4에서 작성한 케이스 최종 점검)
- [ ] `useChatStream` 테스트 보완 (Phase 9에서 작성한 케이스 최종 점검)
- [ ] `pnpm test` CI 연결 확인
- [ ] Playwright E2E (선택) — 로그인 → 채팅 → 로그아웃 시나리오, 백엔드는 모킹

### Phase 14. 접근성·UX 마감

- [ ] 키보드 내비게이션 — 입력창 / 버튼 / 사이드바 항목
- [ ] `prefers-reduced-motion` 대응
- [ ] 에러 / 로딩 상태 일관성 (`Skeleton`, `Toast`)
- [ ] 모바일 우선 레이아웃 점검 (PC 반응형은 POST-MVP)

### Phase 15. 배포 (Vercel)

- [ ] Vercel 프로젝트 연동 — `NEXT_PUBLIC_API_BASE_URL` / `API_BASE_URL` / `NEXT_PUBLIC_OAUTH_LOGIN_URL` 환경변수 등록
- [ ] 백엔드와 same-site 또는 reverse proxy 구성 합의(SameSite=Strict 쿠키 전송 보장)
- [ ] Preview / Production 분리, PR Preview에서 OAuth 콜백 동작 확인
- [ ] `pnpm build` 사이즈·LCP 점검

검증: Production URL에서 로그인 → 채팅 → 로그아웃 플로우 완전 동작, 토큰이 URL / 스토리지 / 응답 본문에 노출되지 않음

---

## 주요 설계 준수 사항

1. **인증 단일 진실**: 백엔드 발급 httpOnly 쿠키(`access_token`, `refresh_token`)가 단일 진실. 프론트는 토큰을 메모리/스토리지/`document.cookie`에 보관하지 않는다.
2. **API 호출 경계**: 모든 API 호출은 `lib/api-client.ts` 경유 + `credentials: 'include'`. Route Handler 외부에서 직접 `fetch()` 금지.
3. **SSE 단일 진입점**: SSE 처리는 `hooks/useChatStream.ts` 안에서만. 컴포넌트는 직접 `EventSource` / `fetch+ReadableStream`을 다루지 않는다.
4. **Cross-boundary contracts co-evolved**: `types/sse-events.ts`는 백엔드 `ai-service/schemas/events.py`의 미러. 어긋나면 frontend-qa가 차단. `types/`에 출처(파일·라인) 주석 필수.
5. **shadcn 보존**: `components/ui/`는 shadcn CLI 생성 형태를 보존. 변형이 필요하면 wrapper 컴포넌트로 분리.

---

## 참고

- 인증·SSE·shadcn 구현 패턴은 각각 `oauth-jwt-flow` / `sse-chat-stream` / `shadcn-add` 스킬을 정본으로 사용
- 패키지 매니저는 `pnpm` 고정 (npm/yarn 사용 금지)
- 배포는 Vercel. 백엔드와 same-site 구성이 아닐 경우 `SameSite=None; Secure` + CORS Allow-Credentials 협의 필요
- 지도(POST-MVP)는 카카오맵 JavaScript SDK 도입 시 별도 Phase로 추가
