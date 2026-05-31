# 카카오 OAuth 로그인 연동 가이드

이 문서는 기존 Google OAuth 로그인에 **카카오 로그인**을 추가하는 방법을
설명합니다. 카카오 인증은 백엔드(`on-seoul-api`)에 이미 완전히 구성되어
있으므로, 프론트엔드는 카카오 진입 버튼을 추가하고 환경변수를 정리하는 작업만
하면 됩니다.

결론부터 말하면 다음 세 가지만 처리하면 카카오 로그인이 동작합니다.

1. 카카오 개발자 콘솔에서 앱을 등록하고 백엔드 환경변수(`KAKAO_CLIENT_ID`,
   `KAKAO_CLIENT_SECRET`)를 채운다.
2. 프론트엔드 OAuth 진입 URL을 프로바이더별로 구성한다.
3. 로그인 페이지에 카카오 버튼을 추가한다.

백엔드 코드 변경은 필요하지 않습니다.

---

## 현재 상태

카카오 로그인에 필요한 백엔드와 프론트엔드의 현황은 다음과 같습니다.

### 백엔드 지원 현황 (변경 불필요)

| 항목 | 상태 | 위치 |
| --- | --- | --- |
| 카카오 `registration` / `provider` 설정 | 완료 | `bootstrap/.../application.yml` |
| 인증 진입 엔드포인트 `permitAll` | 완료 | `SecurityConfig` `/oauth2/authorization/**` |
| 콜백 엔드포인트 `permitAll` | 완료 | `SecurityConfig` `/login/oauth2/code/**` |
| 카카오 사용자 속성 파싱 | 완료 | `OAuth2LoginSuccessHandler` (`kakao_account`, `properties`, `id`) |
| 토큰 발급 + 쿠키 + 프론트 리다이렉트 | 완료 | `OAuth2LoginSuccessHandler` |

백엔드는 `provider`가 `google`이든 `kakao`든 동일한 성공/실패 흐름을 따릅니다.
유일하게 비어 있는 부분은 환경변수 `KAKAO_CLIENT_ID`, `KAKAO_CLIENT_SECRET`
입니다.

### 프론트엔드 현황 (작업 대상)

현재 `app/(auth)/login/page.tsx`는 단일 환경변수
`NEXT_PUBLIC_OAUTH_LOGIN_URL`로 Google 버튼 하나만 노출합니다. 카카오를
추가하려면 진입 URL을 프로바이더별로 구성해야 합니다.

콜백 페이지 `app/(auth)/oauth/callback/page.tsx`는 프로바이더와 무관하게
`status` / `error` 쿼리 파라미터만 처리하므로 **변경할 필요가 없습니다**.

---

## 1단계: 카카오 개발자 콘솔 설정

카카오 로그인을 사용하려면 카카오 개발자 콘솔에서 애플리케이션을 등록해야
합니다. 백엔드 설정은 `client-authentication-method: client_secret_post`를
사용하므로 **Client Secret 발급이 필수**입니다.

다음 순서로 진행합니다.

