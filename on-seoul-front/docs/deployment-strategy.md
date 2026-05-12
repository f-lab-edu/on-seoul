# 배포 전략 — on-seoul-front

## 1. Vercel 개요

### 역할

Vercel은 Next.js 공식 호스팅 플랫폼으로, 프론트엔드 빌드·배포·서빙을 통합 제공한다.  
`git push` 한 번으로 빌드·배포·CDN 배포까지 자동화된다.

### 주요 제공 기능

| 기능 | 설명 |
|---|---|
| **엣지 네트워크 (CDN)** | 전 세계 엣지 노드에서 정적 자산 서빙, 낮은 레이턴시 |
| **자동 HTTPS** | TLS 인증서 자동 발급·갱신 (Let's Encrypt) |
| **리버스 프록시** | Next.js Route Handler, API 라우팅 내장 처리 |
| **미리보기 배포** | PR·브랜치마다 독립적인 Preview URL 자동 생성 |
| **환경변수 관리** | Production / Preview / Development 환경별 분리 |
| **서버리스 함수** | Next.js 서버 컴포넌트·Route Handler를 서버리스로 실행 |

### Nginx가 필요 없어진 이유

기존 셀프 호스팅 환경에서 Nginx는 아래 역할을 담당했다.

- TLS 종료 (HTTPS)
- 정적 파일 서빙
- 리버스 프록시 (Next.js → upstream)
- Gzip 압축

Vercel은 이 모든 역할을 엣지 인프라 수준에서 처리한다.  
별도의 Nginx 설정·유지보수 없이 동일한 기능을 더 넓은 엣지 네트워크로 제공하므로 Nginx 레이어가 불필요하다.

---

## 2. 브랜치 배포 전략

| 브랜치 | 배포 환경 | URL |
|---|---|---|
| `main` | **Production** | `https://on-seoul.jazzz.dev` |
| `on-seoul-front` | **Preview** | `https://on-seoul-front-*.vercel.app` (자동 생성) |

- `main` 브랜치에 push 또는 merge 될 때마다 Production 자동 배포
- `on-seoul-front` 브랜치는 개발 중 Preview로 확인, 검증 완료 후 `main`으로 merge

---

## 3. Vercel 프로젝트 설정

### 3-1. 프로젝트 생성

1. [vercel.com/new](https://vercel.com/new)에서 GitHub 레포지토리 import
2. Framework Preset: **Next.js** 자동 감지 확인
3. Root Directory: `on-seoul-front` (모노레포 구조)

### 3-2. 환경변수 등록

**Settings → Environment Variables**

| 변수 | 환경 | 설명 |
|---|---|---|
| `NEXT_PUBLIC_API_BASE_URL` | Production, Preview | 브라우저 → 백엔드 API URL (번들에 포함) |
| `API_BASE_URL` | Production, Preview | Route Handler → 백엔드 내부 URL (서버 전용) |
| `NEXT_PUBLIC_OAUTH_LOGIN_URL` | Production, Preview | Google OAuth 진입 URL |

> `NEXT_PUBLIC_*` 변수는 브라우저 번들에 포함된다. 시크릿 값은 절대 `NEXT_PUBLIC_` 접두사로 등록하지 않는다.

---

## 4. Cloudflare DNS 라우팅

서비스 도메인 `on-seoul.jazzz.dev`는 Cloudflare에서 DNS를 관리한다.

### DNS 레코드 구성

| 타입 | 이름 | 값 | Proxy 상태 |
|---|---|---|---|
| `CNAME` | `on-seoul` | `cname.vercel-dns.com` | DNS only (회색 구름) |

> Vercel 커스텀 도메인은 Cloudflare Proxy(주황 구름) 대신 **DNS only** 모드를 권장한다.  
> Proxy를 활성화하면 Vercel의 TLS 인증서 검증과 충돌할 수 있다.

### 설정 순서

1. Vercel 대시보드 → **Settings → Domains** → `on-seoul.jazzz.dev` 추가
2. Vercel이 안내하는 CNAME 값을 Cloudflare DNS에 등록
3. Vercel이 TLS 인증서 자동 발급 (수 분 소요)
4. `https://on-seoul.jazzz.dev` 접속 확인

### 서비스 URL

```
https://on-seoul.jazzz.dev
```

---

## 5. 쿠키 동작 확인

### 조건

| 조건 | 담당 | 상태 |
|---|---|---|
| `credentials: "include"` | 프론트엔드 `api-client.ts`, `useChatStream.ts` | ✅ 적용 완료 |
| CORS `allowCredentials(true)` | 백엔드 `SecurityConfig` | ✅ 적용 완료 |
| `allowedOrigins`에 프론트 도메인 명시 | 백엔드 | `on-seoul.jazzz.dev` 추가 필요 |

> 와일드카드(`*`)는 `credentials`와 함께 사용할 수 없다. 백엔드 `allowedOrigins`에 `https://on-seoul.jazzz.dev`를 명시해야 한다.

### SameSite 구성

Production 환경:

| 구성 | eTLD+1 | 쿠키 전송 |
|---|---|---|
| `on-seoul.jazzz.dev` (프론트) ↔ `api.jazzz.dev` (백엔드 예시) | 둘 다 `jazzz.dev` | ✅ same-site |

프론트(`on-seoul.jazzz.dev`)와 백엔드가 동일한 apex 도메인을 공유하면 `SameSite=Strict`/`Lax` 모두 차단 없이 동작한다.  
백엔드가 다른 eTLD+1을 사용하면 `SameSite=None; Secure` + CORS 재협의 필요.

---

## 6. 배포 후 체크리스트

- [ ] `/login` 진입 → Google OAuth 리다이렉트 정상 동작
- [ ] OAuth 콜백 후 `/` 도착 시 URL·스토리지에 토큰 미노출 확인
- [ ] 챗봇 질의 → SSE 스트리밍 정상 수신
- [ ] Access 만료 → 자동 refresh → UX 끊김 없음
- [ ] `pnpm build` 로컬 빌드 오류 없음
- [ ] Cloudflare DNS 전파 확인 (`dig on-seoul.jazzz.dev`)
- [ ] HTTPS 인증서 정상 발급 확인
- [ ] 백엔드 `allowedOrigins`에 `https://on-seoul.jazzz.dev` 추가 확인
