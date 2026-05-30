# 인증

이 서비스의 핵심은 멀티에이전트 오케스트레이션과 공공데이터 분석이다. 인증은
그 핵심을 안전하게 보호하는 기반 레이어로, 보안 원칙을 지키면서도 구현 비용을
핵심 기능 개발에 집중할 수 있도록 설계했다. 자체 인증(회원가입, 비밀번호 해싱,
이메일 인증 등)은 별도 서비스 수준의 작업량을 요구하므로 검증된 외부 IdP에
위임하고, 내부는 JWT 기반으로 빠르고 명확하게 구현했다.

---

## 구조

```
사용자 → Google / Kakao OAuth 2.0 → API 서비스 (JWT 발급 및 검증)
```

1. 사용자가 소셜 로그인 요청
2. API 서비스가 Authorization Code Flow를 처리하고 사용자 정보를 수신
3. API 서비스가 내부 사용자를 식별(신규면 자동 가입)한 후 JWT를 발급
4. 이후 모든 요청은 JWT로 인증하며, API 서비스가 검증한다

---

## 인증 흐름

### 소셜 로그인

OAuth 2.0 Authorization Code Flow를 사용한다. 콜백은 브라우저의 full-page
navigation이므로 JSON body 대신 HttpOnly 쿠키로 토큰을 전달하고, 프론트엔드
콜백 페이지로 리다이렉트한다.

OAuth2 인가 요청의 `state` 값은 서버 세션이 아닌 **HttpOnly 쿠키**
(`oauth2_auth_request`)에 저장한다. 분산 환경에서 콜백이 다른 서버 인스턴스로
라우팅되더라도 state 검증이 정상 동작하며, 서버가 어떤 순간에도 상태를 갖지
않는 완전한 Stateless 구조를 달성한다.

```
GET /oauth2/authorization/{provider}
  → oauth2_auth_request 쿠키 발급 (HttpOnly, SameSite=Lax, 10분, HMAC-SHA256 서명)
  → 소셜 제공자 인증 완료
  → GET /login/oauth2/code/{provider}?code=...&state=...
      (브라우저가 oauth2_auth_request 쿠키를 자동 전송)
  → state 검증 통과 → oauth2_auth_request 쿠키 만료
  → OAuth2LoginSuccessHandler
      ├─ 제공자 속성 파싱 (Google: sub·email·name / Kakao: 중첩 속성)
      ├─ (provider, provider_id) 기준 사용자 조회
      │     신규 → users 자동 생성 (status=ACTIVE)
      │     기존 → email·nickname 업데이트
      ├─ 비활성화 계정 → {frontend}/oauth/callback?error=forbidden 리다이렉트
      ├─ Access Token (15분) + Refresh Token (7일) 발급
      ├─ Refresh Token → Redis 저장 (키: RT:{userId})
      ├─ 두 토큰을 HttpOnly 쿠키(access_token, refresh_token)로 설정
      └─ {frontend}/oauth/callback?status=success 리다이렉트
```

### 요청 인증

`JwtAuthenticationFilter`가 모든 요청에서 Access Token을 읽어 검증한다.
토큰 소스는 두 가지를 순서대로 시도한다.

1. `Authorization: Bearer <token>` 헤더 — API·모바일 클라이언트
2. `access_token` HttpOnly 쿠키 — 브라우저 SPA

유효한 토큰이면 `userId`를 `SecurityContext`에 설정하고, 유효하지 않거나 없으면
`401`을 반환한다. Access Token의 `type` 클레임이 `access`인지도 검증한다.

### 토큰 갱신

브라우저는 `refresh_token` 쿠키를 자동으로 전송하므로 요청 본문이 필요 없다.
`getAndDelete` 원자적 연산으로 조회와 삭제를 동시에 처리해 동시 요청 간
경합(TOCTOU)을 방지한다.

```
POST /auth/token/refresh
  (refresh_token 쿠키 자동 전송)
  → JWT type 클레임 검증 (refresh 타입이어야 함)
  → Redis에서 getAndDelete(RT:{userId})
  → 꺼낸 값 != 요청 토큰 → 401
  → 비활성화 계정 → 403
  → 새 Access Token + Refresh Token 발급
  → Redis에 새 Refresh Token 저장
  → 새 토큰을 Set-Cookie로 응답 + 204 No Content
```

