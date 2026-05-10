# Vercel 배포 가이드

이 문서는 `on-seoul-front`를 Vercel에 배포하는 절차를 설명한다.

---

## 사전 준비

- Vercel 계정 (vercel.com)
- GitHub에 레포지토리가 push된 상태
- 백엔드 서버 주소 확인 (`API_BASE_URL`)
- Google OAuth 진입 URL 확인 (`NEXT_PUBLIC_OAUTH_LOGIN_URL`)

---

## 1. Vercel 프로젝트 생성

1. [vercel.com/new](https://vercel.com/new)에서 GitHub 레포지토리를 import한다.
2. Framework Preset이 **Next.js**로 자동 감지되는지 확인한다.
3. Root Directory는 `on-seoul-front`로 설정한다 (모노레포 구조인 경우).

---

## 2. 환경변수 등록

**Settings → Environment Variables**에서 아래 변수를 등록한다.

| 변수 | 환경 | 설명 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Production, Preview | 브라우저에서 백엔드를 호출할 때 사용하는 공개 URL |
| `API_BASE_URL` | Production, Preview | Route Handler에서 백엔드를 호출할 때 사용하는 내부 URL (외부 노출 불가) |
| `NEXT_PUBLIC_OAUTH_LOGIN_URL` | Production, Preview | Google OAuth 진입 URL |

> **참고:** `NEXT_PUBLIC_*` 변수는 브라우저 번들에 포함된다. 비밀 값(시크릿 키 등)은 절대 `NEXT_PUBLIC_` 접두사로 등록하지 않는다.

---

## 3. 배포

환경변수 등록 후 **Deploy** 버튼을 누른다. 이후 `main` 브랜치에 push할 때마다 자동으로 Production 배포된다.

PR을 열면 Preview URL이 자동 생성된다.

---

## 4. 쿠키 동작 확인

쿠키가 정상 전송되려면 두 가지 조건이 모두 충족돼야 한다.

### 조건 1 — `credentials: "include"` (프론트엔드)

브라우저의 cross-origin fetch는 기본적으로 쿠키를 포함하지 않는다.
`api-client.ts`와 `useChatStream.ts`는 이미 `credentials: "include"`를 적용하고 있으므로 별도 수정 불필요.

### 조건 2 — CORS `allowCredentials(true)` (백엔드)

백엔드 `SecurityConfig`에 이미 설정돼 있으므로 별도 수정 불필요.
단, `allowedOrigins`에 프론트 도메인이 명시돼 있어야 한다. 와일드카드(`*`)는 credentials와 함께 사용할 수 없다.

### SameSite=Strict와 서브도메인

SameSite의 "same-site" 기준은 eTLD+1(등록 가능한 최상위 도메인+1)이다.
**서브도메인이 달라도 apex가 같으면 same-site**이므로 SameSite=Strict가 차단하지 않는다.

| 구성 | eTLD+1 | 쿠키 전송 여부 |
|---|---|---|
| `on-seoul.jazzz.dev` ↔ `api.jazzz.dev` | 둘 다 `jazzz.dev` | ✅ same-site, 차단 없음 |
| `on-seoul.vercel.app` ↔ `api.jazzz.dev` | 서로 다름 | ❌ cross-site → 쿠키 차단 |

cross-site 구성이 불가피하면 백엔드 측에서 `SameSite=None; Secure`로 변경하고 CORS `allowedOrigins`·`allowCredentials`를 재협의한다.

---

## 5. 배포 후 체크리스트

- [x] `/login` 진입 → Google OAuth 리다이렉트 정상 동작
- [x] OAuth 콜백 후 `/` 도착 시 URL·스토리지에 토큰 미노출 확인
- [x] 챗봇 질의 → SSE 스트리밍 정상 수신
- [x] Access 만료 → 자동 refresh → UX 끊김 없음
- [x] `pnpm build` 로컬 빌드 오류 없음 (`next build --turbopack`)
