# on-seoul-front

서울시 공공서비스 예약 AI Agent 서비스의 웹 프론트엔드. 챗봇 기반 시설 검색·예약 안내와 OAuth 인증을 제공한다.

---

## 범위 (MVP)

이 단계의 프론트엔드는 다음 두 가지에 집중한다.

- **챗봇**: 자연어 질의 → API 서비스 경유 → AI 서비스 SSE 스트리밍 응답. 에이전트 실행 중간 과정(라우팅, 도구 호출, 토큰)을 실시간으로 노출한다.
- **인증**: Google OAuth 2.0 로그인, JWT(Access + Refresh) **httpOnly 쿠키** 기반 세션, 자동 토큰 재발급.

지도 기반 탐색·알림·반응형 PC 대응은 POST-MVP.

---

## 기술 스택

| 영역 | 선택 |
|---|---|
| 프레임워크 | Next.js 15 (App Router) |
| 언어 | TypeScript |
| 스타일 | Tailwind CSS |
| 컴포넌트 | shadcn/ui |
| 서버 상태 | TanStack Query |
| SSE 처리 | 커스텀 훅 (`useChatStream`) |
| 클라이언트 상태 | useState / useReducer / Context |
| 테스트 | Vitest (코어 2개만: `api-client`, `useChatStream`) |
| 지도 (POST-MVP) | 카카오맵 JavaScript SDK |
| 배포 | Vercel |

선택 근거와 기각 대안은 상위 문서(프론트엔드 스택 결정 노트) 참조.

---

## 디렉토리 구조 (예정)

```
on-seoul-front/
├── app/
│   ├── (auth)/
│   │   ├── login/page.tsx                  # Google OAuth 진입
│   │   └── oauth/callback/page.tsx         # OAuth 콜백 처리 (쿠키는 백엔드가 발급)
│   ├── (chat)/
│   │   ├── layout.tsx              # 사이드바(대화 목록) + 본문
│   │   ├── page.tsx                # 새 대화
│   │   └── [roomId]/page.tsx       # 기존 대화 이어가기
│   └── api/
│       └── query/route.ts          # SSE 프록시 (쿠키 forward + 백엔드 중계)
├── components/
│   ├── ui/                         # shadcn/ui 생성물
│   ├── chat/
│   │   ├── chat-input.tsx
│   │   ├── message-list.tsx
│   │   ├── message-bubble.tsx
│   │   └── agent-trace.tsx         # 에이전트 중간 과정(tool_call 등) 표시
│   └── auth/
│       └── google-login-button.tsx
├── hooks/
│   ├── useChatStream.ts            # SSE 스트림 구독 + 이벤트 디스패치
│   └── useAuth.ts                  # 토큰 보관·재발급
├── lib/
│   ├── api-client.ts               # fetch 래퍼 (credentials: include, 401 → refresh)
│   └── sse.ts                      # SSE 파서 유틸
├── types/
│   ├── chat.ts                     # ChatRoom, ChatMessage
│   ├── sse-events.ts               # agent_start, tool_call, token, done 등
│   └── auth.ts                     # User, TokenPair
└── CLAUDE.md                       # 바이브코딩 규약 (SSE 이벤트, API, UI 규칙)
```

---

## 핵심 흐름

### 1. 인증

```
[Login] ── Google OAuth 2.0 ──▶ [API 서비스 (Spring Security)]
                                     │  Authorization Code → Google → 사용자 정보
                                     ▼
                          JWT 발급 → Set-Cookie (httpOnly, Strict)
                          access_token (Path=/)  + refresh_token (Path=/auth, 7d, Redis)
                                     │
                                     ▼
                          302 → /oauth/callback?status=success
                          프론트는 토큰을 보관하지 않음. 쿠키가 단일 진실.
                          401 응답 시 /auth/refresh 1회 재시도 → 실패 시 로그아웃
```

- 로그인 진입점: `/login` → 백엔드 OAuth URL(`/oauth2/authorization/google`)로 리다이렉트.
- 콜백: `/oauth/callback`에서 `status` / `error` 쿼리만 확인 후 라우팅. 토큰 처리는 불필요.
- 모든 API 호출은 `lib/api-client.ts`가 `credentials: 'include'`로 쿠키를 자동 전송하고 401을 가로채 Refresh를 시도한다.