동일한 Refresh Token으로 두 번 요청하면 두 번째는 Redis에 값이 없으므로 `401`을
반환한다. 탈취된 토큰이 이미 사용됐는지 감지할 수 있다.

### 로그아웃

```
POST /auth/logout
  (access_token 쿠키 또는 Authorization 헤더)
  → Redis에서 RT:{userId} 삭제
  → access_token · refresh_token 쿠키 만료(maxAge=0)
  → 204 No Content
```

Access Token은 만료를 기다린다(서버 상태 없음). Refresh Token을 Redis에서 삭제하면
재발급이 불가능해 사실상 세션이 종료된다. Access Token이 만료된 상태에서도 쿠키는
정리된다.

---

## JWT 설계

| 토큰 | 만료 | 저장 위치 | `type` 클레임 |
|---|---|---|---|
| Access Token | 15분 | HttpOnly 쿠키 또는 클라이언트 | `access` |
| Refresh Token | 7일 | Redis + HttpOnly 쿠키 | `refresh` |

- 서명 알고리즘: HS256
- `sub`: 내부 `users.id` (Long)
- `type` 클레임으로 두 토큰을 구조적으로 분리. Access Token을 Refresh 자리에
  사용하는 오용을 서버에서 거부한다.
- 시크릿 키는 환경 변수 `JWT_SECRET`으로만 관리한다.
- Redis TTL은 `jwt.refresh-token-minutes` 값에서 자동으로 파생된다(단일 소스).

### 쿠키 속성

| 쿠키 | `path` | `maxAge` |
|---|---|---|
| `access_token` | `/` | 15분 |
| `refresh_token` | `/auth` | 7일 |

두 쿠키 모두 `HttpOnly; Secure; SameSite=Strict`로 설정한다. `Secure` 속성은
환경 변수 `COOKIE_SECURE=false`로 로컬 HTTP 환경에서 비활성화할 수 있다.

OAuth2 state 쿠키(`oauth2_auth_request`)는 `SameSite=Lax`로 설정한다.
OAuth2 콜백은 Google/Kakao에서 오는 cross-site GET 리다이렉트이므로
`Strict`이면 쿠키가 전송되지 않아 인증이 실패한다.

---

## 공개 경로

| 경로 | 설명 |
|---|---|
| `/actuator/health` | 헬스체크 |
| `/auth/token/refresh` | 토큰 갱신 |
| `/oauth2/authorization/**` | OAuth2 로그인 시작 |
| `/login/oauth2/code/**` | OAuth2 콜백 |

---

## Stateless 달성

서버는 어떤 순간에도 요청 처리에 필요한 상태를 메모리나 세션에 보관하지 않는다.

| 상태 | 저장 위치 | 이유 |
|---|---|---|
| OAuth2 `state` (인가 요청) | 클라이언트 쿠키 (`oauth2_auth_request`) | 분산 환경에서 콜백이 다른 인스턴스로 라우팅돼도 검증 가능 |
| Access Token | 클라이언트 쿠키 (`access_token`) | 서버가 검증만 수행, 저장 불필요 |
| Refresh Token | Redis (userId 키) | 회전·무효화를 위해 서버 측 저장 필요 — 유일한 서버 상태 |

Redis의 Refresh Token은 상태이지만 세션이 아니다. 특정 서버 인스턴스에 종속되지
않으며 모든 인스턴스가 공유하는 중앙 저장소다. 서버를 아무리 늘려도 인증 흐름이
유지된다.

---

## 트레이드오프

| 항목 | 소셜 로그인 + JWT | 자체 인증 + JWT |
|---|---|---|
| 구현 비용 | 낮음 (Spring Security OAuth2 Client) | 높음 |
| 보안 책임 | 외부 IdP에 위임 | 직접 부담 |
| 사용자 경험 | 별도 가입 불필요 | 가입 절차 필요 |
