# 프론트 작업 가이드: OAuth 이메일 충돌 처리

이 문서는 같은 이메일을 다른 소셜 로그인 벤더로 다시 로그인할 때 발생하는
**이메일 충돌**을 프론트엔드에서 처리하는 방법을 설명합니다. 백엔드 정책과
쿼리 파라미터 계약은 이미 확정됐고, 프론트는 콜백 페이지와 로그인 페이지에서
안내 처리를 추가하면 됩니다.

이 작업은 백엔드 변경(정책 B, "기존 벤더 안내")과 짝을 이룹니다. 백엔드는
충돌을 감지하면 프론트 콜백으로 `error=email_conflict`를 리다이렉트하며,
프론트는 이 신호를 받아 사용자에게 원래 가입한 벤더로 로그인하도록
안내합니다.

---

## 배경

온서울은 **이메일 1개당 계정 1개**(글로벌 유니크) 정책을 사용합니다. 사용자가
Google로 가입한 이메일과 동일한 이메일을 Kakao로 로그인하면, 백엔드는 이를 새
계정 생성으로 처리하려다 유니크 제약에 막힙니다.

이때 계정을 자동으로 연동하지 않고 **기존 벤더로 로그인하도록 안내**하는
이유는 보안입니다. 이메일만으로 자동 연동하면, 미검증 이메일을 가진 벤더를
통해 타인의 계정에 접근하는 계정 탈취 위험이 생깁니다. 카카오 이메일은 검수
전 미검증이거나 비어 있을 수 있으므로 자동 연동 대상으로 신뢰할 수 없습니다.

수정 전에는 이 충돌이 서버 오류(`error=server_error`)로 처리되어 사용자에게
원인이 전달되지 않았습니다. 이 작업으로 명확한 안내 메시지를 제공합니다.

---

## 백엔드 계약 (확정)

백엔드는 OAuth 콜백 처리 결과를 프론트 콜백 URL
`{FRONTEND_BASE_URL}/oauth/callback`의 쿼리 파라미터로 전달합니다. 이번 작업과
관련된 파라미터는 다음과 같습니다.

| 파라미터 | 값 | 의미 |
| --- | --- | --- |
| `error` | `email_conflict` | 같은 이메일이 다른 벤더로 이미 가입됨. 원래 벤더로 로그인하도록 안내한다. |
| `provider` | `google` \| `kakao` | (`email_conflict`일 때만 함께 전달) 이메일이 처음 가입된 벤더. **드물게 동시 가입 경합 시 누락될 수 있으므로** 없는 경우도 처리해야 한다. |

기존 파라미터(변경 없음)는 다음과 같습니다.

| 파라미터 | 값 | 의미 |
| --- | --- | --- |
| `status` | `success` | 로그인 성공. 토큰은 httpOnly 쿠키로 발급됨. |
| `error` | `forbidden` | 정지/탈퇴 계정. |
| `error` | `server_error` | 서버 내부 오류. |

충돌 리다이렉트 예시: `{FRONTEND_BASE_URL}/oauth/callback?error=email_conflict&provider=google`

---

## 작업 항목

콜백 페이지에서 충돌 신호를 로그인 페이지로 전달하고, 로그인 페이지에서 안내
메시지를 노출합니다.

### 1. 콜백 페이지 — 충돌 신호 전달

파일: `app/(auth)/oauth/callback/page.tsx`

현재 콜백 페이지는 `status=success`와 `error=forbidden`만 처리하고, 그 외는
`/login`으로 회귀합니다. `error=email_conflict`를 추가로 처리하고 `provider`
값을 로그인 페이지로 전달합니다. 토큰은 백엔드가 쿠키로 처리하므로 이 페이지는
라우팅만 책임집니다.

`OAuthCallbackInner`의 `useEffect` 분기에 다음을 추가합니다.

```tsx
if (error === "email_conflict") {
  const provider = params.get("provider");
  const query = provider
    ? `?error=email_conflict&provider=${provider}`
    : "?error=email_conflict";
  router.replace(`/login${query}`);
  return;
}
```

이 분기는 기존 `error === "forbidden"` 분기와 같은 위치에 둡니다.

### 2. 로그인 페이지 — 안내 메시지 노출

파일: `app/(auth)/login/page.tsx`

로그인 페이지는 서버 컴포넌트로 `searchParams`를 읽습니다. `error` 타입에
`email_conflict`를 더하고 `provider`를 함께 읽어, 사용자가 어떤 벤더로
로그인해야 하는지 안내합니다.

먼저 `searchParams` 타입과 구조 분해에 `provider`를 추가합니다.

```tsx
interface LoginPageProps {
  searchParams: Promise<{ error?: string; provider?: string }>;
}

// 컴포넌트 본문
const { error, provider } = await searchParams;
```

벤더 식별자를 사용자에게 보여줄 이름으로 변환하는 매핑을 정의합니다.

```tsx
const PROVIDER_LABELS: Record<string, string> = {
  google: "Google",
  kakao: "카카오",
};

const conflictMessage =
  provider && PROVIDER_LABELS[provider]
    ? `이미 ${PROVIDER_LABELS[provider]} 계정으로 가입된 이메일입니다. ${PROVIDER_LABELS[provider]}(으)로 로그인해주세요.`
    : "이미 다른 방식으로 가입된 이메일입니다. 기존에 사용하신 로그인 수단으로 다시 시도해주세요.";
```

기존 `error === "forbidden"` 안내 블록과 같은 형식으로 충돌 안내 블록을
추가합니다.

```tsx
{error === "email_conflict" && (
  <p
    role="alert"
    className="rounded-md bg-destructive/10 px-3 py-2 text-sm text-destructive"
  >
    {conflictMessage}
  </p>
)}
```

> **Note:** `provider`가 누락된 경우(동시 가입 경합)에도 사용자가 막히지 않도록
> 일반 안내 문구를 보여줍니다. 특정 벤더 버튼만 강조하는 동작은 `provider`가
> 있을 때만 적용하세요.

### 3. (선택) 충돌 벤더 버튼 강조

`provider`가 전달된 경우, 해당 벤더 로그인 버튼을 시각적으로 강조해 사용자가
바로 올바른 버튼을 누르도록 유도할 수 있습니다. 이는 UX 개선용 선택 작업이며,
1·2번 처리만으로도 기능은 완결됩니다.

---

## 검증 시나리오

다음 시나리오를 수동으로 확인합니다.

1. Google로 이메일 X에 가입한다.
2. 로그아웃 후 같은 이메일 X를 Kakao로 로그인한다.
3. 백엔드가 `/oauth/callback?error=email_conflict&provider=google`로
   리다이렉트한다.
4. 콜백 페이지가 `/login?error=email_conflict&provider=google`로 회귀한다.
5. 로그인 페이지에 "이미 Google 계정으로 가입된 이메일입니다..." 안내가
   노출된다.
6. `provider`를 임의로 제거한 URL(`/login?error=email_conflict`)에서도 일반
   안내 문구가 정상 노출된다(누락 처리 확인).
7. 정상 로그인(`status=success`)과 정지 계정(`error=forbidden`) 흐름은 기존과
   동일하게 동작한다(회귀 없음).

---

## 참고

- 인증 전반의 흐름과 쿠키 정책은 인증 위키 및
  [`front-implementation.md`](./front-implementation.md)의 Epic 2(`feat/auth`)를
  참고하세요.
- 토큰은 백엔드가 httpOnly 쿠키로 발급하므로, 이 작업에서도 프론트는 토큰을
  직접 읽거나 저장하지 않습니다.