1. [카카오 개발자 콘솔](https://developers.kakao.com)에 로그인한 후 **내
   애플리케이션**에서 새 앱을 생성합니다.
2. **앱 설정 > 앱 키**에서 **REST API 키**를 복사합니다. 이 값이 백엔드의
   `KAKAO_CLIENT_ID`입니다.
3. **제품 설정 > 카카오 로그인**에서 **활성화 설정**을 **ON**으로 변경합니다.
4. **제품 설정 > 카카오 로그인 > 보안**에서 **Client Secret**을 생성하고
   **활성화**합니다. 이 값이 백엔드의 `KAKAO_CLIENT_SECRET`입니다.
5. **제품 설정 > 카카오 로그인**의 **Redirect URI**에 백엔드 콜백 주소를
   등록합니다. 형식은 `{API_BASE_URL}/login/oauth2/code/kakao`입니다.
   - 운영: `https://api.jazzz.dev/login/oauth2/code/kakao`
   - 로컬: `http://localhost:8080/login/oauth2/code/kakao`
6. **제품 설정 > 카카오 로그인 > 동의항목**에서 다음 항목을 설정합니다.
   - **닉네임**(`profile_nickname`): 필수 동의
   - **카카오계정(이메일)**(`account_email`): 선택 또는 필수 동의
7. **앱 설정 > 플랫폼**에서 **Web 플랫폼**을 추가하고 프론트엔드 사이트
   도메인(예: `https://jazzz.dev`, `http://localhost:3000`)을 등록합니다.

> **Warning:** 카카오의 이메일(`account_email`) 동의항목은 앱 검수를 받지
> 않으면 **앱 관리자 본인 계정에서만** 제공됩니다. 검수 전에는 일반 사용자의
> 이메일이 `null`로 들어올 수 있으므로, 백엔드가 이메일 `null`을 허용하는지
> 확인하세요. 닉네임은 검수 없이도 제공됩니다.

### 백엔드 환경변수 설정

콘솔에서 발급받은 값을 백엔드 환경변수에 등록합니다. 배포 서버에서는
`.env.api`(또는 해당 환경의 시크릿 저장소)에 추가합니다.

```bash
KAKAO_CLIENT_ID=<카카오 REST API 키>
KAKAO_CLIENT_SECRET=<카카오 Client Secret>
```

---

## 2단계: 백엔드 인증 흐름 (참고)

프론트엔드 구현을 이해하는 데 필요한 백엔드 동작은 다음과 같습니다. 이 흐름은
Google과 카카오가 동일하며, 프로바이더 이름만 다릅니다.

1. 사용자가 프론트의 카카오 버튼을 클릭하면 브라우저가 백엔드의 인증 진입
   URL `{API_BASE_URL}/oauth2/authorization/kakao`로 이동합니다.
2. 백엔드가 카카오 인증 페이지로 리다이렉트하고, 사용자는 카카오에서
   로그인·동의합니다.
3. 카카오가 백엔드 콜백 `{API_BASE_URL}/login/oauth2/code/kakao`로 인가
   코드를 전달합니다.
4. 백엔드가 토큰을 교환하고 사용자 정보를 조회한 뒤, 회원을 생성하거나
   조회하고 JWT를 발급합니다.
5. 백엔드가 `access_token`과 `refresh_token`을 **httpOnly 쿠키**로 심고
   프론트엔드 콜백으로 리다이렉트합니다.

콜백 결과에 따른 리다이렉트 주소는 다음과 같습니다.

| 결과 | 리다이렉트 URL | 의미 |
| --- | --- | --- |
| 성공 | `{FRONTEND_BASE_URL}/oauth/callback?status=success` | 로그인 완료, 쿠키 발급됨 |
| 정지/탈퇴 계정 | `{FRONTEND_BASE_URL}/oauth/callback?error=forbidden` | 사용할 수 없는 계정 |
| 서버 오류 | `{FRONTEND_BASE_URL}/oauth/callback?error=server_error` | 내부 오류(Redis 장애 등) |
| 인증 실패 | `{FRONTEND_BASE_URL}/oauth/callback?error=auth_failed` | OAuth 인증 자체 실패 |

프론트엔드 콜백 페이지는 이 파라미터만 보고 라우팅하면 되며, 토큰은 쿠키로만
전달되므로 프론트는 토큰을 직접 다루지 않습니다.

---

## 3단계: 프론트엔드 구현

진입 URL을 프로바이더별로 구성하고 로그인 페이지에 카카오 버튼을 추가합니다.

### 환경변수 구성

현재는 `NEXT_PUBLIC_OAUTH_LOGIN_URL` 하나로 Google만 가리킵니다. 프로바이더가
둘 이상이므로 **공통 진입 베이스 URL** 방식으로 정리하는 것을 권장합니다.
백엔드 인증 진입 경로가 `{API_BASE_URL}/oauth2/authorization/{provider}`로
일정하기 때문에, 베이스 URL 하나에 프로바이더만 덧붙이면 됩니다.

`.env.example`을 다음과 같이 수정합니다.

```bash
# OAuth 진입 베이스 URL (프로바이더 경로 제외)
# 예: https://api.jazzz.dev/oauth2/authorization
NEXT_PUBLIC_OAUTH_BASE_URL=
```

> **Note:** 기존 `NEXT_PUBLIC_OAUTH_LOGIN_URL`(Google 전체 URL)을 그대로
> 유지하고 카카오용 변수만 따로 추가할 수도 있습니다. 다만 프로바이더가 늘어날
> 때마다 변수가 늘어나므로, 베이스 URL 방식이 확장에 유리합니다. 배포 환경의
> Vercel 환경변수도 함께 갱신해야 합니다.

### 프로바이더 정의

로그인 페이지에서 반복을 줄이기 위해 프로바이더 목록을 배열로 정의합니다.
다음 예시처럼 표시 이름과 진입 경로를 함께 둡니다.

```tsx
const OAUTH_BASE_URL = process.env.NEXT_PUBLIC_OAUTH_BASE_URL;

const PROVIDERS = [
  { id: "google", label: "Google 계정으로 계속하기" },
  { id: "kakao", label: "카카오로 계속하기" },
] as const;
```

### 로그인 페이지 수정

`app/(auth)/login/page.tsx`에서 프로바이더 목록을 순회하며 버튼을 렌더링합니다.
외부 오리진으로 이동하므로 Next.js `<Link>` 대신 `<a>`를 사용합니다. 베이스
URL이 비어 있으면 버튼을 비활성화하고 안내 메시지를 노출하는 기존 패턴을
유지합니다.

```tsx
{PROVIDERS.map((provider) =>
  OAUTH_BASE_URL ? (
    <a
      key={provider.id}
      href={`${OAUTH_BASE_URL}/${provider.id}`}
      className={buttonVariants({ size: "lg", className: "w-full" })}
    >
      {provider.label}
    </a>
  ) : (
    <button
      key={provider.id}
      type="button"
      disabled
      aria-disabled="true"
      className={cn(
        buttonVariants({ size: "lg", className: "w-full" }),
        "pointer-events-none opacity-50",
      )}
    >
      {provider.label}
    </button>
  ),
)}
```

기존 `CardDescription`의 "Google 계정으로 로그인하고..." 문구도 프로바이더
중립적인 표현(예: "소셜 계정으로 로그인하고...")으로 다듬는 것을 권장합니다.

> **Note:** 카카오 버튼 색상·로고는 [카카오 로그인 디자인
> 가이드](https://developers.kakao.com/docs/latest/ko/kakaologin/design-guide)를
> 따라야 합니다. `components/ui`의 shadcn 버튼은 보존하고, 카카오 브랜드
> 스타일은 wrapper 컴포넌트나 `className` 확장으로 적용하세요.

### 콜백 페이지

`app/(auth)/oauth/callback/page.tsx`는 변경하지 않습니다. 이 페이지는
프로바이더와 무관하게 `status=success`와 `error=*`만 처리하므로 카카오
로그인에도 그대로 동작합니다.

---

## 로컬 개발과 운영 환경 차이

쿠키 기반 인증이므로 프론트엔드와 백엔드의 도메인 구성에 따라 쿠키 설정이
달라집니다. 다음 표를 참고하세요.

| 환경 | 쿠키 설정(백엔드) | 비고 |
| --- | --- | --- |
| 로컬 (HTTP) | `COOKIE_SECURE=false`, `COOKIE_SAME_SITE=Lax` 또는 `None` | `localhost`에서 동작 확인 |
| 운영 (HTTPS, 동일 상위 도메인) | `COOKIE_SECURE=true`, `COOKIE_SAME_SITE=Strict`, `COOKIE_DOMAIN=.jazzz.dev` | 서브도메인 간 쿠키 공유 |
| 운영 (교차 도메인) | `COOKIE_SAME_SITE=None; Secure` + CORS `allowCredentials` | 프론트·백엔드 도메인이 다를 때 |

카카오 콘솔의 **Redirect URI**와 **사이트 도메인**은 위 환경별 실제 주소와
일치해야 합니다. 등록되지 않은 주소로 콜백되면 카카오가 인증을 거부합니다.

---

## 검증 시나리오

연동 후 다음 시나리오를 수동으로 확인합니다.

1. `/login` 진입 → **카카오로 계속하기** 버튼이 노출된다.
2. 버튼 클릭 → 카카오 인증 페이지로 이동 → 로그인·동의를 완료한다.
3. `/oauth/callback?status=success`를 거쳐 `/`로 이동한다.
4. 로그인 후 `access_token` / `refresh_token` 쿠키가 발급되고, 토큰이 URL,
   `localStorage`, 응답 본문에 노출되지 않는다.
5. 정지된 계정으로 로그인하면 `?error=forbidden`으로 `/login`에 돌아오고
   안내 메시지가 보인다.
6. Google 로그인도 기존과 동일하게 동작한다(회귀 없음).

---

## 주의사항

연동 시 자주 발생하는 문제와 점검 항목은 다음과 같습니다.

- **Client Secret 누락**: 백엔드가 `client_secret_post`를 사용하므로 카카오
  콘솔에서 Client Secret을 **활성화**하지 않으면 토큰 교환이 실패합니다.
- **Redirect URI 불일치**: 콘솔에 등록한 Redirect URI와 백엔드가 사용하는
  `{API_BASE_URL}/login/oauth2/code/kakao`가 정확히 일치해야 합니다.
- **이메일 `null`**: 앱 검수 전에는 일반 사용자 이메일이 제공되지 않을 수
  있습니다. 백엔드 회원 생성 로직이 이메일 `null`을 허용하는지 확인하세요.
- **사용자 식별자**: 카카오는 `sub`가 없고 `id`를 식별자로 사용합니다. 백엔드
  `OAuth2LoginSuccessHandler`가 이미 `id`를 읽도록 처리되어 있으므로 프론트는
  신경 쓸 필요가 없습니다.

---

## 다음 단계

- 프론트엔드 구현 진행 상황은
  [`front-implementation.md`](./front-implementation.md)의 Epic 2(`feat/auth`)에
  카카오 항목을 추가해 추적하세요.
- 백엔드 인증 시퀀스 상세는 `docs/schemas/auth-design.md`를 참고하세요.