### 2. 챗봇 (SSE 스트리밍)

```
[브라우저] ── POST /api/query ──▶ [Next Route Handler]
                                              │ 쿠키(access_token) forward
                                              ▼
                                         [API 서비스 /query]
                                              │
                                              ▼
                                         [AI 서비스 /chat/stream]
                                              │
                       ◀──────────── SSE (agent_start / tool_call / token / done)
```

- `app/api/query/route.ts`가 SSE를 그대로 프록시한다. 요청 쿠키를 백엔드로 그대로 forward하며 토큰을 URL/응답 헤더로 노출하지 않는다.
- `useChatStream` 훅이 이벤트 타입별로 상태를 분기한다.
  - `agent_start`, `tool_call`: 진행 상태 UI(`agent-trace.tsx`)
  - `token`: 본문에 누적
  - `done`: 메시지 확정, 카드/링크 렌더링

### 3. 대화 이력

- `GET /chat/rooms`, `GET /chat/rooms/{id}/messages` 는 TanStack Query로 캐싱.
- 새 대화는 첫 응답 완료 후 백엔드가 제목을 채운다. 그 전까지는 "새 대화"로 표기.

---

## SSE 이벤트 타입 (초안)

```ts
type SseEvent =
  | { type: 'agent_start'; agent: 'router' | 'sql' | 'vector' | 'answer' }
  | { type: 'tool_call'; tool: 'sql_search' | 'vector_search' | 'map_search'; args: unknown }
  | { type: 'tool_result'; tool: string; ok: boolean }
  | { type: 'token'; delta: string }
  | { type: 'done'; messageId: number }
  | { type: 'error'; message: string };
```

> 실제 스키마는 백엔드 `ai-service/schemas/events.py`를 정본으로 한다. 이 타입 파일은 그 미러로 유지한다.

---

## 환경 변수

```
NEXT_PUBLIC_API_BASE_URL=     # API 서비스 (Spring Boot)
API_BASE_URL=                 # 서버 컴포넌트/Route Handler 전용 (내부망 가능)
NEXT_PUBLIC_OAUTH_LOGIN_URL=  # /oauth2/authorization/google
```

---

## 개발

```bash
pnpm install
pnpm dev          # http://localhost:3000
pnpm lint
pnpm typecheck
pnpm test         # Vitest — api-client, useChatStream 코어만
pnpm build
```

> **테스트 범위**: 컴포넌트·페이지는 테스트 대상에서 제외한다. `lib/api-client.ts`(single-flight, 401 refresh 인터셉터)와 `hooks/useChatStream.ts`(이벤트 디스패치, 토큰 누적, 취소) 두 파일만 Vitest로 관리한다. Vitest는 Epic 2 Phase 4(`api-client` 구현) 시점에 도입한다.

---

## 배포

| 항목 | 값 |
|---|---|
| 플랫폼 | Vercel |
| 서비스 URL | `https://on-seoul.jazzz.dev` |
| Production 브랜치 | `main` |
| Preview 브랜치 | `on-seoul-front` |
| DNS | Cloudflare → `on-seoul` CNAME → `cname.vercel-dns.com` |

상세 설정은 [`docs/deployment-strategy.md`](./docs/deployment-strategy.md) 참조.

---

## 바이브코딩 규약

`CLAUDE.md`에 다음을 고정해두고 작업한다.

- SSE 이벤트 타입 정의는 `types/sse-events.ts`를 단일 소스로 사용
- API 엔드포인트는 `lib/api-client.ts`를 통해서만 호출 (`credentials: 'include'` 필수)
- UI 컴포넌트는 shadcn/ui를 우선 사용, 신규 추가 시 `components/ui/`에 생성
- 인증 토큰은 httpOnly 쿠키가 단일 진실. `localStorage` / `sessionStorage` / `document.cookie` 직접 접근 금지

`useChatStream` 훅과 SSE 이벤트 타입만 사람이 직접 잡아두고, 이후 화면 단위는 바이브코딩으로 진행한다.
